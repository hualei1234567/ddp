"""数据集版本有效性巡检（系统用例）。

**为什么需要它**

契约里 ``validity_status`` 是一根独立的状态轴：上游 Episode 失效、Manifest
Hash 变化、策略升级或来源不可达时，版本应从 ``VALID`` 转为 ``STALE``，
默认阻断新的导出与交付。但全仓没有任何检测器写入这个字段——``invalidate`` /
``revalidate`` 因此永远不可达，``STALE`` 永远不出现。

本用例是那个缺失的检测器：按**已有事实**判断上游是否变了，只做"标记过期 / 标记
未知"，不代替人做 ``invalidate``（那要负责人决定），也不自动改写历史数据。

判定口径（全部来自库内事实或可复用的对象存储引用，不猜）：

- 条目引用的 Episode 已不存在 → 上游丢失（``STALE``）；
- Episode 状态为 ``INVALIDATED`` / ``REJECTED`` / ``FAILED`` → 上游失效（``STALE``）；
- Episode 当前 ``manifest_sha256`` 与条目快照不一致 → 上游内容变了（``STALE``）；
- 上游事实都正常、但 Episode 的 Canonical Manifest 在对象存储中**明确不存在**
  → 来源不可达，无法确认有效性（``UNKNOWN``）。

**UNKNOWN 的语义与恢复**

``UNKNOWN`` 只表达"我们确认不了"，不是"已失效"：

- 探测超时、鉴权失败、存储驱动错误 → **不写任何状态**，本轮放弃、下一轮重试；
  临时网络故障不得把 ``VALID`` 永久写坏；
- 对象明确不存在（404）→ 写 ``UNKNOWN``，保留全部原始证据，只改状态轴；
- 来源恢复可达后，``UNKNOWN`` 只自动推进到 ``STALE``（既有状态机允许的边），
  由负责人 ``revalidate`` 确认后才回 ``VALID``。系统不替人断言"内容仍然有效"；
- 探测与状态迁移都幂等：同一结论重复巡检不会重复写审计与事件。

**为什么不在 HTTP 线程探测**

探测只发生在 ``workers.domain_sweeps`` 的巡检循环里，HTTP 请求只读取库内
``validity_status`` 并 fail-closed 阻断导出/交付。
"""

from __future__ import annotations

import logging
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
    SourceReachability,
    UnitOfWork,
    ValiditySourceProbe,
)
from app.domain.datasets.enums import DatasetVersionStatus, ValidityStatus
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.identity.models import ProjectScope

logger = logging.getLogger("ddp.datasets.validity")

# 上游一旦进入这些状态，引用它的数据集版本就不再可信。
_UNUSABLE_EPISODE_STATUSES = frozenset({"INVALIDATED", "REJECTED", "FAILED"})

_RESOURCE = "DATASET_VERSION"
_EVENT = "dataset.version.validity_changed"

# 以下默认值只为未注入部署配置的调用方（单测/兼容）保留；
# 生产由 ``DDP_DATASET_SOURCE_PROBE_*`` 注入，不在用例内硬编码阈值。
_DEFAULT_PROBE_ATTEMPTS = 2
_DEFAULT_MAX_OBJECTS_PER_VERSION = 200
_DEFAULT_MAX_FAILURES_PER_RUN = 5


class UpstreamEpisodeDirectory(Protocol):
    def episode_states(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, tuple[str, str | None, int]]: ...

    def episode_manifest_keys(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, str | None]:
        """Episode 当前的 Canonical Manifest 对象键；未就绪时为 None。"""
        ...


class OwnerResolver(Protocol):
    def first_owner_id(self, project_id: UUID) -> UUID | None: ...


@dataclass(frozen=True, slots=True)
class ValidityOutcome:
    version_id: UUID
    reasons: tuple[str, ...]


@dataclass(slots=True)
class _ProbeBudget:
    """单轮巡检的探测失败熔断器。

    成功探测不占用预算——对象存储正常时要把候选版本探完；只有连续
    ``UNREACHABLE`` 才计数，达到上限后本轮停止探测，避免存储整体不可用时
    每轮巡检被超时拖死。下一轮重新开始。
    """

    max_failures: int
    failures: int = 0

    @property
    def exhausted(self) -> bool:
        return self.failures >= self.max_failures


@dataclass(frozen=True, slots=True)
class _SourceOutcome:
    reachability: SourceReachability
    missing: int
    probed: int


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
        *,
        source_probe: ValiditySourceProbe | None = None,
        probe_attempts: int = _DEFAULT_PROBE_ATTEMPTS,
        max_objects_per_version: int = _DEFAULT_MAX_OBJECTS_PER_VERSION,
        max_failures_per_run: int = _DEFAULT_MAX_FAILURES_PER_RUN,
    ) -> None:
        self._versions = versions
        self._items = items
        self._upstream = upstream
        self._owners = owners
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._source_probe = source_probe
        self._probe_attempts = max(1, probe_attempts)
        self._max_objects_per_version = max(1, max_objects_per_version)
        self._max_failures_per_run = max(1, max_failures_per_run)
        # 由 ``run_once`` 维护：下一轮从哪个 version_id 之后继续。
        # 可达的 VALID 版本不会被状态变化移出候选集，没有游标会永远只探前 limit 个。
        self.next_after_id: UUID | None = None

    def pending(
        self,
        *,
        limit: int = 200,
        after_id: UUID | None = None,
    ) -> list[tuple[UUID, UUID]]:
        return self._versions.list_validity_candidates(after_id=after_id, limit=limit)

    def check_version(
        self,
        scope: ProjectScope,
        version_id: UUID,
        *,
        now: datetime | None = None,
        budget: _ProbeBudget | None = None,
    ) -> ValidityOutcome | None:
        moment = now or datetime.now(UTC)
        version = self._versions.get(scope, version_id)
        if version is None or version.validity_status not in {
            ValidityStatus.VALID,
            ValidityStatus.UNKNOWN,
        }:
            return None
        if version.status not in {
            DatasetVersionStatus.READY,
            DatasetVersionStatus.FROZEN,
        }:
            return None
        items = self._items.list_all(scope, version.dataset_version_id)
        reasons = self._upstream_problems(scope, version, items)
        if reasons:
            return self._transition(
                scope,
                version,
                action="system-mark-stale",
                reason="上游变化：" + "；".join(reasons),
                reasons=tuple(reasons),
                now=moment,
            )
        if self._source_probe is None:
            return None
        probe_budget = budget or _ProbeBudget(self._max_failures_per_run)
        source = self._source_outcome(scope, version, items, probe_budget)
        if source is None:
            # 未完整探测 / 存储不可达：本轮不下结论，VALID 保持原样。
            return None
        if (
            version.validity_status is ValidityStatus.VALID
            and source.reachability is SourceReachability.MISSING
        ):
            reason = (
                f"{source.missing} 个上游 Episode 的 Canonical Manifest 在对象存储中不存在"
                "（来源不可达，无法确认有效性）"
            )
            return self._transition(
                scope,
                version,
                action="system-mark-unknown",
                reason=reason,
                reasons=(reason,),
                now=moment,
            )
        if (
            version.validity_status is ValidityStatus.UNKNOWN
            and source.reachability is SourceReachability.REACHABLE
        ):
            reason = "来源已恢复可达，原 UNKNOWN 需负责人重新确认有效性"
            return self._transition(
                scope,
                version,
                action="system-mark-stale",
                reason=reason,
                reasons=(reason,),
                now=moment,
            )
        return None

    def run_once(
        self,
        *,
        limit: int = 200,
        now: datetime | None = None,
        after_id: UUID | None = None,
    ) -> list[ValidityOutcome]:
        moment = now or datetime.now(UTC)
        self.next_after_id = None
        # 多取一条判断是否还有下一页；游标让可达的候选版本也能被轮转探到。
        candidates = self.pending(limit=limit + 1, after_id=after_id)
        has_more = len(candidates) > limit
        batch = candidates[:limit]
        if has_more and batch:
            self.next_after_id = batch[-1][0]
        budget = _ProbeBudget(self._max_failures_per_run)
        outcomes: list[ValidityOutcome] = []
        for version_id, project_id in batch:
            owner_id = self._owners.first_owner_id(project_id)
            if owner_id is None:
                # 没有负责人就没有可审计的执行人，跳过而不是伪造一个。
                continue
            scope = ProjectScope(project_id, actor_id=owner_id)
            try:
                outcome = self.check_version(
                    scope,
                    version_id,
                    now=moment,
                    budget=budget,
                )
            except SQLAlchemyError:
                # 单条失败不中断整批；下一轮重试。
                self._uow.rollback()
                continue
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _transition(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        *,
        action: str,
        reason: str,
        reasons: tuple[str, ...],
        now: datetime,
    ) -> ValidityOutcome:
        updated = version.apply_validity_action(
            action,
            reason=reason,
            now=now,
            actor_id=scope.actor_id,
        )
        saved = self._versions.save(scope, updated, expected_version=version.row_version)
        details = {
            "from_validity": version.validity_status.value,
            "to_validity": saved.validity_status.value,
            "reasons": list(reasons),
        }
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.dataset_version_id,
            action=action,
            from_status=version.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=None,
            details=details,
            event_type=_EVENT,
            event_payload={
                "dataset_version_id": str(saved.dataset_version_id),
                "dataset_id": str(saved.dataset_id),
                "from_validity": version.validity_status.value,
                "to_validity": saved.validity_status.value,
                "action": action,
                "reasons": list(reasons),
            },
            occurred_at=now,
        )
        self._uow.commit()
        return ValidityOutcome(version_id=saved.dataset_version_id, reasons=reasons)

    def _upstream_problems(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        items: list[DatasetItem],
    ) -> list[str]:
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

    def _source_outcome(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        items: list[DatasetItem],
        budget: _ProbeBudget,
    ) -> _SourceOutcome | None:
        """探测版本条目的来源对象；返回 None 表示本轮无法下结论。

        只有"每个条目都有可探测引用，且全部探测完成"才给结论。部分条目没有
        ``manifest_key``（Episode 尚未就绪）时保守放弃——把"没有引用"误判为
        "来源丢失"会让合法版本永久 UNKNOWN。
        """
        episode_ids = sorted({item.episode_id for item in items})
        if not episode_ids:
            return None
        if len(episode_ids) > self._max_objects_per_version:
            logger.warning(
                "数据集版本条目超过单轮探测上限，跳过来源探测",
                extra={
                    "dataset_version_id": str(version.dataset_version_id),
                    "episode_count": len(episode_ids),
                    "limit": self._max_objects_per_version,
                },
            )
            return None
        keys = self._upstream.episode_manifest_keys(scope, episode_ids)
        object_keys: list[str] = []
        for episode_id in episode_ids:
            object_key = keys.get(episode_id)
            if object_key is None:
                # Episode 不存在由上游检查处理；存在但没有 Manifest 引用则无法探测。
                return None
            object_keys.append(object_key)
        missing = 0
        for object_key in object_keys:
            verdict = self._probe_object(budget, object_key)
            if verdict is None:
                return None
            if verdict is SourceReachability.UNREACHABLE:
                return None
            if verdict is SourceReachability.MISSING:
                missing += 1
        reachability = SourceReachability.MISSING if missing else SourceReachability.REACHABLE
        return _SourceOutcome(
            reachability=reachability,
            missing=missing,
            probed=len(object_keys),
        )

    def _probe_object(
        self,
        budget: _ProbeBudget,
        object_key: str,
    ) -> SourceReachability | None:
        if self._source_probe is None:
            return None
        for _attempt in range(self._probe_attempts):
            if budget.exhausted:
                return None
            try:
                verdict = self._source_probe.probe(object_key)
            except Exception:  # noqa: BLE001 —— 端口实现违约也不能击穿巡检
                logger.exception(
                    "来源探测适配器异常，按不可达处理",
                    extra={"object_key": object_key},
                )
                verdict = SourceReachability.UNREACHABLE
            if verdict is not SourceReachability.UNREACHABLE:
                return verdict
        budget.failures += 1
        logger.warning(
            "来源对象探测不可达，跳过本轮结论",
            extra={"object_key": object_key, "failures": budget.failures},
        )
        return SourceReachability.UNREACHABLE


__all__ = ["DatasetValiditySweep", "ValidityOutcome"]
