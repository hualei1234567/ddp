"""领域测试：采集会话状态机、原因要求与状态—时间一致性。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.collection import (
    CaptureSession,
    CaptureSessionStatus,
    DomainValidationError,
    InvalidStateTransitionError,
    allowed_session_actions,
    assert_session_action,
    session_transition_pairs,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _session(status: str = "SCHEDULED", **overrides: object) -> CaptureSession:
    values: dict[str, object] = {
        "session_id": uuid4(),
        "project_id": uuid4(),
        "code": "session-1",
        "status": status,
    }
    values.update(overrides)
    return CaptureSession(**values)  # type: ignore[arg-type]


def test_full_happy_path_preserves_times_and_reason() -> None:
    session = _session()
    started = session.apply_action("start", now=_NOW)
    assert started.status is CaptureSessionStatus.RUNNING
    assert started.started_at == _NOW
    assert started.ended_at is None

    paused = started.apply_action(
        "pause",
        {"reason": "更换电池"},
        now=_NOW + timedelta(minutes=1),
    )
    assert paused.status is CaptureSessionStatus.PAUSED
    assert paused.status_reason == "更换电池"

    resumed = paused.apply_action(
        "resume",
        {"reason": "电池更换完成"},
        now=_NOW + timedelta(minutes=2),
    )
    assert resumed.status is CaptureSessionStatus.RUNNING
    assert resumed.status_reason == "电池更换完成"

    completed = resumed.apply_action("complete", now=_NOW + timedelta(minutes=3))
    assert completed.status is CaptureSessionStatus.COMPLETED
    assert completed.ended_at == _NOW + timedelta(minutes=3)
    assert allowed_session_actions(completed.status) == frozenset()


def test_abort_from_scheduled_allows_missing_started_at() -> None:
    aborted = _session().apply_action(
        "abort",
        {"reason": "设备故障，未开录"},
        now=_NOW,
    )
    assert aborted.status is CaptureSessionStatus.ABORTED
    assert aborted.started_at is None
    assert aborted.ended_at == _NOW
    assert aborted.status_reason == "设备故障，未开录"


@pytest.mark.parametrize(
    ("current", "action"),
    [
        ("COMPLETED", "start"),
        ("COMPLETED", "pause"),
        ("ABORTED", "resume"),
        ("ABORTED", "complete"),
        ("SCHEDULED", "pause"),
        ("SCHEDULED", "complete"),
        ("RUNNING", "resume"),
        ("PAUSED", "pause"),
    ],
)
def test_illegal_transitions_are_rejected(current: str, action: str) -> None:
    with pytest.raises(InvalidStateTransitionError):
        assert_session_action(CaptureSessionStatus(current), action)


@pytest.mark.parametrize("action", ["pause", "resume", "abort"])
def test_reason_requiring_actions_reject_missing_reason(action: str) -> None:
    status = {
        "pause": "RUNNING",
        "resume": "PAUSED",
        "abort": "RUNNING",
    }[action]
    started_at = None if status == "SCHEDULED" else _NOW
    session = _session(status, started_at=started_at)
    with pytest.raises(DomainValidationError, match="必须填写原因"):
        session.apply_action(action, now=_NOW)


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _session().apply_action("teleport", now=_NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "SCHEDULED", "started_at": _NOW},
        {"status": "SCHEDULED", "ended_at": _NOW},
        {"status": "RUNNING"},
        {"status": "PAUSED", "started_at": _NOW, "ended_at": _NOW},
        {"status": "COMPLETED", "started_at": _NOW},
        {"status": "ABORTED"},
        {"status": "RUNNING", "started_at": _NOW, "ended_at": _NOW - timedelta(seconds=1)},
    ],
)
def test_status_time_inconsistency_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        _session(**overrides)


def test_transition_pairs_match_state_machine() -> None:
    assert session_transition_pairs() == frozenset(
        {
            ("SCHEDULED", "RUNNING"),
            ("SCHEDULED", "ABORTED"),
            ("RUNNING", "PAUSED"),
            ("RUNNING", "COMPLETED"),
            ("RUNNING", "ABORTED"),
            ("PAUSED", "RUNNING"),
            ("PAUSED", "COMPLETED"),
            ("PAUSED", "ABORTED"),
        }
    )
