"""存量外部训练引用的 ``TRAINED_ON`` 血缘回填（可审计补偿用例）。

**缺口**：N5-005 上线前登记的 ``external_training_references`` 没有
``DatasetVersion --TRAINED_ON--> TrainingRun`` 边，"模型版本用了哪份数据"
查不到。本用例只补缺失的边，不重放训练、不改引用状态、不删不改任何历史。

**为什么不新写写边器**：写边一律复用
:meth:`app.application.datasets.lineage.DatasetVersionLineageRecorder.record_training_run`，
它负责"先节点、后边"，并由仓储 ``ensure_edge`` 保证幂等（已存在返回 ``False``）。

**默认 dry-run**：``apply=False`` 只做只读扫描并产出逐条报告（项目、来源、
目标），一个字节都不写；只有 ``apply=True`` 才进入写事务。每条补边与审计、
outbox 在同一次提交里完成；单条失败只回滚该条并计入 ``failed``，不中断整批
（与 ``DatasetValiditySweep`` 同口径）。

**有界与超时**：键集分页（``after_id``）+ 批大小 + 批次数上限；超过
``timeout_seconds`` 时在**批次之间**停止并标记 ``timed_out``。它由独立 Worker
CLI 调用，不在 HTTP 线程执行。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.application.audit import record_change
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.ports import AuditRepository, OutboxRepository
from app.application.datasets.training_ports import (
    DEFAULT_BATCH_LIMIT,
    MAX_BATCH_LIMIT,
    LineageBackfillCandidate,
    TrainingLineageBackfillSource,
)
from app.domain.datasets.errors import DatasetDomainError
from app.domain.datasets.errors import DomainValidationError as DatasetValidationError
from app.domain.episodes.errors import EpisodeDomainError
from app.domain.identity.models import ProjectScope

logger = logging.getLogger("ddp.lineage.backfill")

_RESOURCE = "EXTERNAL_TRAINING_REFERENCE"
_ACTION = "backfill-lineage"
# 留痕类事件：补偿写入不是集成信号，不需要消费者（``dispatch`` 默认 False）。
_EVENT = "training.external_reference.lineage_backfilled"

ACTION_PLANNED = "planned"
ACTION_CREATED = "created"
ACTION_SKIPPED = "skipped"
ACTION_FAILED = "failed"

# 单条失败原因：不是异常堆栈的替代，异常本身仍走 logger.exception。
REASON_DATASET_VERSION_MISSING = "dataset_version_missing"
REASON_EDGE_EXISTS = "edge_exists"


class TransactionBoundary(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class DatasetVersionLookup(Protocol):
    """按项目作用域读取版本；读不到即视为"本项目没有这份数据"。"""

    def get(self, scope: ProjectScope, version_id: UUID) -> object | None: ...


@dataclass(frozen=True, slots=True)
class BackfillEntry:
    project_id: UUID
    external_ref_id: UUID
    dataset_version_id: UUID
    adapter_id: str
    external_run_id: str
    action: str
    reason: str | None = None

    def describe(self) -> str:
        """一行可读报告：项目 + 来源 + 目标 + 结果。"""
        text = (
            f"{self.action} project={self.project_id} "
            f"source=DATASET_VERSION {self.dataset_version_id} "
            f"target=TRAINING_RUN {self.external_ref_id} "
            f"adapter={self.adapter_id} run={self.external_run_id}"
        )
        return f"{text} reason={self.reason}" if self.reason else text


@dataclass(frozen=True, slots=True)
class BackfillReport:
    applied: bool
    examined: int
    planned: int
    created: int
    skipped: int
    failed: int
    timed_out: bool
    entries: tuple[BackfillEntry, ...]

    def summary(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "examined": self.examined,
            "planned": self.planned,
            "created": self.created,
            "skipped": self.skipped,
            "failed": self.failed,
            "timed_out": self.timed_out,
        }


class TrainingLineageBackfill:
    def __init__(
        self,
        *,
        candidates: TrainingLineageBackfillSource,
        versions: DatasetVersionLookup,
        lineage: DatasetVersionLineageRecorder,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: TransactionBoundary,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._candidates = candidates
        self._versions = versions
        self._lineage = lineage
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._monotonic = monotonic

    def run(
        self,
        *,
        apply: bool = False,
        limit: int = DEFAULT_BATCH_LIMIT,
        max_batches: int = 1,
        timeout_seconds: float | None = None,
        now: datetime | None = None,
    ) -> BackfillReport:
        if limit < 1 or limit > MAX_BATCH_LIMIT:
            raise DatasetValidationError(f"limit 必须在 1 到 {MAX_BATCH_LIMIT} 之间")
        if max_batches < 1:
            raise DatasetValidationError("max_batches 必须至少为 1")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise DatasetValidationError("timeout_seconds 必须大于 0")
        moment = now or datetime.now(UTC)
        deadline = None if timeout_seconds is None else self._monotonic() + timeout_seconds
        entries: list[BackfillEntry] = []
        after_id: UUID | None = None
        batches = 0
        timed_out = False
        while batches < max_batches:
            if deadline is not None and self._monotonic() >= deadline:
                timed_out = True
                break
            batch = self._candidates.list_missing_trained_on(after_id=after_id, limit=limit)
            if not batch:
                break
            batches += 1
            for candidate in batch:
                entries.append(self._process(candidate, apply=apply, moment=moment))
            after_id = batch[-1].external_ref_id
        return _build_report(apply=apply, entries=entries, timed_out=timed_out)

    def _process(
        self,
        candidate: LineageBackfillCandidate,
        *,
        apply: bool,
        moment: datetime,
    ) -> BackfillEntry:
        # 执行人取引用的原始创建者：边的 created_by 与审计的 actor 都必须落在真实
        # 用户上（数据库外键），且这是与该训练事件最相关的责任人。补偿性质由
        # action=backfill-lineage 与 details.backfilled 显式标注，不冒充人工操作。
        scope = ProjectScope(candidate.project_id, actor_id=candidate.created_by)
        if self._versions.get(scope, candidate.dataset_version_id) is None:
            # 源版本在本项目不存在（可能是跨项目脏引用）：不写边、不造节点。
            logger.warning(
                "回填跳过：源数据集版本不可见",
                extra={
                    "project_id": str(candidate.project_id),
                    "external_ref_id": str(candidate.external_ref_id),
                    "dataset_version_id": str(candidate.dataset_version_id),
                },
            )
            return _entry(candidate, ACTION_SKIPPED, REASON_DATASET_VERSION_MISSING)
        if not apply:
            return _entry(candidate, ACTION_PLANNED)
        metadata: dict[str, object] = {
            "training_delivery_id": str(candidate.training_delivery_id),
            "training_request_revision_id": str(candidate.training_request_revision_id),
            "adapter_id": candidate.adapter_id,
            "external_run_id": candidate.external_run_id,
            "execution_mode": candidate.execution_mode,
            "backfilled": True,
            "backfilled_at": moment.isoformat(),
        }
        try:
            created = self._lineage.record_training_run(
                scope,
                dataset_version_id=candidate.dataset_version_id,
                training_run_id=candidate.external_ref_id,
                # 新边沿用引用的原始创建时间：血缘时间线描述真实训练事件，
                # 而不是补偿任务的执行时刻。执行时刻记在 metadata 与审计里。
                now=candidate.created_at,
                metadata=metadata,
            )
            if not created:
                # 并发写入同一条边：回滚本轮可能产生的节点副作用，视为幂等成功。
                self._uow.rollback()
                return _entry(candidate, ACTION_SKIPPED, REASON_EDGE_EXISTS)
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_RESOURCE,
                resource_id=candidate.external_ref_id,
                action=_ACTION,
                from_status=None,
                to_status=None,
                reason="补写缺失的 TRAINED_ON 血缘边",
                request_id=None,
                details={
                    "dataset_version_id": str(candidate.dataset_version_id),
                    "relation": "TRAINED_ON",
                    "from_node_type": "DATASET_VERSION",
                    "to_node_type": "TRAINING_RUN",
                    "adapter_id": candidate.adapter_id,
                    "external_run_id": candidate.external_run_id,
                    "edge_created_at": candidate.created_at.isoformat(),
                    "backfilled": True,
                },
                event_type=_EVENT,
                event_payload={
                    "external_ref_id": str(candidate.external_ref_id),
                    "dataset_version_id": str(candidate.dataset_version_id),
                    "relation": "TRAINED_ON",
                    "adapter_id": candidate.adapter_id,
                    "external_run_id": candidate.external_run_id,
                },
                occurred_at=moment,
            )
            self._uow.commit()
        except (SQLAlchemyError, EpisodeDomainError, DatasetDomainError):
            self._uow.rollback()
            logger.exception(
                "回填失败，留待下一轮重试",
                extra={
                    "project_id": str(candidate.project_id),
                    "external_ref_id": str(candidate.external_ref_id),
                },
            )
            return _entry(candidate, ACTION_FAILED, "write_failed")
        return _entry(candidate, ACTION_CREATED)


def _entry(
    candidate: LineageBackfillCandidate,
    action: str,
    reason: str | None = None,
) -> BackfillEntry:
    return BackfillEntry(
        project_id=candidate.project_id,
        external_ref_id=candidate.external_ref_id,
        dataset_version_id=candidate.dataset_version_id,
        adapter_id=candidate.adapter_id,
        external_run_id=candidate.external_run_id,
        action=action,
        reason=reason,
    )


def _build_report(
    *,
    apply: bool,
    entries: list[BackfillEntry],
    timed_out: bool,
) -> BackfillReport:
    def count(action: str) -> int:
        return sum(1 for entry in entries if entry.action == action)

    return BackfillReport(
        applied=apply,
        examined=len(entries),
        planned=count(ACTION_PLANNED),
        created=count(ACTION_CREATED),
        skipped=count(ACTION_SKIPPED),
        failed=count(ACTION_FAILED),
        timed_out=timed_out,
        entries=tuple(entries),
    )


__all__ = [
    "ACTION_CREATED",
    "ACTION_FAILED",
    "ACTION_PLANNED",
    "ACTION_SKIPPED",
    "DEFAULT_BATCH_LIMIT",
    "MAX_BATCH_LIMIT",
    "BackfillEntry",
    "BackfillReport",
    "TrainingLineageBackfill",
]
