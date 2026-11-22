"""ProcessingRun 应用用例 + SQL 仓储测试（真实 SQLite，非 Mock）。"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingRunStatus, ProcessingStage
from app.domain.processing.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.processing.state_machines import first_stage
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _services(
    factory: sessionmaker[Session],
) -> tuple[ProcessingRunUseCases, ProcessingWorkerUseCases, Session]:
    session = factory()
    repository = SqlProcessingRunRepository(session)
    return (
        ProcessingRunUseCases(
            repository,
            SqlProcessingRunStepRepository(session),
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            raw_assets=SqlRawAssetRepository(session),
        ),
        ProcessingWorkerUseCases(
            repository,
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        ),
        session,
    )


def test_create_worker_lifecycle_and_list(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    runs, worker, session = _services(factory)
    try:
        run = runs.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=("raw/1",)),
        )
        assert run.status == ProcessingRunStatus.CREATED.value
        assert run.allowed_actions == frozenset({"cancel"})

        queued = worker.enqueue(scope, run.run_id, expected_version=run.row_version)
        assert queued.status == ProcessingRunStatus.QUEUED.value

        claimed = worker.claim(scope, run.run_id, expected_version=queued.row_version)
        assert claimed.status == ProcessingRunStatus.RUNNING.value
        assert claimed.stage == first_stage()
        assert claimed.attempt == 1

        advanced = worker.advance(
            scope,
            run.run_id,
            ProcessingStage.FORMAT_PARSING.value,
            expected_version=claimed.row_version,
            output_refs=("stage/format",),
        )
        assert advanced.stage == ProcessingStage.FORMAT_PARSING.value
        assert advanced.output_refs == ("stage/format",)

        failed = worker.fail(
            scope,
            run.run_id,
            expected_version=advanced.row_version,
            error_code="parse_error",
            error_summary="字段缺失",
        )
        assert failed.status == ProcessingRunStatus.FAILED.value
        assert failed.error_code == "parse_error"

        retrying = worker.retry(scope, run.run_id, expected_version=failed.row_version)
        assert retrying.status == ProcessingRunStatus.RETRYING.value
        assert retrying.retry_count == 1

        reclaimed = worker.claim(scope, run.run_id, expected_version=retrying.row_version)
        assert reclaimed.attempt == 2

        succeeded = worker.succeed(
            scope,
            run.run_id,
            expected_version=reclaimed.row_version,
            output_refs=("episodes/1",),
        )
        assert succeeded.status == ProcessingRunStatus.SUCCEEDED.value
        assert succeeded.is_terminal

        listed = runs.list(scope, status=ProcessingRunStatus.SUCCEEDED.value)
        assert [item.run_id for item in listed] == [run.run_id]
        assert runs.list(scope, status=ProcessingRunStatus.FAILED.value) == []
        assert runs.list(scope, stage=ProcessingStage.FORMAT_PARSING.value) == [succeeded]
    finally:
        session.close()


def test_cancel_and_optimistic_lock(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    runs, worker, session = _services(factory)
    try:
        run = runs.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=("raw/1",)),
        )
        queued = worker.enqueue(scope, run.run_id, expected_version=run.row_version)
        claimed = worker.claim(scope, run.run_id, expected_version=queued.row_version)

        with pytest.raises(VersionConflictError):
            runs.cancel(scope, run.run_id, expected_version=queued.row_version)

        cancelled = runs.cancel(scope, run.run_id, expected_version=claimed.row_version)
        assert cancelled.status == ProcessingRunStatus.CANCELLED.value
        assert cancelled.finished_at is not None

        with pytest.raises(InvalidStateTransitionError):
            runs.cancel(scope, run.run_id, expected_version=cancelled.row_version)
        with pytest.raises(DomainValidationError):
            runs.act(
                scope,
                run.run_id,
                "worker-succeed",
                expected_version=cancelled.row_version,
            )
    finally:
        session.close()


def test_cross_project_access_is_not_found(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope_a = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    scope_b = ProjectScope(project_id=ids.project_b, actor_id=ids.owner_a)
    runs, worker, session = _services(factory)
    try:
        run = runs.create(
            scope_a,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=("raw/1",)),
        )
        with pytest.raises(ResourceNotFoundError):
            runs.get(scope_b, run.run_id)
        with pytest.raises(ResourceNotFoundError):
            worker.enqueue(scope_b, run.run_id, expected_version=run.row_version)
        assert runs.list(scope_b) == []
    finally:
        session.close()


def test_repository_rejects_cross_project_scope(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope_b = ProjectScope(project_id=ids.project_b, actor_id=ids.owner_a)
    session = factory()
    repository = SqlProcessingRunRepository(session)
    try:
        from app.domain.processing.models import ProcessingRun

        run = ProcessingRun(
            run_id=uuid4(),
            project_id=ids.project_a,
            pipeline_version="pipeline-v1",
            input_refs=("raw/1",),
        )
        with pytest.raises(CrossProjectAccessError):
            repository.add(scope_b, run)
    finally:
        session.close()


def _audit_rows(session: Session, run_id) -> list[AuditLog]:
    return list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "PROCESSING_RUN",
                AuditLog.resource_id == run_id,
            )
            .order_by(AuditLog.occurred_at)
        ).all()
    )


def _one(rows: list[AuditLog], action: str) -> AuditLog:
    matches = [row for row in rows if row.action == action]
    assert len(matches) == 1, f"{action} 审计条数应为 1，实际 {len(matches)}"
    return matches[0]


def test_key_writes_write_audit_and_record_only_outbox(factory: sessionmaker[Session]) -> None:
    """create/cancel/fail/retry/system-close 都能回答谁/何时/前后状态/原因。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    runs, worker, session = _services(factory)
    try:
        run = runs.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=("raw/1",)),
            request_id="req-create",
        )
        audits = _audit_rows(session, run.run_id)
        created = _one(audits, "create")
        assert (created.from_status, created.to_status) == (None, "CREATED")
        assert created.actor_user_id == ids.editor_a
        assert created.request_id == "req-create"
        assert created.occurred_at is not None
        assert created.details["pipeline_version"] == "pipeline-v1"

        queued = worker.enqueue(scope, run.run_id, expected_version=run.row_version)
        claimed = worker.claim(scope, run.run_id, expected_version=queued.row_version)
        failed = worker.fail(
            scope,
            run.run_id,
            expected_version=claimed.row_version,
            error_code="parse_error",
            error_summary="字段缺失",
            request_id="req-fail",
        )
        retrying = worker.retry(
            scope,
            run.run_id,
            expected_version=failed.row_version,
            request_id="req-retry",
        )
        audits = _audit_rows(session, run.run_id)
        fail_row = _one(audits, "worker-fail")
        assert (fail_row.from_status, fail_row.to_status) == ("RUNNING", "FAILED")
        assert fail_row.reason == "字段缺失"
        assert fail_row.request_id == "req-fail"
        assert fail_row.details["error_code"] == "parse_error"
        assert fail_row.details["closed"] is False
        retry_row = _one(audits, "worker-retry")
        assert (retry_row.from_status, retry_row.to_status) == ("FAILED", "RETRYING")
        assert retry_row.details["retry_count"] == 1

        # 收口：再失败一次，然后 system-close（保留 error，状态不变）。
        reclaimed = worker.claim(scope, run.run_id, expected_version=retrying.row_version)
        failed_again = worker.fail(
            scope,
            run.run_id,
            expected_version=reclaimed.row_version,
            error_code="parse_error",
            error_summary="字段缺失",
        )
        closed = worker.close(
            scope,
            run.run_id,
            expected_version=failed_again.row_version,
            reason="人工确认放弃本次处理",
            request_id="req-close",
        )
        assert closed.status == ProcessingRunStatus.FAILED.value
        assert closed.closed is True
        assert closed.closed_at is not None
        assert closed.is_terminal
        assert closed.error_code == "parse_error"
        assert closed.error_summary == "字段缺失"

        close_row = _one(_audit_rows(session, run.run_id), "system-close")
        assert (close_row.from_status, close_row.to_status) == ("FAILED", "FAILED")
        assert close_row.reason == "人工确认放弃本次处理"
        assert close_row.request_id == "req-close"
        assert close_row.details["error_code"] == "parse_error"
        assert close_row.details["closed"] is True

        # 重新读取必须直接映射 closed_at；收口后 Worker 重试被拒绝。
        reloaded = runs.get(scope, run.run_id)
        assert reloaded is not None and reloaded.closed and reloaded.is_terminal
        with pytest.raises(InvalidStateTransitionError) as excinfo:
            worker.retry(
                scope,
                run.run_id,
                expected_version=reloaded.row_version,
            )
        assert "已系统收口" in str(excinfo.value)

        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == run.run_id)
        ).all()
        by_type = {event.event_type: event for event in events}
        for event_type in (
            "processing.run.failed",
            "processing.run.retry",
            "processing.run.closed",
        ):
            assert by_type[event_type].status == "RECORDED"
            assert by_type[event_type].dispatch is False
        assert by_type["processing.run.requested"].status == "PENDING"
        assert by_type["processing.run.requested"].dispatch is True
        assert by_type["processing.run.closed"].payload["from_status"] == "FAILED"
    finally:
        session.close()


def test_close_uses_optimistic_lock_and_requires_reason(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    runs, worker, session = _services(factory)
    try:
        run = runs.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=("raw/1",)),
        )
        queued = worker.enqueue(scope, run.run_id, expected_version=run.row_version)
        claimed = worker.claim(scope, run.run_id, expected_version=queued.row_version)
        failed = worker.fail(
            scope,
            run.run_id,
            expected_version=claimed.row_version,
            error_code="parse_error",
            error_summary="字段缺失",
        )

        with pytest.raises(DomainValidationError):
            worker.close(
                scope,
                run.run_id,
                expected_version=failed.row_version,
                reason="   ",
            )
        with pytest.raises(VersionConflictError):
            worker.close(
                scope,
                run.run_id,
                expected_version=failed.row_version - 1,
                reason="人工确认放弃",
            )

        assert all(row.action != "system-close" for row in _audit_rows(session, run.run_id))
        assert not runs.get(scope, run.run_id).closed  # type: ignore[union-attr]
    finally:
        session.close()


def test_client_cancel_is_audited_once(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    runs, _worker, session = _services(factory)
    try:
        run = runs.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=("raw/1",)),
        )
        cancelled = runs.act(
            scope,
            run.run_id,
            "cancel",
            expected_version=run.row_version,
            request_id="req-cancel",
        )
        row = _one(_audit_rows(session, run.run_id), "cancel")
        assert (row.from_status, row.to_status) == ("CREATED", "CANCELLED")
        assert row.request_id == "req-cancel"
        with pytest.raises(InvalidStateTransitionError):
            runs.act(
                scope,
                run.run_id,
                "cancel",
                expected_version=cancelled.row_version,
            )
        with pytest.raises(DomainValidationError):
            runs.act(
                scope,
                run.run_id,
                "system-close",
                expected_version=cancelled.row_version,
            )
    finally:
        session.close()
