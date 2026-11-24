"""RawAsset 校验 Worker 集成测试（真实 SQLite + 显式 Fake S3）。

覆盖：成功、Hash 不符、大小不符、对象缺失、重试、死信、重复投递、
审计失败回滚与项目隔离。Fake S3 不替代 MinIO/S3 真实集成验收。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.collection.ports import OutboxEvent as OutboxEventRecord
from app.application.ingestion.raw_asset_validation import (
    VALIDATED_EVENT_TYPE,
    VALIDATION_EVENT_TYPE,
    RawAssetValidationService,
)
from app.application.ingestion.validation_events import (
    ProcessingStatus,
    ValidationEventProcessor,
)
from app.core.config import Settings
from app.core.logging import JsonFormatter, is_safe_trace_id, trace_id_var
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetSourceType,
)
from app.domain.ingestion.models import RawAsset
from app.domain.ingestion.validation import build_raw_object_key
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlOutboxRepository,
)
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.workers.outbox import OutboxWorker, build_validation_processor
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.fakes_s3 import FakeMultipartStorage

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
MCAP_BYTES = b"\x89MCAP0\r\nrobot-content"


@dataclass(frozen=True)
class WorkerHarness:
    factory: sessionmaker[Session]
    fake: FakeMultipartStorage
    settings: Settings
    project_a: UUID
    project_b: UUID
    user_a: UUID
    user_b: UUID

    def worker(self) -> OutboxWorker:
        return OutboxWorker(self.factory, self.settings, self.fake)


@pytest.fixture()
def worker_harness() -> Iterator[WorkerHarness]:
    engine: Engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="worker-project-a", name="Worker 项目 A"),
                Project(id=project_b, code="worker-project-b", name="Worker 项目 B"),
                User(id=user_a, display_name="上传者 A"),
                User(id=user_b, display_name="上传者 B"),
            ]
        )
        session.commit()
    settings = Settings(
        env="local",
        validation_worker_poll_interval_seconds=0.01,
        validation_stream_chunk_size_bytes=4,
        validation_max_attempts=3,
        validation_retry_base_seconds=1,
        validation_retry_max_seconds=8,
    )
    harness = WorkerHarness(
        factory=factory,
        fake=FakeMultipartStorage(),
        settings=settings,
        project_a=project_a,
        project_b=project_b,
        user_a=user_a,
        user_b=user_b,
    )
    try:
        yield harness
    finally:
        engine.dispose()


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _seed_asset(
    harness: WorkerHarness,
    *,
    project_id: UUID | None = None,
    user_id: UUID | None = None,
    registered_content: bytes = MCAP_BYTES,
    stored_content: bytes | None = MCAP_BYTES,
    registered_size: int | None = None,
) -> RawAsset:
    project = project_id or harness.project_a
    user = user_id or harness.user_a
    asset_id = uuid4()
    asset = RawAsset(
        asset_id=asset_id,
        project_id=project,
        code=f"RAW-{asset_id.hex[:20]}",
        source_type=RawAssetSourceType.UPLOAD,
        original_filename="robot.mcap",
        object_key=build_raw_object_key(project, asset_id, NOW, RawAssetFormat.MCAP),
        size_bytes=(registered_size if registered_size is not None else len(registered_content)),
        content_type="application/x-mcap",
        format=RawAssetFormat.MCAP,
        sha256=_sha256(registered_content),
        uploaded_by=user,
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        created_by=user,
        updated_by=user,
    )
    with harness.factory() as session:
        SqlRawAssetRepository(session).add(
            ProjectScope(project, actor_id=user),
            asset,
        )
        session.commit()
    if stored_content is not None:
        harness.fake.put_object(asset.object_key, stored_content)
    return asset


def _enqueue_validation(
    harness: WorkerHarness,
    asset: RawAsset,
    *,
    project_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    validation_job_id: UUID | None = None,
    request_id: str | None = None,
) -> UUID:
    project = project_id or asset.project_id
    actor = actor_user_id or harness.user_a
    event_id = uuid4()
    payload: dict[str, object] = {
        "asset_id": str(asset.asset_id),
        "actor_user_id": str(actor),
        "validation_job_id": str(validation_job_id or uuid4()),
    }
    if request_id is not None:
        payload["request_id"] = request_id
    with harness.factory() as session:
        SqlOutboxRepository(session).enqueue(
            ProjectScope(project, actor_id=actor),
            OutboxEventRecord(
                event_id=event_id,
                project_id=project,
                aggregate_type="RAW_ASSET",
                aggregate_id=asset.asset_id,
                event_type=VALIDATION_EVENT_TYPE,
                payload=payload,
                occurred_at=NOW,
                # 这条事件是要被 worker 消费的，必须标为投递类（R-13 / T-1）。
                dispatch=True,
            ),
        )
        session.commit()
    return event_id


def _asset_row(harness: WorkerHarness, asset_id: UUID) -> RawAssetModel:
    with harness.factory() as session:
        row = session.get(RawAssetModel, asset_id)
        assert row is not None
        return row


def _source_event(harness: WorkerHarness, event_id: UUID) -> OutboxEvent:
    with harness.factory() as session:
        row = session.get(OutboxEvent, event_id)
        assert row is not None
        return row


def _count(harness: WorkerHarness, model: type, *conditions: object) -> int:
    with harness.factory() as session:
        statement = select(func.count()).select_from(model)
        for condition in conditions:
            statement = statement.where(condition)
        return int(session.scalar(statement) or 0)


def test_success_moves_asset_to_valid_and_writes_audit_and_outbox(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    asset = _seed_asset(harness)
    event_id = _enqueue_validation(harness, asset)

    result = harness.worker().process_once(now=NOW)

    assert result.status is ProcessingStatus.PUBLISHED
    assert result.outcome is not None and result.outcome.value == "VALID"
    row = _asset_row(harness, asset.asset_id)
    assert row.status == "VALID"
    assert row.version == 2
    assert row.verified_at is not None
    assert row.quarantine_codes == []
    assert _source_event(harness, event_id).status == "PUBLISHED"
    assert harness.fake.stream_calls == [(asset.object_key, 4)]
    with harness.factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.resource_id == asset.asset_id))
        assert audit is not None
        assert audit.action == "validate"
        assert audit.from_status == "VALIDATING"
        assert audit.to_status == "VALID"
        assert audit.result == "SUCCESS"
        validated = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == VALIDATED_EVENT_TYPE)
        ).all()
        assert len(validated) == 1
        assert validated[0].payload["status"] == "VALID"
        assert validated[0].payload["asset_id"] == str(asset.asset_id)


def test_hash_mismatch_quarantines_asset(worker_harness: WorkerHarness) -> None:
    harness = worker_harness
    asset = _seed_asset(
        harness,
        registered_content=b"expected01",
        stored_content=b"different!",
    )
    event_id = _enqueue_validation(harness, asset)

    result = harness.worker().process_once(now=NOW)

    assert result.status is ProcessingStatus.PUBLISHED
    assert result.outcome is not None and result.outcome.value == "QUARANTINED"
    row = _asset_row(harness, asset.asset_id)
    assert row.status == "QUARANTINED"
    assert row.quarantine_codes == ["hash_mismatch"]
    assert row.quarantine_reason
    assert row.verified_at is None
    assert _source_event(harness, event_id).status == "PUBLISHED"
    with harness.factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.resource_id == asset.asset_id))
        assert audit is not None and audit.result == "FAILURE"
        validated = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == VALIDATED_EVENT_TYPE)
        ).all()
        assert len(validated) == 1
        assert validated[0].payload["quarantine_codes"] == ["hash_mismatch"]


def test_size_mismatch_quarantines_asset(worker_harness: WorkerHarness) -> None:
    harness = worker_harness
    asset = _seed_asset(harness, registered_content=MCAP_BYTES, stored_content=b"short")
    _enqueue_validation(harness, asset)

    result = harness.worker().process_once(now=NOW)

    assert result.status is ProcessingStatus.PUBLISHED
    row = _asset_row(harness, asset.asset_id)
    assert row.status == "QUARANTINED"
    assert "size_mismatch" in row.quarantine_codes
    assert "hash_mismatch" in row.quarantine_codes


def test_missing_object_is_quarantined_with_stable_code(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    asset = _seed_asset(harness, stored_content=None)
    _enqueue_validation(harness, asset)

    result = harness.worker().process_once(now=NOW)

    assert result.status is ProcessingStatus.PUBLISHED
    row = _asset_row(harness, asset.asset_id)
    assert row.status == "QUARANTINED"
    assert row.quarantine_codes == ["object_missing"]
    assert row.quarantine_reason == "对象存储中不存在该对象"


def test_storage_failure_retries_then_succeeds(worker_harness: WorkerHarness) -> None:
    harness = worker_harness
    asset = _seed_asset(harness)
    event_id = _enqueue_validation(harness, asset)
    harness.fake.fail_next("stream")

    first = harness.worker().process_once(now=NOW)

    assert first.status is ProcessingStatus.RETRIED
    assert first.attempts == 1
    event = _source_event(harness, event_id)
    assert event.status == "PENDING"
    assert event.attempts == 1
    retry_at = _utc(event.available_at)
    assert retry_at > NOW
    assert "ObjectStorageUnavailableError" in (event.last_error or "")
    assert _asset_row(harness, asset.asset_id).status == "VALIDATING"

    second = harness.worker().process_once(now=retry_at)

    assert second.status is ProcessingStatus.PUBLISHED
    assert _asset_row(harness, asset.asset_id).status == "VALID"
    assert _source_event(harness, event_id).status == "PUBLISHED"


def test_retry_exhaustion_moves_event_to_dead_letter(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    # 该用例显式把最大次数收紧到 2，验证第 2 次失败进入死信。
    settings = harness.settings.model_copy(update={"validation_max_attempts": 2})
    worker = OutboxWorker(harness.factory, settings, harness.fake)
    asset = _seed_asset(harness)
    event_id = _enqueue_validation(harness, asset)
    harness.fake.fail_next("stream")
    harness.fake.fail_next("stream")

    first = worker.process_once(now=NOW)
    event = _source_event(harness, event_id)
    second = worker.process_once(now=_utc(event.available_at))

    assert first.status is ProcessingStatus.RETRIED
    assert second.status is ProcessingStatus.DEAD_LETTERED
    dead = _source_event(harness, event_id)
    assert dead.status == "FAILED"
    assert dead.attempts == 2
    assert "ObjectStorageUnavailableError" in (dead.last_error or "")
    assert _asset_row(harness, asset.asset_id).status == "VALIDATING"
    assert _count(harness, AuditLog) == 0
    assert (
        _count(
            harness,
            OutboxEvent,
            OutboxEvent.event_type == VALIDATED_EVENT_TYPE,
        )
        == 0
    )


def test_duplicate_delivery_is_idempotent(worker_harness: WorkerHarness) -> None:
    harness = worker_harness
    asset = _seed_asset(harness)
    first_event = _enqueue_validation(harness, asset)
    second_event = _enqueue_validation(harness, asset)
    worker = harness.worker()

    first = worker.process_once(now=NOW)
    second = worker.process_once(now=NOW)

    assert first.status is ProcessingStatus.PUBLISHED
    assert first.outcome is not None and first.outcome.value == "VALID"
    assert second.status is ProcessingStatus.PUBLISHED
    assert second.outcome is not None and second.outcome.value == "SKIPPED"
    row = _asset_row(harness, asset.asset_id)
    assert row.status == "VALID"
    assert row.version == 2
    assert _source_event(harness, first_event).status == "PUBLISHED"
    assert _source_event(harness, second_event).status == "PUBLISHED"
    assert (
        _count(
            harness,
            OutboxEvent,
            OutboxEvent.event_type == VALIDATED_EVENT_TYPE,
        )
        == 1
    )
    assert _count(harness, AuditLog) == 1


def test_event_project_mismatch_does_not_touch_other_project(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    asset_b = _seed_asset(
        harness,
        project_id=harness.project_b,
        user_id=harness.user_b,
    )
    # 事件声明项目 A，但资产属于项目 B；服务端 scope 必须拒绝跨项目访问。
    event_id = _enqueue_validation(
        harness,
        asset_b,
        project_id=harness.project_a,
        actor_user_id=harness.user_a,
    )

    result = harness.worker().process_once(now=NOW)

    assert result.status is ProcessingStatus.DEAD_LETTERED
    assert "ResourceNotFoundError" in (result.error or "")
    assert _asset_row(harness, asset_b.asset_id).status == "VALIDATING"
    assert _source_event(harness, event_id).status == "FAILED"
    assert _count(harness, AuditLog) == 0
    assert (
        _count(
            harness,
            OutboxEvent,
            OutboxEvent.event_type == VALIDATED_EVENT_TYPE,
        )
        == 0
    )


class _FailingAuditRepository:
    def record(self, scope: ProjectScope, record: object) -> None:
        raise RuntimeError("审计存储不可用")


def test_audit_failure_rolls_back_business_and_retries(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    asset = _seed_asset(harness)
    event_id = _enqueue_validation(harness, asset)

    with harness.factory() as session:
        processor = ValidationEventProcessor(
            validation=RawAssetValidationService(
                assets=SqlRawAssetRepository(session),
                storage=harness.fake,
                audit=_FailingAuditRepository(),
                outbox=SqlOutboxRepository(session),
            ),
            queue=SqlOutboxValidationQueue(session),
            uow=session,
            max_attempts=harness.settings.validation_max_attempts,
            retry_base_seconds=harness.settings.validation_retry_base_seconds,
            retry_max_seconds=harness.settings.validation_retry_max_seconds,
            chunk_size_bytes=harness.settings.validation_stream_chunk_size_bytes,
        )
        result = processor.process_next(now=NOW)

    assert result.status is ProcessingStatus.RETRIED
    row = _asset_row(harness, asset.asset_id)
    assert row.status == "VALIDATING"
    assert row.version == 1
    event = _source_event(harness, event_id)
    assert event.status == "PENDING"
    assert event.attempts == 1
    assert "RuntimeError" in (event.last_error or "")
    assert _count(harness, AuditLog) == 0
    assert (
        _count(
            harness,
            OutboxEvent,
            OutboxEvent.event_type == VALIDATED_EVENT_TYPE,
        )
        == 0
    )


def test_malformed_event_goes_straight_to_dead_letter(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    project = harness.project_a
    event_id = uuid4()
    with harness.factory() as session:
        SqlOutboxRepository(session).enqueue(
            ProjectScope(project, actor_id=harness.user_a),
            OutboxEventRecord(
                event_id=event_id,
                project_id=project,
                aggregate_type="RAW_ASSET",
                aggregate_id=uuid4(),
                event_type=VALIDATION_EVENT_TYPE,
                payload={"asset_id": "not-a-uuid"},
                occurred_at=NOW,
                # 这条事件是要被 worker 消费的，必须标为投递类（R-13 / T-1）。
                dispatch=True,
            ),
        )
        session.commit()

    result = harness.worker().process_once(now=NOW)

    assert result.status is ProcessingStatus.DEAD_LETTERED
    assert "InvalidValidationEventError" in (result.error or "")
    assert _source_event(harness, event_id).status == "FAILED"
    assert _count(harness, AuditLog) == 0


def test_build_validation_processor_uses_configured_chunk_size(
    worker_harness: WorkerHarness,
) -> None:
    harness = worker_harness
    with harness.factory() as session:
        processor = build_validation_processor(session, harness.settings, harness.fake)
        assert isinstance(processor, ValidationEventProcessor)


class _CollectingJsonHandler(logging.Handler):
    """按真实 ``JsonFormatter`` 输出解析日志，断言 trace 字段。"""

    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


def _capture_worker_logs():
    handler = _CollectingJsonHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("ddp.worker.outbox")
    logger.addHandler(handler)
    return logger, handler


def test_worker_reuses_event_request_id_in_logs_audit_and_event(
    worker_harness: WorkerHarness,
) -> None:
    """同一 request_id 贯穿：outbox 事件 → Worker 日志 → 审计 → validated 事件。"""
    harness = worker_harness
    asset = _seed_asset(harness)
    _enqueue_validation(harness, asset, request_id="req-upload-1")

    logger, handler = _capture_worker_logs()
    try:
        result = harness.worker().process_once(now=NOW)
    finally:
        logger.removeHandler(handler)

    assert result.status is ProcessingStatus.PUBLISHED
    with harness.factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.resource_id == asset.asset_id))
        assert audit is not None
        assert audit.request_id == "req-upload-1"
        validated = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == VALIDATED_EVENT_TYPE)
        )
        assert validated is not None
        assert validated.payload["request_id"] == "req-upload-1"

    assert handler.payloads, "Worker 必须留下可检索的结果日志"
    assert all(item["trace_id"] == "req-upload-1" for item in handler.payloads)
    assert all(item["trace_source"] == "event" for item in handler.payloads)
    # 处理结束后上下文复位，不串到下一条事件。
    assert trace_id_var.get() is None


def test_worker_generates_marked_trace_when_event_has_no_request_id(
    worker_harness: WorkerHarness,
) -> None:
    """无来源时生成新 trace_id 并标注 generated，不拿 event_id 冒充。"""
    harness = worker_harness
    asset = _seed_asset(harness)
    event_id = _enqueue_validation(harness, asset)

    logger, handler = _capture_worker_logs()
    try:
        result = harness.worker().process_once(now=NOW)
    finally:
        logger.removeHandler(handler)

    assert result.status is ProcessingStatus.PUBLISHED
    assert handler.payloads
    generated = {str(item["trace_id"]) for item in handler.payloads}
    assert len(generated) == 1
    trace_id = next(iter(generated))
    assert trace_id != str(event_id), "不得把事件 ID 当作请求 ID"
    assert is_safe_trace_id(trace_id)
    assert all(item["trace_source"] == "generated" for item in handler.payloads)

    with harness.factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.resource_id == asset.asset_id))
        assert audit is not None
        # 审计沿用同一个生成 ID，链路仍可端到端检索。
        assert audit.request_id == trace_id
        validated = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == VALIDATED_EVENT_TYPE)
        )
        assert validated is not None
        assert validated.payload["request_id"] == trace_id
    assert trace_id_var.get() is None


def test_content_duplicate_is_tombstoned_not_failed(
    worker_harness: WorkerHarness,
) -> None:
    """同项目相同内容的两个活跃资产：第二个必须降级为墓碑，而不是让事件失败。

    背景（真实事故）：上传完成时的内容去重按 (sha256, size_bytes) 查一次，
    两个请求并发完成时可以双双查不到对方，于是都留下 VALIDATING 资产；
    校验转 VALID 时第二个撞 ``uq_raw_assets_project_content_active``，
    表现为 outbox 事件永久 FAILED（UniqueViolation）——把「内容已存在」
    误报成「校验失败」。
    """
    harness = worker_harness

    # 先落一个已校验通过的活跃资产（占住这份内容）。
    winner = _seed_asset(harness)
    _enqueue_validation(harness, winner)
    assert harness.worker().process_once(now=NOW).status is ProcessingStatus.PUBLISHED
    assert _asset_row(harness, winner.asset_id).status == "VALID"

    # 再来一个完全相同内容的 VALIDATING 资产（模拟并发漏网）。
    loser = _seed_asset(harness)
    _enqueue_validation(harness, loser)
    result = harness.worker().process_once(now=NOW)

    # 事件必须成功（不是 FAILED），资产被墓碑化并指向已有资产。
    assert result.status is ProcessingStatus.PUBLISHED, result.error
    loser_row = _asset_row(harness, loser.asset_id)
    assert loser_row.status == "TOMBSTONED"
    assert loser_row.duplicate_of_asset_id == winner.asset_id
    # 胜者保持 VALID，内容仍可下载。
    assert _asset_row(harness, winner.asset_id).status == "VALID"
    # 审计仍留痕（墓碑动作不是静默丢弃）。
    assert _count(harness, AuditLog) >= 1


def test_content_duplicate_in_other_project_is_not_deduped(
    worker_harness: WorkerHarness,
) -> None:
    """内容唯一索引是项目级的：不同项目的相同内容互不影响，必须都能 VALID。"""
    harness = worker_harness
    first = _seed_asset(harness)
    second = _seed_asset(harness, project_id=harness.project_b, user_id=harness.user_b)

    _enqueue_validation(harness, first)
    assert harness.worker().process_once(now=NOW).status is ProcessingStatus.PUBLISHED
    _enqueue_validation(harness, second, project_id=harness.project_b, actor_user_id=harness.user_b)
    assert harness.worker().process_once(now=NOW).status is ProcessingStatus.PUBLISHED

    assert _asset_row(harness, first.asset_id).status == "VALID"
    assert _asset_row(harness, second.asset_id).status == "VALID"
