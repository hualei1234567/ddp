"""T2-02 Worker 并发争用、租约接管、退避重试与死信故障注入回归测试。

使用内存 SQLite（``api_db``）完全隔离外部容器，覆盖两个核心异步 Worker：
- ``ProcessingRunWorker``（``processing.run.requested``）
- ``ExportWorker``（``dataset.export.requested``）

验证三类容错不变量：
1. 并发领取同一事件时单次仅一个 Worker 成功领取并执行（幂等不重跑）；
2. 执行中途崩溃（抛瞬时异常或落租约后硬崩溃）后按指数退避重投或租约到期由第二个 Worker 接管恢复；
3. 达到 ``max_attempts`` 上限后准确转入死信（``FAILED``），不再无限重试。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.use_cases import (
    PROCESSING_RUN_REQUESTED_EVENT,
    ProcessingRunUseCases,
)
from app.core.config import Settings
from app.domain.datasets.errors import ConflictError as ExportConflictError
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import STAGE_SEQUENCE, ProcessingRunStatus
from app.infrastructure.db.models.collection import OutboxEvent
from app.infrastructure.repositories._collection_sql import as_utc
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.export_jobs import SqlExportJobRepository
from app.infrastructure.repositories.export_queue import SqlExportQueue
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.workers import exports as exports_worker_module
from app.workers.exports import ExportProcessingStatus, ExportWorker
from app.workers.processing import (
    RUN_REQUESTED_EVENT,
    ProcessingRunWorker,
    RunProcessingStatus,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.export_support import (
    FakeExportStorage,
    build_frozen_version,
    export_payload,
)
from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader


def _now() -> datetime:
    return datetime.now(UTC)


def _processing_settings(
    *,
    max_attempts: int = 3,
    retry_base_seconds: float = 5.0,
    retry_max_seconds: float = 30.0,
    lease_seconds: float = 60.0,
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        validation_max_attempts=max_attempts,
        validation_retry_base_seconds=retry_base_seconds,
        validation_retry_max_seconds=retry_max_seconds,
        processing_event_lease_seconds=lease_seconds,
    )


def _export_settings(
    *,
    max_attempts: int = 3,
    retry_base_seconds: float = 5.0,
    retry_max_seconds: float = 20.0,
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        env="local",
        export_worker_max_attempts=max_attempts,
        export_worker_retry_base_seconds=retry_base_seconds,
        export_worker_retry_max_seconds=retry_max_seconds,
    )


def _create_processing_run(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    input_refs: tuple[str, ...] | None = None,
) -> UUID:
    with factory() as session:
        use_cases = ProcessingRunUseCases(
            SqlProcessingRunRepository(session),
            SqlProcessingRunStepRepository(session),
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            raw_assets=SqlRawAssetRepository(session),
        )
        run = use_cases.create(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=input_refs or (f"raw/{ids.project_a}/fault-test.bin",),
            ),
        )
        return run.run_id


def _get_processing_run(factory: sessionmaker[Session], ids: ApiIds, run_id: UUID):
    with factory() as session:
        return SqlProcessingRunRepository(session).get(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            run_id,
        )


def _get_processing_steps(factory: sessionmaker[Session], ids: ApiIds, run_id: UUID):
    with factory() as session:
        return SqlProcessingRunStepRepository(session).list(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            run_id=run_id,
            limit=100,
        )


def _get_outbox_event(factory: sessionmaker[Session], event_type: str) -> OutboxEvent:
    with factory() as session:
        row = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type).limit(1)
        )
        assert row is not None
        session.expunge(row)
        return row


def _create_export_job(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    storage: FakeExportStorage,
    idempotency_key: str,
) -> UUID:
    ctx = build_frozen_version(factory, ids)
    client = make_client(factory, ids.editor_a, object_storage=storage)
    response = client.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 202, response.text
    return UUID(response.json()["export_job_id"])


def _get_export_job(factory: sessionmaker[Session], ids: ApiIds, job_id: UUID):
    with factory() as session:
        return SqlExportJobRepository(session).get(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            job_id,
        )


class _FaultInjectingObjectReader(FakeObjectReader):
    """在首次检查对象大小时触发回调（用于模拟并发竞争）。"""

    def __init__(
        self,
        objects: dict[str, bytes],
        *,
        on_object_size=None,
    ) -> None:
        super().__init__(objects)
        self._on_object_size = on_object_size

    def object_size(self, object_key: str) -> int | None:
        if self._on_object_size is not None:
            callback = self._on_object_size
            self._on_object_size = None
            callback()
        return super().object_size(object_key)


# ---------------------------------------------------------------------------
# ProcessingRunWorker（processing.run.requested）并发争用与故障注入
# ---------------------------------------------------------------------------


def test_processing_concurrent_claim_is_idempotent_under_active_lease(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """并发领取同一 processing.run.requested 事件：租约期内仅一个 Worker 执行，不重跑。"""
    factory, ids = api_db
    ref = f"raw/{ids.project_a}/concurrent.bin"
    run_id = _create_processing_run(factory, ids, input_refs=(ref,))
    settings = _processing_settings(lease_seconds=60.0)
    moment = _now()

    competitor_reader = _FaultInjectingObjectReader({ref: b"payload-bytes"})
    worker_b = ProcessingRunWorker(
        factory,
        settings,
        inspector=competitor_reader,
        reader=competitor_reader,
        artifacts=FakeArtifactStore(),
    )
    competitor_results: list[dict[str, object]] = []

    def _contend_mid_flight() -> None:
        # Worker A 已完成 mark_claimed + commit 并在执行 pipeline 中途，
        # 此时 Worker B 并发争抢同一事件，必须被租约挡住并返回 IDLE。
        competitor_results.append(worker_b.process_once(now=moment + timedelta(seconds=1)))

    primary_reader = _FaultInjectingObjectReader(
        {ref: b"payload-bytes"},
        on_object_size=_contend_mid_flight,
    )
    worker_a = ProcessingRunWorker(
        factory,
        settings,
        inspector=primary_reader,
        reader=primary_reader,
        artifacts=FakeArtifactStore(),
    )

    result_a = worker_a.process_once(now=moment)

    assert result_a["status"] == RunProcessingStatus.PUBLISHED.value
    assert competitor_results == [{"status": RunProcessingStatus.IDLE.value}]
    assert competitor_reader.size_calls == []
    assert len(primary_reader.size_calls) >= 1

    run = _get_processing_run(factory, ids, run_id)
    assert run is not None
    assert run.status == ProcessingRunStatus.SUCCEEDED.value
    assert len(_get_processing_steps(factory, ids, run_id)) == len(STAGE_SEQUENCE)

    # 再次轮询确认事件已完结，不会被重复领取
    assert worker_b.process_once(now=moment + timedelta(seconds=120)) == {
        "status": RunProcessingStatus.IDLE.value
    }


def test_processing_worker_crash_retries_with_backoff_then_second_worker_recovers(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker 1 执行中途抛瞬时异常崩溃后退避重投，退避到期由 Worker 2 接管成功恢复。"""
    factory, ids = api_db
    ref = f"raw/{ids.project_a}/crash-recover.bin"
    run_id = _create_processing_run(factory, ids, input_refs=(ref,))
    settings = _processing_settings(
        max_attempts=3,
        retry_base_seconds=5.0,
        retry_max_seconds=30.0,
    )

    # Worker 1 在构建/执行 pipeline 时模拟未预期崩溃
    healthy_reader = FakeObjectReader({ref: b"valid-payload"})
    worker_1 = ProcessingRunWorker(
        factory,
        settings,
        inspector=healthy_reader,
        reader=healthy_reader,
        artifacts=FakeArtifactStore(),
    )
    worker_2 = ProcessingRunWorker(
        factory,
        settings,
        inspector=healthy_reader,
        reader=healthy_reader,
        artifacts=FakeArtifactStore(),
    )

    class _CrashingPipeline:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("worker-1 mid-flight crash")

    monkeypatch.setattr(worker_1, "_build_pipeline", lambda _session: _CrashingPipeline())

    t0 = _now()
    first = worker_1.process_once(now=t0)

    assert first["status"] == RunProcessingStatus.RETRIED.value
    assert first["attempts"] == 1
    assert first["released_for_retry"] is True

    retrying_run = _get_processing_run(factory, ids, run_id)
    assert retrying_run is not None
    assert retrying_run.status == ProcessingRunStatus.RETRYING.value

    event = _get_outbox_event(factory, PROCESSING_RUN_REQUESTED_EVENT)
    assert event.status == "PENDING"
    assert event.attempts == 1
    assert as_utc(event.available_at) == t0 + timedelta(seconds=5)
    assert "worker-1 mid-flight crash" in (event.last_error or "")

    # 退避窗口内（t0 + 4s），Worker 2 不得提前接管
    assert worker_2.process_once(now=t0 + timedelta(seconds=4)) == {
        "status": RunProcessingStatus.IDLE.value
    }

    # 退避到期（t0 + 5s），Worker 2 接管并成功恢复执行
    recovered = worker_2.process_once(now=t0 + timedelta(seconds=5))
    assert recovered["status"] == RunProcessingStatus.PUBLISHED.value
    assert recovered["run_id"] == str(run_id)

    final_run = _get_processing_run(factory, ids, run_id)
    assert final_run is not None
    assert final_run.status == ProcessingRunStatus.SUCCEEDED.value
    final_event = _get_outbox_event(factory, PROCESSING_RUN_REQUESTED_EVENT)
    assert final_event.status == "PUBLISHED"


def test_processing_hard_crash_after_lease_recovers_once_lease_expires(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """Worker 1 落租约后硬崩溃，租约到期后由 Worker 2 接管恢复。"""
    factory, ids = api_db
    ref = f"raw/{ids.project_a}/lease-expiry.bin"
    run_id = _create_processing_run(factory, ids, input_refs=(ref,))
    lease_seconds = 30.0
    settings = _processing_settings(lease_seconds=lease_seconds)
    t0 = _now()

    # 模拟 Worker 1 领取事件并提交租约后直接进程崩溃（无后续 DB 写入）
    with factory() as session:
        queue = SqlOutboxValidationQueue(session)
        claimed = queue.claim_next(now=t0, event_type=RUN_REQUESTED_EVENT)
        assert claimed is not None
        assert queue.mark_claimed(claimed.event_id, now=t0, lease_seconds=lease_seconds)
        session.commit()

    reader = FakeObjectReader({ref: b"recovered-after-lease"})
    worker_2 = ProcessingRunWorker(
        factory,
        settings,
        inspector=reader,
        reader=reader,
        artifacts=FakeArtifactStore(),
    )

    # 租约未到期前，Worker 2 始终保持 IDLE
    assert worker_2.process_once(now=t0 + timedelta(seconds=10)) == {
        "status": RunProcessingStatus.IDLE.value
    }
    assert worker_2.process_once(now=t0 + timedelta(seconds=29)) == {
        "status": RunProcessingStatus.IDLE.value
    }

    # 租约到期后，Worker 2 接管并成功完成全部阶段
    recovered = worker_2.process_once(now=t0 + timedelta(seconds=30))
    assert recovered["status"] == RunProcessingStatus.PUBLISHED.value
    assert recovered["run_id"] == str(run_id)

    run = _get_processing_run(factory, ids, run_id)
    assert run is not None
    assert run.status == ProcessingRunStatus.SUCCEEDED.value
    assert len(_get_processing_steps(factory, ids, run_id)) == len(STAGE_SEQUENCE)


def test_processing_exhausted_max_attempts_transitions_to_dead_letter(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """达到 validation_max_attempts 上限后准确转入死信并关闭运行，不再无限重试。"""
    factory, ids = api_db
    ref = f"raw/{ids.project_a}/dead-letter.bin"
    run_id = _create_processing_run(factory, ids, input_refs=(ref,))
    settings = _processing_settings(
        max_attempts=3,
        retry_base_seconds=4.0,
        retry_max_seconds=10.0,
    )
    reader = FakeObjectReader({ref: b"never-reaches-stage"})
    worker = ProcessingRunWorker(
        factory,
        settings,
        inspector=reader,
        reader=reader,
        artifacts=FakeArtifactStore(),
    )

    class _AlwaysCrashPipeline:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("persistent transient fault")

    monkeypatch.setattr(worker, "_build_pipeline", lambda _session: _AlwaysCrashPipeline())

    t0 = _now()
    # 第 1 次失败：退避 4 * 2^0 = 4s
    r1 = worker.process_once(now=t0)
    assert r1["status"] == RunProcessingStatus.RETRIED.value
    assert r1["attempts"] == 1
    assert as_utc(_get_outbox_event(factory, PROCESSING_RUN_REQUESTED_EVENT).available_at) == (
        t0 + timedelta(seconds=4)
    )

    # 第 2 次失败（t0 + 4s）：退避 4 * 2^1 = 8s
    t1 = t0 + timedelta(seconds=4)
    r2 = worker.process_once(now=t1)
    assert r2["status"] == RunProcessingStatus.RETRIED.value
    assert r2["attempts"] == 2
    assert as_utc(_get_outbox_event(factory, PROCESSING_RUN_REQUESTED_EVENT).available_at) == (
        t1 + timedelta(seconds=8)
    )

    # 第 3 次失败（t1 + 8s）：达到 max_attempts=3 → 死信
    t2 = t1 + timedelta(seconds=8)
    r3 = worker.process_once(now=t2)
    assert r3["status"] == RunProcessingStatus.DEAD_LETTER.value
    assert r3["attempts"] == 3
    assert r3["released_for_retry"] is False

    event = _get_outbox_event(factory, PROCESSING_RUN_REQUESTED_EVENT)
    assert event.status == "FAILED"
    assert event.attempts == 3
    assert "persistent transient fault" in (event.last_error or "")

    final_run = _get_processing_run(factory, ids, run_id)
    assert final_run is not None
    assert final_run.status == ProcessingRunStatus.FAILED.value
    assert final_run.closed is True
    assert final_run.is_terminal is True

    # 死信后无论多久再轮询，均返回 IDLE，不再无限重试
    assert worker.process_once(now=t2 + timedelta(hours=1)) == {
        "status": RunProcessingStatus.IDLE.value
    }


# ---------------------------------------------------------------------------
# ExportWorker（dataset.export.requested）并发争用与故障注入
# ---------------------------------------------------------------------------


def test_export_concurrent_claim_only_one_worker_publishes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """并发领取同一 dataset.export.requested 事件：仅一个 Worker 成功发布，重复发布被拒绝。"""
    factory, ids = api_db
    storage_a = FakeExportStorage()
    storage_b = FakeExportStorage()
    job_id = _create_export_job(
        factory,
        ids,
        storage=storage_a,
        idempotency_key="export-fault-concurrent",
    )
    settings = _export_settings()
    worker_a = ExportWorker(factory, settings, storage_a)
    worker_b = ExportWorker(factory, settings, storage_b)
    t0 = _now()

    # 模拟两个会话几乎同时看到同一条 PENDING 事件的竞争窗口
    with factory() as stale_session:
        stale_queue = SqlExportQueue(stale_session)
        stale_claimed = stale_queue.claim_next(now=t0, event_type="dataset.export.requested")
        assert stale_claimed is not None

        # Worker A 抢先完成整条导出并标记 PUBLISHED
        result_a = worker_a.process_once(now=t0)
        assert result_a.status is ExportProcessingStatus.PUBLISHED
        assert result_a.job_id == job_id

        # 持有陈旧快照的第二个会话尝试重复发布，必须被状态条件更新拒绝
        with pytest.raises(ExportConflictError):
            stale_queue.mark_published(stale_claimed.event_id, now=t0)
        stale_session.rollback()

    # Worker B 正常轮询必须为 IDLE，不重跑导出
    result_b = worker_b.process_once(now=t0)
    assert result_b.status is ExportProcessingStatus.IDLE
    assert storage_b.objects == {}
    assert len(storage_a.objects) > 0

    job = _get_export_job(factory, ids, job_id)
    assert job is not None
    assert job.status == "SUCCEEDED"
    assert job.attempt == 1


def test_export_worker_crash_retries_with_backoff_then_second_worker_recovers(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ExportWorker 执行中途崩溃抛瞬时异常后退避重投，退避到期后第二个 Worker 接管恢复成功。"""
    factory, ids = api_db
    storage = FakeExportStorage()
    job_id = _create_export_job(
        factory,
        ids,
        storage=storage,
        idempotency_key="export-fault-recover",
    )
    settings = _export_settings(
        max_attempts=3,
        retry_base_seconds=5.0,
        retry_max_seconds=20.0,
    )
    worker_1 = ExportWorker(factory, settings, storage)
    worker_2 = ExportWorker(factory, settings, storage)

    real_builder = exports_worker_module.build_execution_service
    crash_once = {"should_crash": True}

    def _flaky_builder(session: Session, cfg: Settings, store):
        if crash_once["should_crash"]:
            crash_once["should_crash"] = False
            raise RuntimeError("export worker transient crash")
        return real_builder(session, cfg, store)

    monkeypatch.setattr(exports_worker_module, "build_execution_service", _flaky_builder)

    t0 = _now()
    first = worker_1.process_once(now=t0)
    assert first.status is ExportProcessingStatus.RETRIED
    assert first.attempts == 1
    assert first.job_id == job_id
    assert "export worker transient crash" in (first.error or "")

    # 事务已回滚，ExportJob 保持 QUEUED，未产生脏产物
    queued_job = _get_export_job(factory, ids, job_id)
    assert queued_job is not None
    assert queued_job.status == "QUEUED"
    assert storage.objects == {}

    event = _get_outbox_event(factory, "dataset.export.requested")
    assert event.status == "PENDING"
    assert event.attempts == 1
    assert as_utc(event.available_at) == t0 + timedelta(seconds=5)

    # 退避窗口内，Worker 2 返回 IDLE
    early = worker_2.process_once(now=t0 + timedelta(seconds=4))
    assert early.status is ExportProcessingStatus.IDLE

    # 退避到期后，Worker 2 接管并成功导出
    recovered = worker_2.process_once(now=t0 + timedelta(seconds=5))
    assert recovered.status is ExportProcessingStatus.PUBLISHED
    assert recovered.job_id == job_id

    final_job = _get_export_job(factory, ids, job_id)
    assert final_job is not None
    assert final_job.status == "SUCCEEDED"
    assert len(storage.objects) > 0
    assert _get_outbox_event(factory, "dataset.export.requested").status == "PUBLISHED"


def test_export_worker_exhausted_max_attempts_transitions_to_dead_letter(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ExportWorker 持续故障达到 export_worker_max_attempts 后转入死信，不再无限重试。"""
    factory, ids = api_db
    storage = FakeExportStorage()
    job_id = _create_export_job(
        factory,
        ids,
        storage=storage,
        idempotency_key="export-fault-dead-letter",
    )
    settings = _export_settings(
        max_attempts=3,
        retry_base_seconds=3.0,
        retry_max_seconds=5.0,
    )
    worker = ExportWorker(factory, settings, storage)

    def _always_crash(*_args, **_kwargs):
        raise RuntimeError("export backend unreachable")

    monkeypatch.setattr(exports_worker_module, "build_execution_service", _always_crash)

    t0 = _now()
    # 第 1 次失败：退避 3 * 2^0 = 3s
    r1 = worker.process_once(now=t0)
    assert r1.status is ExportProcessingStatus.RETRIED
    assert r1.attempts == 1
    assert as_utc(_get_outbox_event(factory, "dataset.export.requested").available_at) == (
        t0 + timedelta(seconds=3)
    )

    # 第 2 次失败（t0 + 3s）：min(3 * 2^1, 5) = 5s（上限截断生效）
    t1 = t0 + timedelta(seconds=3)
    r2 = worker.process_once(now=t1)
    assert r2.status is ExportProcessingStatus.RETRIED
    assert r2.attempts == 2
    assert as_utc(_get_outbox_event(factory, "dataset.export.requested").available_at) == (
        t1 + timedelta(seconds=5)
    )

    # 第 3 次失败（t1 + 5s）：达到 max_attempts=3 → 死信
    t2 = t1 + timedelta(seconds=5)
    r3 = worker.process_once(now=t2)
    assert r3.status is ExportProcessingStatus.DEAD_LETTER
    assert r3.attempts == 3
    assert r3.job_id == job_id

    event = _get_outbox_event(factory, "dataset.export.requested")
    assert event.status == "FAILED"
    assert event.attempts == 3
    assert "export backend unreachable" in (event.last_error or "")

    # 死信后不会再次被领取
    assert worker.process_once(now=t2 + timedelta(hours=1)).status is ExportProcessingStatus.IDLE
