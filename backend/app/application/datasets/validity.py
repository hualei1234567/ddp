"""数据集版本有效性巡检（系统用例）。

**为什么需要它**

契约里 ``validity_status`` 是一根独立的状态轴：上游 Episode 失效、Manifest
Hash 变化、策略升级或来源不可达时，版本应从 ``VALID`` 转为 ``STALE``，
默认阻断新的导出与交付。但全仓没有任何检测器写入这个字段——``invalidate`` /
``revalidate`` 因此永远不可达，``STALE`` 永远不出现。

本用例是那个缺失的检测器：按**已有事实**判断上游是否变了，只做"标记过期"，
不代替人做 ``invalidate``（那要负责人决定），也不自动改写历史数据。

判定口径（全部来自库内事实，不猜）：

- 条目引用的 Episode 已不存在 → 上游丢失；
- Episode 状态为 ``INVALIDATED`` / ``REJECTED`` / ``FAILED`` → 上游失效；
- Episode 当前 ``manifest_sha256`` 与条目快照不一致 → 上游内容变了。

任一条成立即把版本标记为 ``STALE`` 并写原因；没有问题时什么都不做（幂等）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.application.datasets.audit import record_change
from app.application.datasets.ports import (
    AuditRepository,
    DatasetItemRepository,
    DatasetVersionRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasets.enums import DatasetVersionStatus, ValidityStatus
from app.domain.datasets.models import DatasetVersion
from app.domain.identity.models import ProjectScope

# 上游一旦进入这些状态，引用它的数据集版本就不再可信。
_UNUSABLE_EPISODE_STATUSES = frozenset({"INVALIDATED", "REJECTED", "FAILED"})

_RESOURCE = "DATASET_VERSION"
_EVENT = "dataset.version.validity_changed"


class UpstreamEpisodeDirectory(Protocol):
    def episode_states(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, tuple[str, str | None, int]]: ...


class OwnerResolver(Protocol):
    def first_owner_id(self, project_id: UUID) -> UUID | None: ...


@dataclass(frozen=True, slots=True)
class ValidityOutcome:
    version_id: UUID
    reasons: tuple[str, ...]


class DatasetValiditySweep:
    def __init__(
        self,
        versions: DatasetVersionRepository,
        items: DatasetItemRepository,
        upstream: UpstreamEpisodeDirectory,
        owners: OwnerResolver,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._versions = versions
        self._items = items
        self._upstream = upstream
        self._owners = owners
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def pending(self, *, limit: int = 200) -> list[tuple[UUID, UUID]]:
        return self._versions.list_validity_candidates(limit=limit)

    def check_version(
        self,
        scope: ProjectScope,
        version_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ValidityOutcome | None:
        moment = now or datetime.now(UTC)
        version = self._versions.get(scope, version_id)
        if version is None or version.validity_status is not ValidityStatus.VALID:
            return None
        if version.status not in {
            DatasetVersionStatus.READY,
            DatasetVersionStatus.FROZEN,
        }:
            return None
        reasons = self._upstream_problems(scope, version)
        if not reasons:
            return None
        updated = version.apply_validity_action(
            "system-mark-stale",
            reason="上游变化：" + "；".join(reasons),
            now=moment,
            actor_id=scope.actor_id,
        )
        saved = self._versions.save(scope, updated, expected_version=version.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.dataset_version_id,
            action="system-mark-stale",
            from_status=version.status.value,
            to_status=saved.status.value,
            reason="上游变化：" + "；".join(reasons),
            request_id=None,
            details={
                "from_validity": version.validity_status.value,
                "to_validity": saved.validity_status.value,
                "reasons": list(reasons),
            },
            event_type=_EVENT,
            event_payload={
                "dataset_version_id": str(saved.dataset_version_id),
                "dataset_id": str(saved.dataset_id),
                "from_validity": version.validity_status.value,
                "to_validity": saved.validity_status.value,
                "action": "system-mark-stale",
                "reasons": list(reasons),
            },
            occurred_at=moment,
        )
        self._uow.commit()
        return ValidityOutcome(version_id=saved.dataset_version_id, reasons=tuple(reasons))

    def run_once(
        self,
        *,
        limit: int = 200,
        now: datetime | None = None,
    ) -> list[ValidityOutcome]:
        moment = now or datetime.now(UTC)
        outcomes: list[ValidityOutcome] = []
        for version_id, project_id in self.pending(limit=limit):
            owner_id = self._owners.first_owner_id(project_id)
            if owner_id is None:
                # 没有负责人就没有可审计的执行人，跳过而不是伪造一个。
                continue
            scope = ProjectScope(project_id, actor_id=owner_id)
            try:
                outcome = self.check_version(scope, version_id, now=moment)
            except SQLAlchemyError:
                # 单条失败不中断整批；下一轮重试。
                self._uow.rollback()
                continue
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _upstream_problems(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
    ) -> list[str]:
        items = self._items.list_all(scope, version.dataset_version_id)
        if not items:
            return ["版本没有任何条目"]
        states = self._upstream.episode_states(
            scope,
            [item.episode_id for item in items],
        )
        missing = 0
        unusable = 0
        changed = 0
        for item in items:
            state = states.get(item.episode_id)
            if state is None:
                missing += 1
                continue
            status, manifest_sha256, _episode_version = state
            if status in _UNUSABLE_EPISODE_STATUSES:
                unusable += 1
                continue
            if item.manifest_sha256 and manifest_sha256 != item.manifest_sha256:
                changed += 1
        reasons: list[str] = []
        if missing:
            reasons.append(f"{missing} 个上游 Episode 已不存在")
        if unusable:
            reasons.append(f"{unusable} 个上游 Episode 已失效")
        if changed:
            reasons.append(f"{changed} 个上游 Manifest 已变化")
        return reasons


__all__ = ["DatasetValiditySweep", "ValidityOutcome"]
