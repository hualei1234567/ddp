"""ProcessingStep 领域不变量：SKIPPED 必须解释、FAILED 必须有错误、阶段不可回退。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.domain.processing.errors import DomainValidationError
from app.domain.processing.steps import ProcessingStep

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _starting(attempt: int = 1) -> ProcessingStep:
    return ProcessingStep.starting(
        step_id=uuid4(),
        project_id=uuid4(),
        run_id=uuid4(),
        stage=ProcessingStage.FILE_VALIDATION.value,
        attempt=attempt,
        now=NOW,
        actor_id=uuid4(),
    )


def test_skipped_requires_a_reason() -> None:
    step = _starting()
    with pytest.raises(DomainValidationError, match="SKIPPED 必须记录跳过原因"):
        step.finish(
            status=ProcessingStepStatus.SKIPPED.value,
            now=NOW + timedelta(seconds=1),
            log_summary=None,
            actor_id=uuid4(),
        )
    finished = step.finish(
        status=ProcessingStepStatus.SKIPPED.value,
        now=NOW + timedelta(seconds=1),
        log_summary="无真实解析器",
        actor_id=uuid4(),
    )
    assert finished.status == ProcessingStepStatus.SKIPPED.value
    assert finished.finished_at is not None


def test_failed_requires_error_and_other_states_forbid_it() -> None:
    step = _starting()
    with pytest.raises(DomainValidationError, match="FAILED 必须同时记录"):
        step.finish(
            status=ProcessingStepStatus.FAILED.value,
            now=NOW + timedelta(seconds=1),
            log_summary="失败",
            actor_id=uuid4(),
        )
    with pytest.raises(DomainValidationError, match="只有 FAILED 状态可以携带错误"):
        step.finish(
            status=ProcessingStepStatus.SUCCEEDED.value,
            now=NOW + timedelta(seconds=1),
            log_summary="成功",
            error_code="x",
            error_summary="y",
            actor_id=uuid4(),
        )


def test_attempt_must_be_positive_and_stage_closed() -> None:
    with pytest.raises(DomainValidationError):
        _starting(attempt=0)
    with pytest.raises(DomainValidationError):
        ProcessingStep.starting(
            step_id=uuid4(),
            project_id=uuid4(),
            run_id=uuid4(),
            stage="NOT_A_STAGE",
            attempt=1,
            now=NOW,
            actor_id=uuid4(),
        )


def test_terminal_step_cannot_restart() -> None:
    finished = _starting().finish(
        status=ProcessingStepStatus.SUCCEEDED.value,
        now=NOW + timedelta(seconds=1),
        log_summary="ok",
        actor_id=uuid4(),
    )
    with pytest.raises(DomainValidationError, match="已终态"):
        finished.restart(now=NOW + timedelta(seconds=2), actor_id=uuid4())


def test_running_step_must_not_carry_log_or_finish() -> None:
    with pytest.raises(DomainValidationError, match="RUNNING 不得携带 finished_at"):
        ProcessingStep(
            step_id=uuid4(),
            project_id=uuid4(),
            run_id=uuid4(),
            stage=ProcessingStage.FILE_VALIDATION.value,
            attempt=1,
            started_at=NOW,
            finished_at=NOW,
        )
