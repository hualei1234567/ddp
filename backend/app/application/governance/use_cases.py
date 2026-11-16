"""治理域用例：配额维护、用量报表与写操作前置判定（N9 规模化运营）。

两条边界必须记住
----------------
1. **报表读快照，不扫全表**：``usage_report`` 只读 Worker 产出的
   ``usage_snapshots``，因此 HTTP 线程不会因为"项目很大"而变慢。
2. **写入判定读实时聚合**：``enforce`` 必须知道**此刻**的用量，才能做到
   "到上限就拒绝，而不是事后才发现"。它只做单项目、单资源类型的
   ``COUNT`` / ``SUM``（走项目索引前缀），不是全表扫描，也不产生 N+1。
   两者口径不同是有意的：快照适合展示，判定必须实时。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.audit import record_change
from app.application.governance.ports import (
    AuditRepository,
    OutboxRepository,
    QuotaRepository,
    ReplayDetector,
    UnitOfWork,
    UsageReader,
    UsageSnapshotRepository,
)
from app.domain.common.ids import new_id
from app.domain.governance.enums import RESOURCE_KIND_ORDER, ResourceKind
from app.domain.governance.errors import (
    DomainValidationError,
    QuotaExceededError,
    QuotaMeasurementUnavailableError,
    QuotaPreconditionRequiredError,
    VersionConflictError,
)
from app.domain.governance.models import (
    QuotaComparison,
    ResourceQuota,
    ResourceUsage,
    UsageReport,
    UsageSnapshot,
)
from app.domain.governance.policy import resource_kind_for
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "RESOURCE_QUOTA"
_QUOTA_LABEL = "资源配额"

# 事件名必须是字面量：``scripts/check_outbox_events.py`` 用 AST 提取事件类型，
# f-string 会被判失败（R-13）。三者都是**留痕类**，无消费者。
GOVERNANCE_QUOTA_CREATED_EVENT = "governance.quota.created"
GOVERNANCE_QUOTA_UPDATED_EVENT = "governance.quota.updated"
GOVERNANCE_QUOTA_EXCEEDED_EVENT = "governance.quota.exceeded"


class GovernanceUseCases:
    def __init__(
        self,
        quotas: QuotaRepository,
        usage: UsageReader,
        snapshots: UsageSnapshotRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        replays: ReplayDetector,
        uow: UnitOfWork,
    ) -> None:
        self._quotas = quotas
        self._usage = usage
        self._snapshots = snapshots
        self._audit = audit
        self._outbox = outbox
        self._replays = replays
        self._uow = uow

    # ---- 配额配置 ---------------------------------------------------------
    def list_quotas(self, scope: ProjectScope) -> list[ResourceQuota]:
        return self._quotas.list(scope)

    def set_quota(
        self,
        scope: ProjectScope,
        kind: ResourceKind | str,
        limit_value: int,
        *,
        expected_version: int | None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> ResourceQuota:
        """创建或修改一类资源的上限。

        修改既有配额必须带 ``If-Match``（``expected_version``）：配额直接决定
        写操作是否被拒，静默覆盖别人的调整会让"谁把上限改低了"无法追溯。
        同值重复提交视为无操作（不升版本、不写审计），避免重试把版本刷高。
        """
        moment = now or datetime.now(UTC)
        existing = self._quotas.get(scope, _coerce_kind(kind))
        candidate = ResourceQuota(
            quota_id=existing.quota_id if existing is not None else new_id(),
            project_id=scope.project_id,
            resource_kind=_coerce_kind(kind),
            limit_value=limit_value,
            version=existing.version if existing is not None else 1,
            created_at=existing.created_at if existing is not None else moment,
            updated_at=moment,
            created_by=existing.created_by if existing is not None else scope.actor_id,
            updated_by=scope.actor_id,
        )

        if existing is None:
            if expected_version is not None:
                # 客户端以为配额已存在。不落库、不悄悄改成新建。
                raise VersionConflictError(_QUOTA_LABEL)
            created = self._quotas.create(scope, candidate)
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_RESOURCE_TYPE,
                resource_id=created.quota_id,
                action="create",
                from_status=None,
                to_status=None,
                reason=None,
                request_id=request_id,
                details={
                    "resource_kind": created.resource_kind.value,
                    "limit": created.limit_value,
                },
                event_type=GOVERNANCE_QUOTA_CREATED_EVENT,
                event_payload={
                    "quota_id": str(created.quota_id),
                    "resource_kind": created.resource_kind.value,
                    "limit": created.limit_value,
                    "version": created.version,
                },
                occurred_at=moment,
            )
            self._uow.commit()
            return created

        if expected_version is None:
            raise QuotaPreconditionRequiredError()
        if expected_version != existing.version:
            raise VersionConflictError(_QUOTA_LABEL)
        if limit_value == existing.limit_value:
            return existing

        updated = existing.with_limit(limit_value, now=moment, actor_id=scope.actor_id)
        saved = self._quotas.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.quota_id,
            action="update",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={
                "resource_kind": saved.resource_kind.value,
                "limit": saved.limit_value,
                "previous_limit": existing.limit_value,
            },
            event_type=GOVERNANCE_QUOTA_UPDATED_EVENT,
            event_payload={
                "quota_id": str(saved.quota_id),
                "resource_kind": saved.resource_kind.value,
                "limit": saved.limit_value,
                "previous_limit": existing.limit_value,
                "version": saved.version,
            },
            occurred_at=moment,
        )
        self._uow.commit()
        return saved

    # ---- 报表 -------------------------------------------------------------
    def usage_report(
        self,
        scope: ProjectScope,
        *,
        now: datetime | None = None,
    ) -> UsageReport:
        """读取最近一次离线快照，与配额形成对照。

        某类资源没有快照时输出 ``used=None`` / ``status=UNMEASURED``，
        不用 0 冒充；某个项目没配该类配额时输出 ``limit=None`` /
        ``status=UNLIMITED``。
        """
        moment = now or datetime.now(UTC)
        measured = self._snapshots.latest(scope)
        quotas = {quota.resource_kind: quota for quota in self._quotas.list(scope)}
        entries = tuple(
            QuotaComparison(
                usage=measured.get(kind) or ResourceUsage(resource_kind=kind, value=None),
                quota=quotas.get(kind),
            )
            for kind in RESOURCE_KIND_ORDER
        )
        return UsageReport(
            project_id=scope.project_id,
            generated_at=moment,
            entries=entries,
        )

    # ---- 写操作前置判定 ---------------------------------------------------
    def enforce(
        self,
        scope: ProjectScope,
        operation: str,
        *,
        quantity: int = 1,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        method: str | None = None,
        path: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """写操作前判定；超限抛 :class:`QuotaExceededError` 并留审计。

        未配置配额 ⇒ 直接放行（"没设上限"是合法的运营选择）。
        配置了配额但测量失败 ⇒ fail-closed，抛
        :class:`QuotaMeasurementUnavailableError`（503），**不**放行。
        已成功请求的幂等重放 ⇒ 直接放行：重放不会新建资源，也不应因为
        此刻已到上限而拿不回原本成功的结果。
        """
        moment = now or datetime.now(UTC)
        kind = resource_kind_for(operation)
        requested = _positive_quantity(quantity)
        if idempotency_key and method and path:
            if self._replays.is_replay(
                scope,
                idempotency_key,
                method=method,
                path=path,
                now=moment,
            ):
                return
        quota = self._quotas.get(scope, kind)
        if quota is None:
            return

        usage = self._usage.measure(scope, kind, at=moment)
        if usage.value is None:
            # 协议上 measure 成功必带值；这里防御性拦住"返回空值"的实现。
            raise QuotaMeasurementUnavailableError(kind)
        used = usage.value
        if used + requested <= quota.limit_value:
            return

        # 拒绝也要留痕。业务写入不会发生，因此这条审计**必须独立提交**：
        # 若与随后抛出的异常共处一个最终回滚的事务里，它就永远查不到。
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=quota.quota_id,
            action="quota-exceeded",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            result="FAILURE",
            details={
                "operation": operation,
                "resource_kind": kind.value,
                "limit": quota.limit_value,
                "used": used,
                "requested": requested,
            },
            event_type=GOVERNANCE_QUOTA_EXCEEDED_EVENT,
            event_payload={
                "operation": operation,
                "resource_kind": kind.value,
                "limit": quota.limit_value,
                "used": used,
                "requested": requested,
            },
            occurred_at=moment,
        )
        self._uow.commit()
        raise QuotaExceededError(
            kind,
            limit=quota.limit_value,
            used=used,
            requested=requested,
        )


class SnapshotUseCases:
    """离线用量快照刷新（由 Worker 调用，不在 HTTP 线程运行）。"""

    def __init__(
        self,
        usage: UsageReader,
        snapshots: UsageSnapshotRepository,
        uow: UnitOfWork,
    ) -> None:
        self._usage = usage
        self._snapshots = snapshots
        self._uow = uow

    def refresh(self, *, now: datetime | None = None) -> int:
        """重算全部项目的全部资源用量并整体替换快照。

        整体替换而不是逐行 upsert：快照是 **缓存**，不是事实来源；
        一次事务内替换保证读者要么看到旧的一组、要么看到新的一组，
        不会看到"一半新一半旧"的混合报表。失败时事务回滚，旧快照保留。
        """
        moment = now or datetime.now(UTC)
        measured = self._usage.measure_all(at=moment)
        rows = [
            UsageSnapshot(project_id=project_id, usage=usage)
            for project_id, per_kind in measured.items()
            for usage in per_kind.values()
        ]
        self._snapshots.replace_all(rows)
        self._uow.commit()
        return len(rows)


def _coerce_kind(value: ResourceKind | str) -> ResourceKind:
    try:
        return ResourceKind(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in ResourceKind)
        raise DomainValidationError(f"resource_kind 取值非法，允许值：{allowed}") from exc


def _positive_quantity(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError("quantity 必须是正整数")
    return value


__all__ = [
    "GOVERNANCE_QUOTA_CREATED_EVENT",
    "GOVERNANCE_QUOTA_EXCEEDED_EVENT",
    "GOVERNANCE_QUOTA_UPDATED_EVENT",
    "GovernanceUseCases",
    "SnapshotUseCases",
]
