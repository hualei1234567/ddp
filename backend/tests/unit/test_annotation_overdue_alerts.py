"""领域测试：逾期提醒的窗口判定与去重（纯函数、时间边界）。

钉住三件事：逾期口径复用 ``is_overdue``；去重键是「任务 + due_at」窗口；
同一窗口在单批内也不会重复产出。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.overdue import (
    build_overdue_alert,
    overdue_window,
    select_overdue_alerts,
)
from app.domain.annotation.task import AnnotationTask

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


def _task(
    *,
    due_at: datetime | None,
    status: str = "IN_PROGRESS",
    task_id: UUID | None = None,
    code: str = "AN-OVERDUE-001",
) -> AnnotationTask:
    return AnnotationTask(
        task_id=task_id or uuid4(),
        project_id=uuid4(),
        code=code,
        name="逾期提醒任务",
        sample_set_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        schema_sha256=SHA,
        status=status,
        due_at=due_at,
    )


def test_no_due_at_has_no_window_or_alert() -> None:
    task = _task(due_at=None)
    assert overdue_window(task) is None
    assert build_overdue_alert(task, NOW + timedelta(days=365)) is None


def test_exactly_at_due_at_is_not_overdue() -> None:
    due = NOW + timedelta(hours=1)
    task = _task(due_at=due)
    assert build_overdue_alert(task, due) is None
    alert = build_overdue_alert(task, due + timedelta(seconds=1))
    assert alert is not None
    assert alert.overdue_seconds == 1


def test_past_due_builds_alert_with_utc_window_and_seconds() -> None:
    due = NOW - timedelta(hours=2)
    task = _task(due_at=due)
    alert = build_overdue_alert(task, NOW)
    assert alert is not None
    assert alert.task_id == task.task_id
    assert alert.project_id == task.project_id
    assert alert.task_code == task.code
    assert alert.status == "IN_PROGRESS"
    assert alert.due_at == due
    assert alert.overdue_seconds == 7200
    assert alert.window == f"{task.task_id}:{due.isoformat()}"


def test_closed_statuses_never_alert() -> None:
    due = NOW - timedelta(days=1)
    for status in ("COMPLETED", "CANCELLED", "ARCHIVED"):
        assert build_overdue_alert(_task(due_at=due, status=status), NOW) is None


def test_window_is_identical_across_timezones_for_same_instant() -> None:
    cst = timezone(timedelta(hours=8))
    due_utc = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    task_utc = _task(due_at=due_utc)
    task_cst = _task(due_at=due_utc.astimezone(cst), task_id=task_utc.task_id)
    assert overdue_window(task_utc) == overdue_window(task_cst)
    alert = build_overdue_alert(task_cst, NOW)
    assert alert is not None
    assert alert.due_at == due_utc
    assert alert.due_at.tzinfo is UTC


def test_now_must_be_timezone_aware() -> None:
    with pytest.raises(DomainValidationError):
        build_overdue_alert(
            _task(due_at=NOW - timedelta(hours=1)),
            datetime(2026, 11, 18, 12, 0),
        )


def test_select_skips_already_emitted_window() -> None:
    task = _task(due_at=NOW - timedelta(hours=1))
    window = overdue_window(task)
    assert window is not None
    assert select_overdue_alerts([task], NOW, emitted_windows=set()) != []
    assert select_overdue_alerts([task], NOW, emitted_windows={window}) == []


def test_select_dedupes_same_window_within_one_batch() -> None:
    task = _task(due_at=NOW - timedelta(hours=1))
    duplicate = task  # 同一任务在同一批候选中出现两次
    alerts = select_overdue_alerts([task, duplicate], NOW, emitted_windows=set())
    assert len(alerts) == 1


def test_changed_due_at_opens_a_new_window() -> None:
    task_id = uuid4()
    first = _task(due_at=NOW - timedelta(hours=2), task_id=task_id)
    rescheduled = _task(due_at=NOW - timedelta(minutes=30), task_id=task_id)
    first_window = overdue_window(first)
    assert first_window is not None
    assert overdue_window(rescheduled) != first_window
    alerts = select_overdue_alerts(
        [rescheduled],
        NOW,
        emitted_windows={first_window},
    )
    assert len(alerts) == 1
    assert alerts[0].overdue_seconds == 1800


def test_select_preserves_input_order_and_skips_non_overdue() -> None:
    overdue_a = _task(due_at=NOW - timedelta(hours=3))
    future = _task(due_at=NOW + timedelta(hours=3))
    overdue_b = _task(due_at=NOW - timedelta(hours=1))
    alerts = select_overdue_alerts([overdue_a, future, overdue_b], NOW, emitted_windows=set())
    assert [item.task_id for item in alerts] == [overdue_a.task_id, overdue_b.task_id]
