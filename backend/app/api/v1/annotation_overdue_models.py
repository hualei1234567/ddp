"""标注任务逾期汇总 HTTP 响应模型（N6-002 收尾，只读）。

字段集合以运行时真实返回为准，并与
``packages/contracts/fragments/schemas.stage3.yaml`` 的
``AnnotationOverdueSummary`` 及其子对象对齐。所有计数都由同一批聚合行派生，
服务端保证 ``total_overdue`` 等于各分布之和。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.annotation.overdue_summary import OverdueSummary


class OverduePriorityCountOut(BaseModel):
    priority: str
    count: int


class OverdueStatusCountOut(BaseModel):
    status: str
    count: int


class OverdueBucketCountOut(BaseModel):
    """一个逾期时长分桶；边界由服务端领域常量给出，客户端不自行推导。"""

    bucket: str
    count: int
    lower_bound_hours: int
    upper_bound_hours: int | None


class AnnotationOverdueSummaryOut(BaseModel):
    project_id: UUID
    # 服务端计算分桶所用的"现在"；客户端据此解释边界，不自行取本地时间。
    evaluated_at: datetime
    total_overdue: int
    # 逾期任务里最早的 due_at；没有逾期任务时为 null。
    earliest_due_at: datetime | None
    by_priority: list[OverduePriorityCountOut]
    # 只列出实际出现过的状态，计数为 0 的状态不占位。
    by_status: list[OverdueStatusCountOut]
    buckets: list[OverdueBucketCountOut]

    @classmethod
    def from_domain(cls, summary: OverdueSummary) -> AnnotationOverdueSummaryOut:
        return cls(
            project_id=summary.project_id,
            evaluated_at=summary.evaluated_at,
            total_overdue=summary.total_overdue,
            earliest_due_at=summary.earliest_due_at,
            by_priority=[
                OverduePriorityCountOut(priority=item.priority.value, count=item.count)
                for item in summary.by_priority
            ],
            by_status=[
                OverdueStatusCountOut(status=item.status.value, count=item.count)
                for item in summary.by_status
            ],
            buckets=[
                OverdueBucketCountOut(
                    bucket=item.bucket.value,
                    count=item.count,
                    lower_bound_hours=item.lower_bound_hours,
                    upper_bound_hours=item.upper_bound_hours,
                )
                for item in summary.buckets
            ],
        )


__all__ = [
    "AnnotationOverdueSummaryOut",
    "OverdueBucketCountOut",
    "OverduePriorityCountOut",
    "OverdueStatusCountOut",
]
