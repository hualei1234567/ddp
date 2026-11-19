"""领域测试：标注任务逾期汇总（纯函数、分桶边界）。

逾期是派生事实；这里钉住分桶口径与汇总自洽性，不接触数据库。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.annotation.enums import AnnotationTaskStatus, TaskPriority
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.overdue_summary import (
    OVERDUE_BUCKETS,
    OverdueBucket,
    OverdueGroup,
    OverdueSummary,
    bucket_for,
    build_overdue_summary,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
HOUR = 3600


def test_bucket_hours_are_single_source_and_ordered() -> None:
    assert [item.bucket for item in OVERDUE_BUCKETS] == [
        OverdueBucket.LT_24H,
        OverdueBucket.D1_TO_D3,
        OverdueBucket.GT_3D,
    ]
    assert [(item.lower_bound_hours, item.upper_bound_hours) for item in OVERDUE_BUCKETS] == [
        (0, 24),
        (24, 72),
        (72, None),
    ]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, OverdueBucket.LT_24H),
        (1, OverdueBucket.LT_24H),
        (24 * HOUR - 1, OverdueBucket.LT_24H),
        # 恰好 24h 落入下一桶（左闭右开）。
        (24 * HOUR, OverdueBucket.D1_TO_D3),
        (72 * HOUR - 1, OverdueBucket.D1_TO_D3),
        # 恰好 72h 落入无上界桶。
        (72 * HOUR, OverdueBucket.GT_3D),
        (1000 * HOUR, OverdueBucket.GT_3D),
    ],
)
def test_bucket_boundaries(seconds: int, expected: OverdueBucket) -> None:
    assert bucket_for(seconds) is expected


def test_bucket_rejects_negative_seconds() -> None:
    with pytest.raises(DomainValidationError):
        bucket_for(-1)


def _group(
    *,
    priority: TaskPriority = TaskPriority.P1,
    status: AnnotationTaskStatus = AnnotationTaskStatus.IN_PROGRESS,
    bucket: OverdueBucket = OverdueBucket.LT_24H,
    count: int = 1,
    due_at: datetime | None = None,
) -> OverdueGroup:
    return OverdueGroup(
        priority=priority,
        status=status,
        bucket=bucket,
        count=count,
        earliest_due_at=due_at,
    )


def test_empty_groups_yield_zeroed_summary() -> None:
    summary = build_overdue_summary(uuid4(), [], NOW)

    assert summary.total_overdue == 0
    assert summary.earliest_due_at is None
    assert [item.priority for item in summary.by_priority] == list(TaskPriority)
    assert all(item.count == 0 for item in summary.by_priority)
    assert summary.by_status == ()
    assert [item.bucket for item in summary.buckets] == [
        OverdueBucket.LT_24H,
        OverdueBucket.D1_TO_D3,
        OverdueBucket.GT_3D,
    ]
    assert all(item.count == 0 for item in summary.buckets)


def test_summary_aggregates_distributions_and_earliest() -> None:
    earliest = NOW - timedelta(days=5)
    summary = build_overdue_summary(
        uuid4(),
        [
            _group(priority=TaskPriority.P0, bucket=OverdueBucket.GT_3D, due_at=earliest),
            _group(
                priority=TaskPriority.P1,
                status=AnnotationTaskStatus.BLOCKED,
                bucket=OverdueBucket.D1_TO_D3,
                due_at=NOW - timedelta(days=2),
            ),
            _group(
                priority=TaskPriority.P1,
                status=AnnotationTaskStatus.BLOCKED,
                bucket=OverdueBucket.LT_24H,
                due_at=NOW - timedelta(hours=1),
            ),
        ],
        NOW,
    )

    assert summary.total_overdue == 3
    assert summary.earliest_due_at == earliest
    assert {item.priority: item.count for item in summary.by_priority} == {
        TaskPriority.P0: 1,
        TaskPriority.P1: 2,
        TaskPriority.P2: 0,
        TaskPriority.P3: 0,
    }
    # 只列出实际出现过的状态，顺序按状态机枚举顺序。
    assert [(item.status, item.count) for item in summary.by_status] == [
        (AnnotationTaskStatus.IN_PROGRESS, 1),
        (AnnotationTaskStatus.BLOCKED, 2),
    ]
    assert {item.bucket: item.count for item in summary.buckets} == {
        OverdueBucket.LT_24H: 1,
        OverdueBucket.D1_TO_D3: 1,
        OverdueBucket.GT_3D: 1,
    }


def test_summary_rejects_inconsistent_totals() -> None:
    with pytest.raises(DomainValidationError):
        OverdueSummary(
            project_id=uuid4(),
            evaluated_at=NOW,
            total_overdue=2,
            earliest_due_at=NOW - timedelta(hours=1),
            by_priority=(),
            by_status=(),
            buckets=(),
        )


def test_summary_rejects_missing_earliest_when_overdue() -> None:
    group = _group()
    with pytest.raises(DomainValidationError):
        build_overdue_summary(
            uuid4(),
            [
                OverdueGroup(
                    priority=group.priority,
                    status=group.status,
                    bucket=group.bucket,
                    count=1,
                )
            ],
            NOW,
        )


def test_group_rejects_unknown_status() -> None:
    with pytest.raises(DomainValidationError):
        OverdueGroup(
            priority=TaskPriority.P1,
            status="NOT_A_STATUS",
            bucket=OverdueBucket.LT_24H,
            count=1,
        )
