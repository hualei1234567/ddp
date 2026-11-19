"""ProcessingRun 领域状态机与实体不变量测试。

覆盖全部合法边、全矩阵非法边、阶段单调性、重试计数与错误字段约束。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingRunAction,
    ProcessingRunStatus,
    ProcessingStage,
)
from app.domain.processing.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from app.domain.processing.models import ProcessingRun
from app.domain.processing.state_machines import (
    allowed_actions,
    first_stage,
    target_status,
    validate_action_name,
)

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 18, 10, 5, tzinfo=UTC)
ACTOR = uuid4()

_ALLOWED_EDGES: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            ProcessingRunStatus.CREATED.value,
            ProcessingRunAction.SYSTEM_QUEUE.value,
            ProcessingRunStatus.QUEUED.value,
        ),
        (
            ProcessingRunStatus.CREATED.value,
            ProcessingRunAction.CANCEL.value,
            ProcessingRunStatus.CANCELLED.value,
        ),
        (
            ProcessingRunStatus.QUEUED.value,
            ProcessingRunAction.WORKER_CLAIM.value,
            ProcessingRunStatus.RUNNING.value,
        ),
        (
            ProcessingRunStatus.QUEUED.value,
            ProcessingRunAction.CANCEL.value,
            ProcessingRunStatus.CANCELLED.value,
        ),
        (
            ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.WORKER_ADVANCE.value,
            ProcessingRunStatus.RUNNING.value,
        ),
        (
            ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.WORKER_SUCCEED.value,
            ProcessingRunStatus.SUCCEEDED.value,
        ),
        (
            ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.WORKER_FAIL.value,
            ProcessingRunStatus.FAILED.value,
        ),
        (
            ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.CANCEL.value,
            ProcessingRunStatus.CANCELLED.value,
        ),
        (
            ProcessingRunStatus.FAILED.value,
            ProcessingRunAction.WORKER_RETRY.value,
            ProcessingRunStatus.RETRYING.value,
        ),
        (
            ProcessingRunStatus.FAILED.value,
            ProcessingRunAction.SYSTEM_CLOSE.value,
            ProcessingRunStatus.FAILED.value,
        ),
        (
            ProcessingRunStatus.RETRYING.value,
            ProcessingRunAction.WORKER_CLAIM.value,
            ProcessingRunStatus.RUNNING.value,
        ),
        (
            ProcessingRunStatus.RETRYING.value,
            ProcessingRunAction.CANCEL.value,
            ProcessingRunStatus.CANCELLED.value,
        ),
    }
)


def _run(**overrides: object) -> ProcessingRun:
    base: dict[str, object] = {
        "run_id": uuid4(),
        "project_id": uuid4(),
        "pipeline_version": "pipeline-v1",
        "input_refs": ("raw/asset-1",),
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": ACTOR,
        "updated_by": ACTOR,
    }
    base.update(overrides)
    return ProcessingRun(**base)  # type: ignore[arg-type]


def _queued() -> ProcessingRun:
    return _run(status=ProcessingRunStatus.QUEUED.value)


def _running(**overrides: object) -> ProcessingRun:
    values: dict[str, object] = {
        "status": ProcessingRunStatus.RUNNING.value,
        "stage": first_stage(),
        "attempt": 1,
        "started_at": NOW,
    }
    values.update(overrides)
    return _run(**values)


def _failed(**overrides: object) -> ProcessingRun:
    values: dict[str, object] = {
        "status": ProcessingRunStatus.FAILED.value,
        "stage": first_stage(),
        "attempt": 1,
        "started_at": NOW,
        "finished_at": LATER,
        "error_code": "parse_error",
        "error_summary": "字段缺失",
    }
    values.update(overrides)
    return _run(**values)


def test_every_legal_edge_and_no_other_edge() -> None:
    """全矩阵：合法边必须命中，其余组合一律拒绝。"""
    actions = {action.value for action in ProcessingRunAction}
    for state in ProcessingRunStatus:
        for action in actions:
            triple = (state.value, action)
            expected = next(
                (target for current, act, target in _ALLOWED_EDGES if (current, act) == triple),
                None,
            )
            if expected is None:
                with pytest.raises(InvalidStateTransitionError):
                    target_status(state.value, action)
            else:
                assert target_status(state.value, action) == expected


def test_allowed_actions_never_expose_internal_actions() -> None:
    expected = {
        ProcessingRunStatus.CREATED.value: {"cancel"},
        ProcessingRunStatus.QUEUED.value: {"cancel"},
        ProcessingRunStatus.RUNNING.value: {"cancel"},
        ProcessingRunStatus.FAILED.value: set(),
        ProcessingRunStatus.RETRYING.value: {"cancel"},
        ProcessingRunStatus.SUCCEEDED.value: set(),
        ProcessingRunStatus.CANCELLED.value: set(),
    }
    for status, actions in expected.items():
        actual = allowed_actions(status)
        assert actual == actions
        assert not any(item.startswith(("system-", "worker-")) for item in actual)


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        validate_action_name("worker-unknown")
    with pytest.raises(DomainValidationError):
        validate_action_name("")
    with pytest.raises(DomainValidationError):
        validate_action_name(123)


def test_full_success_lifecycle_advances_stages_and_attempt() -> None:
    run = _run()
    run = run.transition(
        ProcessingRunAction.SYSTEM_QUEUE.value,
        now=NOW,
        actor_id=ACTOR,
    )
    assert run.status == ProcessingRunStatus.QUEUED.value
    assert run.stage is None

    run = run.transition(
        ProcessingRunAction.WORKER_CLAIM.value,
        now=NOW,
        actor_id=ACTOR,
    )
    assert run.status == ProcessingRunStatus.RUNNING.value
    assert run.stage == STAGE_SEQUENCE[0].value
    assert run.attempt == 1
    assert run.started_at == NOW

    for stage in STAGE_SEQUENCE[1:]:
        run = run.transition(
            ProcessingRunAction.WORKER_ADVANCE.value,
            now=NOW,
            actor_id=ACTOR,
            stage=stage.value,
            output_refs=[f"stage/{stage.value.lower()}"],
        )
        assert run.stage == stage.value
        assert run.attempt == 1

    run = run.transition(
        ProcessingRunAction.WORKER_SUCCEED.value,
        now=LATER,
        actor_id=ACTOR,
        output_refs=["episodes/1", "episodes/2"],
    )
    assert run.status == ProcessingRunStatus.SUCCEEDED.value
    assert run.finished_at == LATER
    assert run.error_code is None
    assert "episodes/2" in run.output_refs
    assert run.is_terminal


def test_failure_retry_and_recovery_counters() -> None:
    run = _running()
    failed = run.transition(
        ProcessingRunAction.WORKER_FAIL.value,
        now=LATER,
        actor_id=ACTOR,
        error_code="ffmpeg_failed",
        error_summary="转码进程退出码 1",
    )
    assert failed.status == ProcessingRunStatus.FAILED.value
    assert failed.error_code == "ffmpeg_failed"
    assert failed.finished_at == LATER
    assert failed.attempt == 1
    assert failed.retry_count == 0

    retrying = failed.transition(
        ProcessingRunAction.WORKER_RETRY.value,
        now=LATER,
        actor_id=ACTOR,
    )
    assert retrying.status == ProcessingRunStatus.RETRYING.value
    assert retrying.retry_count == 1
    assert retrying.error_code is None
    assert retrying.finished_at is None
    assert retrying.stage == failed.stage

    recovered = retrying.transition(
        ProcessingRunAction.WORKER_CLAIM.value,
        now=LATER,
        actor_id=ACTOR,
    )
    assert recovered.status == ProcessingRunStatus.RUNNING.value
    assert recovered.attempt == 2
    assert recovered.retry_count == 1
    assert recovered.started_at == NOW


def test_cancel_from_each_cancellable_state() -> None:
    created = _run().transition(
        ProcessingRunAction.CANCEL.value,
        now=NOW,
        actor_id=ACTOR,
    )
    assert created.status == ProcessingRunStatus.CANCELLED.value
    assert created.stage is None
    assert created.finished_at == NOW

    for run in (
        _queued(),
        _running(),
        _failed().transition(
            ProcessingRunAction.WORKER_RETRY.value,
            now=LATER,
            actor_id=ACTOR,
        ),
    ):
        cancelled = run.transition(
            ProcessingRunAction.CANCEL.value,
            now=LATER,
            actor_id=ACTOR,
        )
        assert cancelled.status == ProcessingRunStatus.CANCELLED.value
        assert cancelled.finished_at == LATER
        assert cancelled.error_code is None


def test_failed_run_cannot_be_cancelled_and_keeps_error() -> None:
    """FAILED 不允许客户端 cancel：否则 error_* 会被 CANCELLED 的 CHECK 清空。"""
    failed = _failed()
    assert failed.error_code is not None
    with pytest.raises(InvalidStateTransitionError):
        failed.transition(ProcessingRunAction.CANCEL.value, now=LATER, actor_id=ACTOR)
    assert allowed_actions(ProcessingRunStatus.FAILED.value) == frozenset()


def test_terminal_and_illegal_transitions_are_rejected() -> None:
    succeeded = _running().transition(
        ProcessingRunAction.WORKER_SUCCEED.value,
        now=LATER,
        actor_id=ACTOR,
    )
    with pytest.raises(InvalidStateTransitionError):
        succeeded.transition(ProcessingRunAction.CANCEL.value, now=LATER, actor_id=ACTOR)
    with pytest.raises(InvalidStateTransitionError):
        succeeded.transition(ProcessingRunAction.WORKER_CLAIM.value, now=LATER, actor_id=ACTOR)

    cancelled = _run().transition(ProcessingRunAction.CANCEL.value, now=NOW, actor_id=ACTOR)
    with pytest.raises(InvalidStateTransitionError):
        cancelled.transition(ProcessingRunAction.SYSTEM_QUEUE.value, now=NOW, actor_id=ACTOR)

    with pytest.raises(InvalidStateTransitionError):
        queued = _queued()
        queued.transition(ProcessingRunAction.WORKER_SUCCEED.value, now=NOW, actor_id=ACTOR)
    with pytest.raises(InvalidStateTransitionError):
        created = _run()
        created.transition(ProcessingRunAction.WORKER_CLAIM.value, now=NOW, actor_id=ACTOR)
    with pytest.raises(InvalidStateTransitionError):
        failed = _failed()
        failed.transition(ProcessingRunAction.WORKER_CLAIM.value, now=LATER, actor_id=ACTOR)


def test_stage_must_advance_and_never_regress() -> None:
    run = _running(stage=ProcessingStage.FORMAT_PARSING.value)
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.WORKER_ADVANCE.value,
            now=LATER,
            actor_id=ACTOR,
            stage=ProcessingStage.FILE_VALIDATION.value,
        )
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.WORKER_ADVANCE.value,
            now=LATER,
            actor_id=ACTOR,
            stage=ProcessingStage.FORMAT_PARSING.value,
        )
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.WORKER_ADVANCE.value,
            now=LATER,
            actor_id=ACTOR,
        )

    advanced = run.transition(
        ProcessingRunAction.WORKER_ADVANCE.value,
        now=LATER,
        actor_id=ACTOR,
        stage=ProcessingStage.TIME_ALIGNMENT.value,
    )
    assert advanced.stage == ProcessingStage.TIME_ALIGNMENT.value


def test_fail_requires_error_fields_and_other_actions_reject_them() -> None:
    run = _running()
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.WORKER_FAIL.value,
            now=LATER,
            actor_id=ACTOR,
            error_code="only-code",
        )
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.WORKER_SUCCEED.value,
            now=LATER,
            actor_id=ACTOR,
            error_code="unexpected",
            error_summary="不应出现",
        )


def test_cancel_rejects_stage_products_and_errors() -> None:
    run = _running()
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.CANCEL.value,
            now=LATER,
            actor_id=ACTOR,
            stage=ProcessingStage.TIME_ALIGNMENT.value,
        )
    with pytest.raises(DomainValidationError):
        run.transition(
            ProcessingRunAction.CANCEL.value,
            now=LATER,
            actor_id=ACTOR,
            output_refs=["should-not-exist"],
        )


def test_output_refs_are_deduplicated_and_trimmed() -> None:
    run = _running()
    updated = run.transition(
        ProcessingRunAction.WORKER_ADVANCE.value,
        now=LATER,
        actor_id=ACTOR,
        stage=ProcessingStage.DATA_CLEANING.value,
        output_refs=["  episodes/1  ", "episodes/1", "episodes/2"],
    )
    assert updated.output_refs == ("episodes/1", "episodes/2")


def test_entity_invariants_reject_inconsistent_construction() -> None:
    with pytest.raises(DomainValidationError):
        _run(input_refs=())
    with pytest.raises(DomainValidationError):
        _run(status=ProcessingRunStatus.RUNNING.value)
    with pytest.raises(DomainValidationError):
        _run(
            status=ProcessingRunStatus.FAILED.value,
            stage=first_stage(),
            attempt=1,
            started_at=NOW,
            finished_at=LATER,
        )
    with pytest.raises(DomainValidationError):
        _run(
            status=ProcessingRunStatus.QUEUED.value,
            attempt=1,
            retry_count=2,
        )
    with pytest.raises(DomainValidationError):
        _run(status="UNKNOWN")
    with pytest.raises(DomainValidationError):
        _run(stage="UNKNOWN")


def test_advance_keeps_status_and_does_not_bump_attempt() -> None:
    run = _running(stage=ProcessingStage.DATA_CLEANING.value)
    advanced = run.transition(
        ProcessingRunAction.WORKER_ADVANCE.value,
        now=LATER,
        actor_id=ACTOR,
        stage=ProcessingStage.QUALITY_PRE_CHECK.value,
    )
    assert advanced.status == ProcessingRunStatus.RUNNING.value
    assert advanced.attempt == 1
    assert advanced.retry_count == 0


def test_system_close_requires_reason_and_preserves_error() -> None:
    """收口是保留错误的自环：状态/错误/阶段/结束时间都不变，只是不可再操作。"""
    failed = _failed()
    assert failed.is_terminal is False

    with pytest.raises(DomainValidationError):
        failed.transition(
            ProcessingRunAction.SYSTEM_CLOSE.value,
            now=LATER,
            actor_id=ACTOR,
        )

    closed = failed.transition(
        ProcessingRunAction.SYSTEM_CLOSE.value,
        now=LATER,
        actor_id=ACTOR,
        reason="人工确认放弃本次处理",
    )
    assert closed.status == ProcessingRunStatus.FAILED.value
    assert closed.closed is True
    assert closed.closed_at == LATER
    assert closed.is_terminal
    assert closed.error_code == failed.error_code
    assert closed.error_summary == failed.error_summary
    assert closed.stage == failed.stage
    assert closed.finished_at == failed.finished_at
    assert closed.updated_at == LATER
    assert closed.updated_by == ACTOR


def test_closed_run_rejects_every_outgoing_action() -> None:
    closed = _failed(closed_at=LATER)
    assert closed.is_terminal

    with pytest.raises(InvalidStateTransitionError):
        closed.transition(
            ProcessingRunAction.WORKER_RETRY.value,
            now=LATER,
            actor_id=ACTOR,
        )
    with pytest.raises(InvalidStateTransitionError):
        closed.transition(
            ProcessingRunAction.SYSTEM_CLOSE.value,
            now=LATER,
            actor_id=ACTOR,
            reason="重复收口",
        )


def test_system_close_rejected_outside_failed_state() -> None:
    for run in (_run(), _queued(), _running()):
        with pytest.raises(InvalidStateTransitionError):
            run.transition(
                ProcessingRunAction.SYSTEM_CLOSE.value,
                now=LATER,
                actor_id=ACTOR,
                reason="状态不符",
            )


def test_reason_is_only_accepted_by_system_close() -> None:
    with pytest.raises(DomainValidationError):
        _running().transition(
            ProcessingRunAction.WORKER_FAIL.value,
            now=LATER,
            actor_id=ACTOR,
            error_code="e",
            error_summary="s",
            reason="不该出现在失败动作上",
        )


def test_closed_at_only_allowed_on_failed_status() -> None:
    with pytest.raises(DomainValidationError):
        _run(status=ProcessingRunStatus.CREATED.value, closed_at=LATER)
    with pytest.raises(DomainValidationError):
        _run(status=ProcessingRunStatus.FAILED.value, closed_at="yes")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        # closed_at 不能早于失败结束时间。
        _run(
            status=ProcessingRunStatus.FAILED.value,
            stage=first_stage(),
            attempt=1,
            started_at=NOW,
            finished_at=LATER,
            error_code="parse_error",
            error_summary="字段缺失",
            closed_at=NOW,
        )
