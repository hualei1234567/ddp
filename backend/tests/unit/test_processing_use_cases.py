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
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
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
        ),
        ProcessingWorkerUseCases(repository, session),
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
