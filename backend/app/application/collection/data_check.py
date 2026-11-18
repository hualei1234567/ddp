"""采集任务数据检查（系统用例）：``SUBMITTED`` → ``DATA_CHECKING``。

**为什么需要它**

采集任务状态机里 ``SUBMITTED --system-data-check--> DATA_CHECKING`` 一直存在，
但全仓没有任何触发点：任务提交后就永久停在 ``SUBMITTED``，验收（accept）与
完成（complete）按钮永远不会出现——"数据检查"这一步在界面上根本走不到。

本用例是那个缺失的触发点：扫到 ``SUBMITTED`` 的任务，用**已落库的事实**
跑一遍检查，把结论写进 ``quality_summary`` 并推进状态。它不代替人做决定：
通过与否都推进到 ``DATA_CHECKING``，accept / reject 仍由人点。

**检查口径（不猜）**

- 任务名下的导入（迁移 0050 的 ``collection_task_id`` 归属）与媒体资产；
- 每个导入复用 ``evaluate_import_quality`` 的既有规则（观测项，不另写一套）；
- 任务级只看能由事实判定的东西：有没有导入、有没有资产、有没有片段、
  各导入的规则里哪些没通过。

**幂等与失败处理**

- 只处理 ``SUBMITTED``：并发/重复扫描时第二次直接跳过（状态已变）；
- 单个任务失败只记日志并保持原状态，下一次扫描重试；不中断整批；
- 每个任务独立事务提交：一个坏任务不会回滚其他任务的检查结果。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.application.collection.audit import record_change
from app.application.collection.ports import (
    AuditRepository,
    CollectionTaskRepository,
    OutboxRepository,
)
from app.application.datasources.ports import (
    DataSourceImportRepository,
    MediaAssetRepository,
)
from app.application.datasources.quality import RULE_VERSION, evaluate_import_quality
from app.domain.collection.enums import CollectionTaskStatus
from app.domain.collection.models import CollectionTask
from app.domain.datasources.errors import DomainValidationError
from app.domain.identity.models import ProjectScope

logger = logging.getLogger("ddp.application.collection.data_check")

DATA_CHECKED_EVENT = "collection.task.data_checked"
# 与 ``SqlDataSourceImportRepository.list_for_collection_task`` 的单次上限一致。
_IMPORT_PAGE = 100


@dataclass(frozen=True, slots=True)
class DataCheckOutcome:
    task_id: UUID
    status: str
    summary: dict[str, object]
    failed_rules: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failed_rules


class OwnerResolver(Protocol):
    """系统任务的执行人解析：``ProjectScope.actor_id`` 必填且会写进审计。"""

    def first_owner_id(self, project_id: UUID) -> UUID | None: ...


class CollectionDataCheckUseCases:
    def __init__(
        self,
        tasks: CollectionTaskRepository,
        imports: DataSourceImportRepository,
        assets: MediaAssetRepository,
        owners: OwnerResolver,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: object,
    ) -> None:
        self._tasks = tasks
        self._imports = imports
        self._assets = assets
        self._owners = owners
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def pending(self, *, limit: int = 50) -> list[tuple[UUID, UUID]]:
        """跨项目待检查任务 ``(task_id, project_id)``。"""
        return self._tasks.list_pending_status(CollectionTaskStatus.SUBMITTED, limit=limit)

    def check_task(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> DataCheckOutcome | None:
        moment = now or datetime.now(UTC)
        task = self._tasks.get(scope, task_id)
        if task is None or task.status is not CollectionTaskStatus.SUBMITTED:
            # 并发/重复扫描：状态已不是 SUBMITTED，跳过而不是报错。
            return None
        summary, failed = self._inspect(scope, task, now=moment)
        updated = task.apply_action("system-data-check", now=moment)
        updated = _with_summary(updated, summary)
        saved = self._tasks.save(scope, updated, expected_version=task.version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="collection_task",
            resource_id=saved.task_id,
            action="system-data-check",
            from_status=task.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=None,
            details={
                "imports": summary["imports"],
                "assets": summary["assets"],
                "episodes": summary["episodes"],
                "failed_rules": list(failed),
            },
            event_type=DATA_CHECKED_EVENT,
            event_payload={
                "task_id": str(saved.task_id),
                "from_status": task.status.value,
                "to_status": saved.status.value,
                "passed": not failed,
                "failed_rules": list(failed),
                "version": saved.version,
            },
            occurred_at=moment,
        )
        self._uow.commit()  # type: ignore[attr-defined]
        return DataCheckOutcome(
            task_id=saved.task_id,
            status=saved.status.value,
            summary=summary,
            failed_rules=tuple(failed),
        )

    def run_once(
        self,
        *,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[DataCheckOutcome]:
        """扫一批 ``SUBMITTED`` 任务；单任务失败不中断整批。"""
        moment = now or datetime.now(UTC)
        outcomes: list[DataCheckOutcome] = []
        for task_id, project_id in self.pending(limit=limit):
            owner_id = self._owners.first_owner_id(project_id)
            if owner_id is None:
                # 项目没有负责人：没有可审计的执行人，跳过而不是伪造一个。
                logger.warning(
                    "采集任务数据检查跳过：项目没有负责人",
                    extra={"task_id": str(task_id), "project_id": str(project_id)},
                )
                continue
            scope = ProjectScope(project_id, actor_id=owner_id)
            try:
                outcome = self.check_task(scope, task_id, now=moment)
            except (DomainValidationError, SQLAlchemyError):
                # 单个任务失败：回滚本次事务，保持 SUBMITTED，下一轮重试。
                self._uow.rollback()  # type: ignore[attr-defined]
                logger.exception("采集任务数据检查失败", extra={"task_id": str(task_id)})
                continue
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _inspect(
        self,
        scope: ProjectScope,
        task: CollectionTask,
        *,
        now: datetime,
    ) -> tuple[dict[str, object], list[str]]:
        imports = self._imports.list_for_collection_task(scope, task.task_id, limit=_IMPORT_PAGE)
        assets = [
            asset
            for item in imports
            for asset in self._assets.list_by_import(scope, item.import_id, limit=1000)
        ]
        failed: list[str] = []
        if not imports:
            failed.append("imports_present")
        if len(imports) >= _IMPORT_PAGE:
            # 仓储单次最多返回这么多个导入。达到上限时必须如实说"没看全"，
            # 否则一份被截断的检查会被当成"全部导入都已通过"。
            failed.append("imports_truncated")
        for item in imports:
            media = [asset for asset in assets if asset.import_id == item.import_id]
            report = evaluate_import_quality(
                project_id=scope.project_id,
                import_id=item.import_id,
                sample_scope=item.scope,
                assets=media,
            )
            failed.extend(
                f"import:{item.import_id}:{finding.rule_id}"
                for finding in report.findings
                if not finding.passed
            )
        episode_keys = {asset.episode_key for asset in assets}
        if imports and not assets:
            failed.append("assets_present")
        if imports and not episode_keys:
            failed.append("episodes_present")
        summary: dict[str, object] = {
            "rule_version": RULE_VERSION,
            "checked_at": now.isoformat(),
            "imports": len(imports),
            "assets": len(assets),
            "episodes": len(episode_keys),
            "failed_rules": list(dict.fromkeys(failed)),
            "passed": not failed,
        }
        return summary, list(dict.fromkeys(failed))


def _with_summary(task: CollectionTask, summary: dict[str, object]) -> CollectionTask:
    # 只写检查结论；submitted/accepted 计数由任务用例在每次动作前按 Episode
    # 事实重算（唯一权威口径），这里再写一遍只会制造第二个来源。
    return replace(task, quality_summary=summary)


__all__ = ["CollectionDataCheckUseCases", "DataCheckOutcome", "DATA_CHECKED_EVENT"]
