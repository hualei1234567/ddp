"""IngestionJob 领域：失败可重试、不可静默成功、超时与终态。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.ingestion.enums import IngestionJobStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    JobNotRetryableError,
    JobTimeoutError,
)
from app.domain.ingestion.jobs import IngestionJob

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _job(**overrides: object) -> IngestionJob:
    values: dict[str, object] = {
        "job_id": uuid4(),
        "project_id": uuid4(),
        "job_type": "PARSE_METADATA",
        "timeout_sec": 60,
        "raw_asset_ids": (uuid4(),),
        "max_attempts": 3,
    }
    values.update(overrides)
    return IngestionJob(**values)  # type: ignore[arg-type]


def test_job_cannot_succeed_without_running() -> None:
    job = _job()
    with pytest.raises(InvalidStateTransitionError):
        job.succeed(now=NOW)
    running = job.start(NOW)
    assert running.status is IngestionJobStatus.RUNNING
    with pytest.raises(InvalidStateTransitionError):
        running.start(NOW)
    succeeded = running.succeed(now=NOW, output_refs=[{"kind": "metadata"}])
    assert succeeded.status is IngestionJobStatus.SUCCEEDED
    with pytest.raises(InvalidStateTransitionError):
        succeeded.start(NOW)
    with pytest.raises(InvalidStateTransitionError):
        succeeded.fail(error_code="x", error_summary="失败", now=NOW)


def test_failed_job_requires_error_and_retry_increments_attempt() -> None:
    running = _job().start(NOW)
    with pytest.raises(DomainValidationError):
        running.fail(error_code="", error_summary="失败", now=NOW)
    failed = running.fail(
        error_code="parse_failed",
        error_summary="解析失败",
        now=NOW,
    )
    assert failed.status is IngestionJobStatus.FAILED
    assert failed.error_code == "parse_failed"
    assert failed.finished_at == NOW

    retried = failed.retry(NOW)
    assert retried.status is IngestionJobStatus.QUEUED
    assert retried.attempt == 2
    assert retried.error_code is None
    assert retried.started_at is None
    with pytest.raises(InvalidStateTransitionError):
        retried.confirm_cancel(NOW)


def test_retry_stops_at_max_attempts() -> None:
    job = _job(max_attempts=2)
    failed = job.start(NOW).fail(error_code="x", error_summary="失败", now=NOW)
    retried = failed.retry(NOW)
    failed_again = retried.start(NOW).fail(error_code="x", error_summary="失败", now=NOW)
    with pytest.raises(JobNotRetryableError):
        failed_again.retry(NOW)
    assert failed_again.can_retry is False


def test_timeout_only_after_deadline() -> None:
    running = _job(timeout_sec=30).start(NOW)
    with pytest.raises(JobTimeoutError):
        running.fail_timeout(NOW + timedelta(seconds=10))
    timed_out = running.fail_timeout(NOW + timedelta(seconds=31))
    assert timed_out.status is IngestionJobStatus.FAILED
    assert timed_out.error_code == "job_timeout"


def test_cancel_flow_and_idempotent_request_matching() -> None:
    job = _job(idempotency_key="same-key")
    cancelling = job.request_cancel(NOW)
    assert cancelling.status is IngestionJobStatus.CANCELLING
    cancelled = cancelling.confirm_cancel(NOW)
    assert cancelled.status is IngestionJobStatus.CANCELLED
    with pytest.raises(InvalidStateTransitionError):
        cancelled.retry(NOW)

    same = _job(idempotency_key="same-key", raw_asset_ids=job.raw_asset_ids)
    other = _job(idempotency_key="same-key", raw_asset_ids=(uuid4(),))
    assert job.matches_idempotent_request(same)
    assert not job.matches_idempotent_request(other)
