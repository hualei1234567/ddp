"""RawAsset 校验 Worker 集成测试（真实 SQLite + 显式 Fake S3）。

覆盖：成功、Hash 不符、大小不符、对象缺失、重试、死信、重复投递、
审计失败回滚与项目隔离。Fake S3 不替代 MinIO/S3 真实集成验收。
"""

from __future__ import annotations

import hashlib
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
) -> UUID:
    project = project_id or asset.project_id
    actor = actor_user_id or harness.user_a
    event_id = uuid4()
    with harness.factory() as session:
        SqlOutboxRepository(session).enqueue(
            ProjectScope(project, actor_id=actor),
            OutboxEventRecord(
                event_id=event_id,
                project_id=project,
                aggregate_type="RAW_ASSET",
                aggregate_id=asset.asset_id,
                event_type=VALIDATION_EVENT_TYPE,
                payload={
                    "asset_id": str(asset.asset_id),
                    "actor_user_id": str(actor),
                    "validation_job_id": str(validation_job_id or uuid4()),
                },
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
