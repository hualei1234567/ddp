"""领域测试：标注任务 SLA 逾期判定（纯函数、时间旅行）。

逾期是派生事实，不新增状态、不写库；这里只钉住判定口径。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.task import SLA_CLOSED_STATUSES, AnnotationTask, is_overdue

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


def _task(*, due_at: datetime | None, status: str = "IN_PROGRESS") -> AnnotationTask:
    return AnnotationTask(
        task_id=uuid4(),
        project_id=uuid4(),
        code="AN-SLA-001",
        name="SLA 任务",
        sample_set_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        schema_sha256=SHA,
        status=status,
        due_at=due_at,
    )


def test_no_due_at_is_never_overdue() -> None:
    assert is_overdue(_task(due_at=None), NOW + timedelta(days=365)) is False


def test_overdue_is_strictly_after_due_at() -> None:
    due = NOW + timedelta(hours=1)
    task = _task(due_at=due)
    assert is_overdue(task, NOW) is False
    assert is_overdue(task, due) is False  # 恰好截止时刻不算逾期
    assert is_overdue(task, due + timedelta(seconds=1)) is True


def test_closed_tasks_are_not_overdue() -> None:
    due = NOW - timedelta(days=1)
    assert {item.value for item in SLA_CLOSED_STATUSES} == {
        "COMPLETED",
        "CANCELLED",
        "ARCHIVED",
    }
    for status in ("COMPLETED", "CANCELLED", "ARCHIVED"):
        assert is_overdue(_task(due_at=due, status=status), NOW) is False


def test_non_terminal_status_past_due_is_overdue() -> None:
    due = NOW - timedelta(minutes=1)
    for status in ("DRAFT", "IN_PROGRESS", "ACCEPTED", "REWORK"):
        assert is_overdue(_task(due_at=due, status=status), NOW) is True


def test_now_must_be_timezone_aware() -> None:
    with pytest.raises(DomainValidationError):
        is_overdue(_task(due_at=NOW), datetime(2026, 11, 18, 12, 0))


def test_is_overdue_does_not_mutate_task() -> None:
    due = NOW - timedelta(hours=2)
    task = _task(due_at=due)
    assert is_overdue(task, NOW) is True
    assert task.due_at == due
    assert task.status.value == "IN_PROGRESS"
