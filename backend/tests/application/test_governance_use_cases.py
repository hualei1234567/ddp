"""治理用例测试：配额配置、报表三态与写操作前置判定（真实业务逻辑，无 HTTP）。

用内存 Fake 替身隔离数据库，重点验证编排语义：

- 配额变更必须留审计；
- 超限拒绝必须在抛出异常**之前**提交审计；
- 未配置配额 / 未测量 / 已测量三种情形不混淆。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.governance import (
    GOVERNANCE_QUOTA_CREATED_EVENT,
    GOVERNANCE_QUOTA_EXCEEDED_EVENT,
    GOVERNANCE_QUOTA_UPDATED_EVENT,
    GovernanceUseCases,
    SnapshotUseCases,
)
from app.domain.governance import (
    EPISODE_CREATE,
    INGESTION_JOB_CREATE,
    OBJECT_STORAGE_UPLOAD,
    DomainValidationError,
    QuotaExceededError,
    QuotaMeasurementUnavailableError,
    QuotaPreconditionRequiredError,
    QuotaStatus,
    ResourceKind,
    ResourceQuota,
    ResourceUsage,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class FakeQuotaRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, ResourceKind], ResourceQuota] = {}

    def get(self, scope: ProjectScope, kind: ResourceKind) -> ResourceQuota | None:
        return self.rows.get((scope.project_id, kind))

    def list(self, scope: ProjectScope) -> list[ResourceQuota]:
        return [
            quota for (project, _kind), quota in self.rows.items() if project == scope.project_id
        ]

    def create(self, scope: ProjectScope, quota: ResourceQuota) -> ResourceQuota:
        self.rows[(scope.project_id, quota.resource_kind)] = quota
        return quota

    def save(
        self,
        scope: ProjectScope,
        quota: ResourceQuota,
        *,
        expected_version: int,
    ) -> ResourceQuota:
        current = self.rows[(scope.project_id, quota.resource_kind)]
        if current.version != expected_version:
            raise VersionConflictError("资源配额")
        saved = ResourceQuota(
            quota_id=quota.quota_id,
            project_id=quota.project_id,
            resource_kind=quota.resource_kind,
            limit_value=quota.limit_value,
            version=expected_version + 1,
            created_at=quota.created_at,
            updated_at=quota.updated_at,
            created_by=quota.created_by,
            updated_by=quota.updated_by,
        )
        self.rows[(scope.project_id, quota.resource_kind)] = saved
        return saved


class FakeUsageReader:
    def __init__(
        self,
        value: int = 0,
        *,
        error: Exception | None = None,
        all_values: dict[UUID, dict[ResourceKind, int]] | None = None,
    ) -> None:
        self.value = value
        self.error = error
        self.all_values = all_values or {}
        self.measured_all_at: datetime | None = None

    def measure(
        self,
        scope: ProjectScope,
        kind: ResourceKind,
        *,
        at: datetime,
    ) -> ResourceUsage:
        if self.error is not None:
            raise self.error
        return ResourceUsage(
            resource_kind=kind,
            value=self.value,
            measured_at=at,
            source="fake.count",
        )

    def measure_all(self, *, at: datetime) -> dict[UUID, dict[ResourceKind, ResourceUsage]]:
        self.measured_all_at = at
        return {
            project_id: {
                kind: ResourceUsage(
                    resource_kind=kind,
                    value=value,
                    measured_at=at,
                    source="fake.count",
                )
                for kind, value in per_kind.items()
            }
            for project_id, per_kind in self.all_values.items()
        }


class FakeSnapshotRepository:
    def __init__(self) -> None:
        self.store: dict[UUID, dict[ResourceKind, ResourceUsage]] = {}
        self.replaced: list[tuple[UUID, ResourceKind, int]] = []

    def latest(self, scope: ProjectScope) -> dict[ResourceKind, ResourceUsage]:
        return dict(self.store.get(scope.project_id, {}))

    def replace_all(self, rows: object) -> None:
        self.store.clear()
        self.replaced.clear()
        for row in rows:  # type: ignore[union-attr]
            self.store.setdefault(row.project_id, {})[row.usage.resource_kind] = row.usage
            self.replaced.append((row.project_id, row.usage.resource_kind, row.usage.value or 0))


class FakeReplayDetector:
    def __init__(self, *, replay: bool = False) -> None:
        self.replay = replay
        self.calls: list[tuple[str, str, str]] = []

    def is_replay(
        self,
        scope: ProjectScope,
        key: str,
        *,
        method: str,
        path: str,
        now: datetime,
    ) -> bool:
        self.calls.append((key, method, path))
        return self.replay


class Recorder:
    def __init__(self) -> None:
        self.records: list[object] = []
        self.events: list[object] = []


class FakeAudit:
    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def record(self, scope: ProjectScope, record: object) -> None:
        self._recorder.records.append(record)


class FakeOutbox:
    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def enqueue(self, scope: ProjectScope, event: object) -> None:
        self._recorder.events.append(event)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _build(
    *,
    usage: FakeUsageReader | None = None,
    replays: FakeReplayDetector | None = None,
) -> tuple[
    GovernanceUseCases,
    FakeQuotaRepository,
    FakeUsageReader,
    FakeSnapshotRepository,
    Recorder,
    FakeUnitOfWork,
]:
    quotas = FakeQuotaRepository()
    reader = usage or FakeUsageReader(value=0)
    snapshots = FakeSnapshotRepository()
    recorder = Recorder()
    uow = FakeUnitOfWork()
    use_cases = GovernanceUseCases(
        quotas=quotas,
        usage=reader,
        snapshots=snapshots,
        audit=FakeAudit(recorder),
        outbox=FakeOutbox(recorder),
        replays=replays or FakeReplayDetector(),
        uow=uow,
    )
    return use_cases, quotas, reader, snapshots, recorder, uow


def _scope() -> ProjectScope:
    return ProjectScope(project_id=uuid4(), actor_id=uuid4())


# --- 配额配置 --------------------------------------------------------------
def test_create_quota_writes_audit_and_commits() -> None:
    use_cases, quotas, _reader, _snapshots, recorder, uow = _build()
    scope = _scope()

    created = use_cases.set_quota(
        scope,
        ResourceKind.EPISODES,
        100,
        expected_version=None,
        now=NOW,
    )

    assert created.version == 1
    assert quotas.get(scope, ResourceKind.EPISODES) is not None
    assert uow.commits == 1
    assert len(recorder.records) == 1
    assert recorder.events[0].event_type == GOVERNANCE_QUOTA_CREATED_EVENT


def test_update_existing_quota_requires_if_match() -> None:
    use_cases, _quotas, _reader, _snapshots, _recorder, _uow = _build()
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 100, expected_version=None)

    with pytest.raises(QuotaPreconditionRequiredError):
        use_cases.set_quota(scope, ResourceKind.EPISODES, 200, expected_version=None)


def test_update_with_stale_version_conflicts() -> None:
    use_cases, _quotas, _reader, _snapshots, _recorder, _uow = _build()
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 100, expected_version=None)

    with pytest.raises(VersionConflictError):
        use_cases.set_quota(scope, ResourceKind.EPISODES, 200, expected_version=99)


def test_update_quota_bumps_version_and_records_previous_limit() -> None:
    use_cases, _quotas, _reader, _snapshots, recorder, uow = _build()
    scope = _scope()
    created = use_cases.set_quota(scope, ResourceKind.EPISODES, 100, expected_version=None)

    updated = use_cases.set_quota(
        scope,
        ResourceKind.EPISODES,
        150,
        expected_version=created.version,
        now=NOW,
    )

    assert updated.version == 2
    assert updated.limit_value == 150
    assert uow.commits == 2
    assert recorder.events[-1].event_type == GOVERNANCE_QUOTA_UPDATED_EVENT
    assert recorder.records[-1].details["previous_limit"] == 100


def test_same_limit_is_noop_without_version_bump() -> None:
    use_cases, _quotas, _reader, _snapshots, recorder, uow = _build()
    scope = _scope()
    created = use_cases.set_quota(scope, ResourceKind.EPISODES, 100, expected_version=None)

    again = use_cases.set_quota(
        scope,
        ResourceKind.EPISODES,
        100,
        expected_version=created.version,
    )

    assert again.version == created.version
    assert uow.commits == 1
    assert len(recorder.records) == 1


# --- 报表 ------------------------------------------------------------------
def test_report_marks_missing_snapshot_as_unmeasured() -> None:
    use_cases, _quotas, _reader, _snapshots, _recorder, _uow = _build()
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 10, expected_version=None)

    report = use_cases.usage_report(scope, now=NOW)

    entries = {entry.resource_kind: entry for entry in report.entries}
    assert len(entries) == len(ResourceKind)
    assert entries[ResourceKind.EPISODES].used is None
    assert entries[ResourceKind.EPISODES].status is QuotaStatus.UNMEASURED
    assert entries[ResourceKind.DATASET_VERSIONS].status is QuotaStatus.UNMEASURED


def test_report_compares_snapshot_with_quota() -> None:
    use_cases, _quotas, _reader, snapshots, _recorder, _uow = _build()
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 10, expected_version=None)
    snapshots.store[scope.project_id] = {
        ResourceKind.EPISODES: ResourceUsage(
            resource_kind=ResourceKind.EPISODES,
            value=12,
            measured_at=NOW,
            source="episodes.count",
        )
    }

    report = use_cases.usage_report(scope, now=NOW)
    entries = {entry.resource_kind: entry for entry in report.entries}

    assert entries[ResourceKind.EPISODES].used == 12
    assert entries[ResourceKind.EPISODES].status is QuotaStatus.OVER_LIMIT
    assert entries[ResourceKind.EPISODES].remaining == 0
    assert entries[ResourceKind.EPISODES].usage.measured_at == NOW
    # 没配配额也没有快照：未测量优先于无限额。
    assert entries[ResourceKind.INGESTION_JOBS].status is QuotaStatus.UNMEASURED
    assert report.generated_at == NOW


# --- 写操作判定 ------------------------------------------------------------
def test_enforce_passes_when_quota_not_configured() -> None:
    use_cases, _quotas, reader, _snapshots, recorder, uow = _build()
    use_cases.enforce(_scope(), EPISODE_CREATE)
    assert reader.value == 0
    assert uow.commits == 0
    assert recorder.records == []


def test_enforce_allows_write_within_limit() -> None:
    use_cases, _quotas, _reader, _snapshots, recorder, uow = _build(usage=FakeUsageReader(9))
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 10, expected_version=None)
    uow.commits = 0

    use_cases.enforce(scope, EPISODE_CREATE)

    assert uow.commits == 0
    assert len(recorder.records) == 1  # 只有创建配额那条


def test_enforce_rejects_at_limit_and_commits_audit_before_raising() -> None:
    use_cases, _quotas, _reader, _snapshots, recorder, uow = _build(usage=FakeUsageReader(10))
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 10, expected_version=None)
    uow.commits = 0

    with pytest.raises(QuotaExceededError) as excinfo:
        use_cases.enforce(scope, EPISODE_CREATE)

    assert excinfo.value.details["resource_kind"] == ResourceKind.EPISODES.value
    assert excinfo.value.details["limit"] == 10
    assert excinfo.value.details["used"] == 10
    assert excinfo.value.details["requested"] == 1
    # 拒绝必须先落审计再抛异常；否则请求回滚后这条记录就消失了。
    assert uow.commits == 1
    assert recorder.events[-1].event_type == GOVERNANCE_QUOTA_EXCEEDED_EVENT
    assert recorder.records[-1].result == "FAILURE"


def test_enforce_counts_bytes_for_storage_quota() -> None:
    use_cases, _quotas, _reader, _snapshots, _recorder, _uow = _build(usage=FakeUsageReader(90))
    scope = _scope()
    use_cases.set_quota(
        scope,
        ResourceKind.OBJECT_STORAGE_BYTES,
        100,
        expected_version=None,
    )

    with pytest.raises(QuotaExceededError):
        use_cases.enforce(
            scope,
            OBJECT_STORAGE_UPLOAD,
            quantity=11,
        )


def test_enforce_fails_closed_when_measurement_unavailable() -> None:
    use_cases, _quotas, _reader, _snapshots, recorder, uow = _build(
        usage=FakeUsageReader(error=QuotaMeasurementUnavailableError(ResourceKind.EPISODES))
    )
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 10, expected_version=None)
    uow.commits = 0

    with pytest.raises(QuotaMeasurementUnavailableError):
        use_cases.enforce(scope, EPISODE_CREATE)

    # 测量失败时不写拒绝审计（没有可信的 used/limit 对照），也不放行。
    assert uow.commits == 0


def test_enforce_skips_when_request_is_idempotent_replay() -> None:
    """重放不消耗配额：即使此刻已到上限，也必须返回原本成功的结果。"""
    replays = FakeReplayDetector(replay=True)
    use_cases, _quotas, _reader, _snapshots, recorder, uow = _build(
        usage=FakeUsageReader(10),
        replays=replays,
    )
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.EPISODES, 10, expected_version=None)
    uow.commits = 0

    use_cases.enforce(
        scope,
        EPISODE_CREATE,
        idempotency_key="idem-key-0001",
        method="POST",
        path="/api/v1/projects/x/episodes",
    )

    assert uow.commits == 0
    assert len(recorder.records) == 1  # 只有创建配额那条，没有拒绝审计
    assert replays.calls == [("idem-key-0001", "POST", "/api/v1/projects/x/episodes")]


def test_enforce_rejects_unknown_operation() -> None:
    use_cases, _quotas, _reader, _snapshots, _recorder, _uow = _build()
    with pytest.raises(DomainValidationError):
        use_cases.enforce(_scope(), "unknown.operation")


def test_enforce_rejects_non_positive_quantity() -> None:
    use_cases, _quotas, _reader, _snapshots, _recorder, _uow = _build()
    scope = _scope()
    use_cases.set_quota(scope, ResourceKind.INGESTION_JOBS, 10, expected_version=None)
    with pytest.raises(DomainValidationError):
        use_cases.enforce(scope, INGESTION_JOB_CREATE, quantity=0)


# --- 快照刷新 --------------------------------------------------------------
def test_snapshot_refresh_replaces_all_rows_and_commits() -> None:
    project_a, project_b = uuid4(), uuid4()
    reader = FakeUsageReader(
        all_values={
            project_a: {ResourceKind.EPISODES: 3, ResourceKind.DATASET_VERSIONS: 1},
            project_b: {ResourceKind.EPISODES: 0},
        }
    )
    snapshots = FakeSnapshotRepository()
    uow = FakeUnitOfWork()
    use_cases = SnapshotUseCases(usage=reader, snapshots=snapshots, uow=uow)

    count = use_cases.refresh(now=NOW)

    assert count == 3
    assert uow.commits == 1
    assert reader.measured_all_at == NOW
    assert snapshots.store[project_a][ResourceKind.EPISODES].value == 3
    # 真实 0 必须落库，而不是被当成"没数据"丢掉。
    assert snapshots.store[project_b][ResourceKind.EPISODES].value == 0
