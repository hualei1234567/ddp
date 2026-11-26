"""评测失败案例回流用例（V52-030，蓝图 N5 / G5）。

## 数据飞轮入口

评测平台发现模型在哪类 capability 上失败 → 回调本端 → 判定（去重/限流）
→ 受理的案例落库为 ``RECEIVED`` → **可选**立即转成标注任务（``CONVERTED``）
→ 标注产出回到训练集。这是蓝图 G5 指出的"失败 → 标注 → 再训练"闭环入口。

## 为什么转换是可选的

建标注任务需要 ``sample_set_id`` / ``schema_id``——这两个是项目配置，
Webhook 调用方（评测平台）无从知晓，**平台不能替它编造**。因此：
- 请求带 ``conversion`` 块（目标样本集 + Schema）→ 同批受理的案例合并为
  **一个**回流任务（``code=BACKFLOW-<UTC 时间>-<短哈希>``），任务描述记录
  案例清单；
- 不带 → 案例停在 ``RECEIVED``，运营在工作台里按需转换（``convert`` 端点）。

## 幂等

三层：批次内去重（领域纯函数）→ 库内 ``known_refs`` 预查询 → 库层唯一键
``IntegrityError`` 翻译（``SqlBackflowRepository.add_accepted`` 的调用方在
本文件捕获并降级为跳过，绝不让回调因并发双写失败）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.application.annotation.commands import NewAnnotationTask
from app.application.annotation.ports import UnitOfWork
from app.application.annotation.tasks import AnnotationTaskUseCases
from app.domain.annotation.backflow import (
    DEFAULT_HOURLY_LIMIT,
    BackflowCase,
    BackflowDecisionBatch,
    classify_batch,
)
from app.domain.annotation.enums import TaskPriority
from app.domain.annotation.errors import DomainValidationError, DuplicateResourceError
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.annotation_backflow import SqlBackflowRepository


@dataclass(frozen=True, slots=True)
class BackflowConversionTarget:
    """受理后立即转任务的目标配置（项目侧的既有配置，非回调方提供）。"""

    sample_set_id: UUID
    schema_id: UUID


class BackflowUseCases:
    """回流受理与转换（写操作同事务提交）。"""

    def __init__(
        self,
        backflow: SqlBackflowRepository,
        tasks: AnnotationTaskUseCases,
        uow: UnitOfWork,
    ) -> None:
        self._backflow = backflow
        self._tasks = tasks
        self._uow = uow

    def ingest(
        self,
        scope: ProjectScope,
        cases: Sequence[Mapping[str, object]],
        *,
        limit: int = DEFAULT_HOURLY_LIMIT,
        conversion: BackflowConversionTarget | None = None,
        now: datetime | None = None,
    ) -> BackflowDecisionBatch:
        """受理一批回调案例：判定 → 落库 → （可选）转任务。"""
        moment = now or datetime.now(UTC)
        sources = tuple(sorted({str(raw.get("source") or "") for raw in cases} - {""}))
        batch, accepted = classify_batch(
            tuple(cases),
            self._backflow.known_refs(scope, sources),
            self._backflow.hourly_counts(scope, sources, now=moment),
            project_id=scope.project_id,
            received_at=moment,
            limit=limit,
        )
        # 只持久化**受理**的案例：跳过/限流的判决是瞬态结论，只回给调用方，
        # 不落库。原因——表上有 (source, case_ref) 唯一键，而"重复跳过"的案例
        # 恰恰与既有行同键，写行必然撞唯一约束（生产演练实测 409，见
        # MEM-INC-023）。限流同理：风暴期若逐条写拒绝行，反而制造写放大。
        self._backflow.add_accepted(scope, accepted)
        # 并发双写兜底：唯一键冲突整批拒绝（会话由依赖层回滚，无部分写入）。
        # 选择整批拒绝而非静默跳过：调用方需要知道"这批没有全部落库"才能重试；
        # 静默跳过会让评测平台误以为案例已受理（MEM-INC-001 的教训是别让异常
        # 冒泡成可重试失败，但更不能伪装成功）。
        try:
            self._uow.commit()
        except IntegrityError as exc:
            raise DuplicateResourceError(
                "回流批次存在并发重复案例（source+case_ref 已存在），整批未写入；请重试"
            ) from exc
        if conversion is not None and accepted:
            self._convert_accepted(scope, accepted, conversion, moment=moment)
        return batch

    def convert(
        self,
        scope: ProjectScope,
        case_ids: Sequence[UUID],
        target: BackflowConversionTarget,
        *,
        now: datetime | None = None,
    ) -> UUID:
        """把已受理案例转成一个标注任务并回填 ``task_id``。"""
        moment = now or datetime.now(UTC)
        rows = [
            case
            for case in self._backflow.list_cases(scope, limit=200)
            if case.case_id in set(case_ids)
        ]
        pending = tuple(case for case in rows if case.status.value == "RECEIVED")
        if not pending:
            raise DomainValidationError("没有处于 RECEIVED 状态的案例可转换")
        task_id = self._convert_accepted(scope, pending, target, moment=moment)
        if task_id is None:  # pragma: no cover - _convert_accepted 有案例时必返回
            raise DomainValidationError("转化失败：未创建任务")
        return task_id

    def _convert_accepted(
        self,
        scope: ProjectScope,
        accepted: Sequence[BackflowCase],
        target: BackflowConversionTarget,
        *,
        moment: datetime,
    ) -> UUID | None:
        """受理案例合并为一个回流任务（同一批次一份任务，避免任务碎片化）。"""
        if not accepted:
            return None
        digest = hashlib.sha256(
            "|".join(sorted(f"{c.source}:{c.case_ref}" for c in accepted)).encode()
        ).hexdigest()[:8]
        code = f"BACKFLOW-{moment.strftime('%Y%m%d%H%M%S')}-{digest}"
        priority = min((c.suggested_priority for c in accepted), key=_priority_rank)
        instruction = "；".join(f"{c.source}:{c.case_ref}({c.capability})" for c in accepted[:20])
        task = self._tasks.create(
            scope,
            NewAnnotationTask(
                code=code,
                name=f"评测回流 {len(accepted)} 例",
                sample_set_id=target.sample_set_id,
                schema_id=target.schema_id,
                target_item_count=len(accepted),
                target_episode_count=0,
                description=f"评测失败案例回流：{instruction}",
                priority=priority.value,
            ),
            path=f"/api/v1/projects/{scope.project_id}/annotation-backflow",
        )
        for case in accepted:
            self._backflow.mark_converted(scope, case.case_id, task.id)
        self._uow.commit()
        return task.id


_PRIORITY_ORDER = (
    TaskPriority.P0,
    TaskPriority.P1,
    TaskPriority.P2,
    TaskPriority.P3,
)


def _priority_rank(priority: TaskPriority) -> int:
    """排序键：P0 最高（rank 0）。"""
    return _PRIORITY_ORDER.index(priority) if priority in _PRIORITY_ORDER else 3


def new_backflow_case_id() -> UUID:
    """案例 ID 工厂（测试可替换；生产 uuid4）。"""
    return uuid4()
