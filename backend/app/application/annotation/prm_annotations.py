"""PRM 步级标注用例：登记、查询、分叉重写（预览 / 一步物化）（V52-020/V52-032）。

## 边界

- **登记**（``create``）：轨迹标注整体创建（含部分标注），创建即不可变——
  步级标注是复核对象而非编辑对象；修正走新轨迹行，不原地覆盖。
  调用方必须是项目写权限持有者（``WriteScope`` 已在 API 层强制）。
- **查询**（``get`` / ``get_by_trajectory`` / ``list_for_project``）：只读，
  项目隔离由 ``ProjectScope`` 强制。
- **分叉重写**（``fork``）：``materialize=False`` 时是纯计算预览——给定点位与
  重写序列，返回分叉轨迹与 Step-DPO 对，不落库（先例：负载预览同为只读
  计算端点）；``materialize=True`` 时在同一次请求内**复用 ``create`` 路径**
  以新 trajectory_id 落库（``<原 id>#fork-<n>``，唯一冲突递增，最多
  ``FORK_MAX_ATTEMPTS`` 次），省掉"预览确认后再调创建"的两步。

偏好对以 JSON 约定值形态回流提交链（MEM-DEC-002），本用例不写
``annotation_records``，只产出对的数据结构与落库实体。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.ports import (
    AuditRepository,
    OutboxRepository,
    PrmAnnotationRepository,
    PrmAnnotationView,
    UnitOfWork,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
)
from app.domain.annotation.prm_dpo import StepDpoPair, to_step_dpo_pair
from app.domain.annotation.prm_step import (
    FORK_MAX_ATTEMPTS,
    ForkedTrajectory,
    PrmAnnotation,
    fork_from_step,
    forked_trajectory_id,
)
from app.domain.identity.models import ProjectScope

# 事件是留痕类（dispatch=False）：没有后台动作消费它，去掉后系统行为不变。
PRM_ANNOTATION_RECORDED_EVENT = "annotation.prm_annotation.recorded"

_RESOURCE_TYPE = "PRM_ANNOTATION"


class RollbackUnitOfWork(UnitOfWork, Protocol):
    """分叉物化的唯一冲突重试需要事务回滚能力（请求级 ``Session`` 天然满足）。

    只扩展局部协议而不改共享 ``UnitOfWork``：绝大多数用例只用 ``commit``，
    把回滚塞进公共端口会让"写事务边界"的语义变得含混。
    """

    def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ForkResult:
    """分叉用例结果：预览与物化共用。

    ``forked_trajectory_id`` 在预览时是建议 id（``<原 id>#fork-preview``），
    物化时是**实际落库**的 id（冲突递增后的最终值）；``materialized`` 只在
    物化成功时非 ``None``。
    """

    forked: ForkedTrajectory
    pair: StepDpoPair
    forked_trajectory_id: str
    materialized: PrmAnnotationView | None = None


class PrmAnnotationUseCases:
    def __init__(
        self,
        prm_annotations: PrmAnnotationRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: RollbackUnitOfWork,
    ) -> None:
        self._prm_annotations = prm_annotations
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    # ------------------------------------------------------------------ 登记
    def create(
        self,
        scope: ProjectScope,
        annotation: PrmAnnotation,
        *,
        request_id: str | None = None,
    ) -> PrmAnnotationView:
        """登记一条轨迹的步级标注；trajectory 重复视为业务键冲突。"""
        if self._prm_annotations.get_by_trajectory(scope, annotation.trajectory_id) is not None:
            raise DuplicateResourceError(f"轨迹 {annotation.trajectory_id} 已存在步级标注")
        annotation_id = uuid4()
        view = self._prm_annotations.add(
            scope,
            annotation,
            annotation_id=annotation_id,
            actor_id=scope.actor_id,
        )
        self._record_event(
            scope,
            annotation,
            annotation_id=annotation_id,
            action="record",
            event_type=PRM_ANNOTATION_RECORDED_EVENT,
            request_id=request_id,
        )
        self._uow.commit()
        return view

    def create_forked(
        self,
        scope: ProjectScope,
        forked: ForkedTrajectory,
        *,
        forked_trajectory_id: str,
        request_id: str | None = None,
    ) -> PrmAnnotationView:
        """把分叉轨迹物化成独立标注行（与原轨迹构成 Step-DPO 对的 chosen 侧）。"""
        materialized = forked.to_prm_annotation(forked_trajectory_id)
        return self.create(scope, materialized, request_id=request_id)

    # ------------------------------------------------------------------ 查询
    def get(self, scope: ProjectScope, annotation_id: UUID) -> PrmAnnotationView:
        view = self._prm_annotations.get(scope, annotation_id)
        if view is None:
            raise ResourceNotFoundError("PRM 步级标注", annotation_id)
        return view

    def get_by_trajectory(self, scope: ProjectScope, trajectory_id: str) -> PrmAnnotationView:
        view = self._prm_annotations.get_by_trajectory(scope, trajectory_id)
        if view is None:
            raise ResourceNotFoundError("PRM 步级标注", trajectory_id)
        return view

    def list_for_project(
        self,
        scope: ProjectScope,
        *,
        limit: int = 50,
    ) -> list[PrmAnnotationView]:
        if limit < 1 or limit > 200:
            raise DomainValidationError("limit 必须在 1 到 200 之间")
        return self._prm_annotations.list_for_project(scope, limit=limit)

    # ------------------------------------------------------------------ 分叉
    def fork(
        self,
        scope: ProjectScope,
        trajectory_id: str,
        *,
        at_index: int,
        new_actions: tuple[str, ...],
        materialize: bool = False,
        request_id: str | None = None,
    ) -> ForkResult:
        """分叉重写：默认纯计算预览；``materialize=True`` 时同时落库。

        Step-DPO 对语义：分叉点前共享前缀（进 prompt）；分叉点后
        chosen = Forked 重写序列、rejected = Original 原序列。
        """
        prm = self.get_by_trajectory(scope, trajectory_id).annotation
        forked = fork_from_step(prm, at_index, *new_actions)
        pair = to_step_dpo_pair(prm, forked)
        if not materialize:
            preview_id = f"{prm.trajectory_id}#fork-preview"
            return ForkResult(forked, pair, forked_trajectory_id=preview_id)
        return self._materialize(scope, prm, forked, pair, request_id=request_id)

    def _materialize(
        self,
        scope: ProjectScope,
        prm: PrmAnnotation,
        forked: ForkedTrajectory,
        pair: StepDpoPair,
        *,
        request_id: str | None,
    ) -> ForkResult:
        """把分叉轨迹落库：trajectory_id 唯一冲突则递增序号，超限显式失败。"""
        last_error: DuplicateResourceError | None = None
        for sequence in range(1, FORK_MAX_ATTEMPTS + 1):
            candidate = forked_trajectory_id(prm.trajectory_id, sequence)
            try:
                view = self.create_forked(
                    scope,
                    forked,
                    forked_trajectory_id=candidate,
                    request_id=request_id,
                )
            except DuplicateResourceError as exc:
                # 唯一键冲突可能发生在 create 的预查或 flush；flush 失败后
                # Session 处于失败事务，必须先回滚才能继续下一轮尝试。
                self._uow.rollback()
                last_error = exc
                continue
            return ForkResult(
                forked,
                pair,
                forked_trajectory_id=candidate,
                materialized=view,
            )
        raise DuplicateResourceError(
            f"分叉轨迹 id 连续 {FORK_MAX_ATTEMPTS} 次冲突，" "请改用创建端点显式指定 trajectory_id"
        ) from last_error

    # ------------------------------------------------------------------ 内部
    def _record_event(
        self,
        scope: ProjectScope,
        annotation: PrmAnnotation,
        *,
        annotation_id: UUID,
        action: str,
        event_type: str,
        request_id: str | None,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=annotation_id,
            action=action,
            from_status=None,
            to_status="RECORDED",
            reason=None,
            request_id=request_id,
            details={
                "trajectory_id": annotation.trajectory_id,
                "step_count": annotation.step_count,
                "marked_count": annotation.labeled_step_count,
            },
            event_type=event_type,
            event_payload={
                "trajectory_id": annotation.trajectory_id,
                "step_count": annotation.step_count,
                "marked_count": annotation.labeled_step_count,
            },
            occurred_at=datetime.now(UTC),
        )


__all__ = [
    "PRM_ANNOTATION_RECORDED_EVENT",
    "ForkResult",
    "PrmAnnotationUseCases",
    "RollbackUnitOfWork",
]
