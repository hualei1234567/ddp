"""IngestionJob SQLite 集成测试：幂等、重试上限、失败不可静默成功与项目隔离。"""

from __future__ import annotations

import pytest
from app.application.ingestion.commands import JobActionCommand
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import IngestionJobStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    IdempotencyKeyReusedError,
    InvalidStateTransitionError,
    JobNotRetryableError,
    JobTimeoutError,
)
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.episode_support import build_services, job_command


def _scope(ids: object, project: str = "a") -> ProjectScope:
    if project == "a":
        return ProjectScope(ids.project_a, actor_id=ids.user_a)  # type: ignore[attr-defined]
    return ProjectScope(ids.project_b, actor_id=ids.user_b)  # type: ignore[attr-defined]


def test_job_idempotency_and_cross_project_isolation(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        _, job_service, _ = build_services(session)
        command = job_command(
            ids.asset_a,  # type: ignore[attr-defined]
            idempotency_key="job-key-1",
        )
        first = job_service.create(_scope(ids, "a"), command)
        replay = job_service.create(_scope(ids, "a"), command)
        assert replay.job_id == first.job_id

        with pytest.raises(IdempotencyKeyReusedError):
            job_service.create(
                _scope(ids, "a"),
                job_command(
                    ids.asset_a,  # type: ignore[attr-defined]
                    idempotency_key="job-key-1",
                    max_attempts=4,
                ),
            )

    with factory() as session:
        repository = SqlIngestionJobRepository(session)
        assert repository.get(_scope(ids, "a"), first.job_id) is not None
        assert repository.get(_scope(ids, "b"), first.job_id) is None
        assert repository.list(_scope(ids, "b")) == []


def test_job_failure_retry_and_no_silent_success(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        _, job_service, _ = build_services(session)
        job = job_service.create(
            _scope(ids, "a"),
            job_command(ids.asset_a, max_attempts=2),  # type: ignore[attr-defined]
        )
        with pytest.raises(DomainValidationError):
            job_service.apply_action(
                _scope(ids, "a"),
                job.job_id,
                JobActionCommand(
                    action="worker-fail",
                    expected_version=job.version,
                    error_code="parse_failed",
                ),
            )
        running = job_service.apply_action(
            _scope(ids, "a"),
            job.job_id,
            JobActionCommand(action="worker-start", expected_version=job.version),
        )
        assert running.status is IngestionJobStatus.RUNNING
        with pytest.raises(InvalidStateTransitionError):
            job_service.apply_action(
                _scope(ids, "a"),
                running.job_id,
                JobActionCommand(action="worker-start", expected_version=running.version),
            )

        failed = job_service.apply_action(
            _scope(ids, "a"),
            running.job_id,
            JobActionCommand(
                action="worker-fail",
                expected_version=running.version,
                error_code="parse_failed",
                error_summary="解析失败",
            ),
        )
        assert failed.status is IngestionJobStatus.FAILED
        retried = job_service.apply_action(
            _scope(ids, "a"),
            failed.job_id,
            JobActionCommand(action="retry", expected_version=failed.version),
        )
        assert retried.status is IngestionJobStatus.QUEUED
        assert retried.attempt == 2

        running_again = job_service.apply_action(
            _scope(ids, "a"),
            retried.job_id,
            JobActionCommand(action="worker-start", expected_version=retried.version),
        )
        failed_again = job_service.apply_action(
            _scope(ids, "a"),
            running_again.job_id,
            JobActionCommand(
                action="worker-fail",
                expected_version=running_again.version,
                error_code="parse_failed",
                error_summary="再次失败",
            ),
        )
        with pytest.raises(JobNotRetryableError):
            job_service.apply_action(
                _scope(ids, "a"),
                failed_again.job_id,
                JobActionCommand(action="retry", expected_version=failed_again.version),
            )

    with factory() as session:
        with pytest.raises(IntegrityError, match="ingestion_job_state_transition_invalid"):
            session.execute(
                update(IngestionJobModel)
                .where(IngestionJobModel.id == job.job_id)
                .values(status="RUNNING")
            )
            session.flush()
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError, match="ingestion_job_delete_forbidden"):
            session.execute(
                IngestionJobModel.__table__.delete().where(IngestionJobModel.id == job.job_id)
            )
            session.flush()


def test_job_timeout_records_failure(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        _, job_service, _ = build_services(session)
        job = job_service.create(
            _scope(ids, "a"),
            job_command(ids.asset_a),  # type: ignore[attr-defined]
        )
        running = job_service.apply_action(
            _scope(ids, "a"),
            job.job_id,
            JobActionCommand(action="worker-start", expected_version=job.version),
        )
        with pytest.raises(JobTimeoutError):
            job_service.apply_action(
                _scope(ids, "a"),
                running.job_id,
                JobActionCommand(action="job-timeout", expected_version=running.version),
            )

    with factory() as session:
        stored = SqlIngestionJobRepository(session).get(_scope(ids, "a"), running.job_id)
        assert stored is not None
        assert stored.status is IngestionJobStatus.RUNNING
        assert stored.error_code is None
