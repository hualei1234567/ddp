"""标注任务逾期汇总的领域规则（纯函数，只读）。

逾期本身是派生事实，判定口径只有一处：:func:`app.domain.annotation.task.is_overdue`。
本模块不重新定义"什么算逾期"，只回答"当前项目里逾期任务如何分布"：

- 总数、按优先级 / 状态的分布；
- 最早期逾期时间（逾期任务里最早的 ``due_at``）；
- 按逾期时长分桶。

**分桶阈值为什么是 24h / 72h**

阈值只在这里声明一次（:data:`OVERDUE_BUCKET_HOURS`），SQL 侧由它生成
``CASE`` 表达式，测试侧用 :func:`bucket_for` 校验同一口径，禁止在仓储或
路由里另写魔法数。

- 24h：标注 SLA 指标以"日"为统计粒度（``docs/product/阶段3-标注指标口径.md``
  的日/分配口径），逾期满一个自然日即需要当日升级处理；
- 72h：连续三个自然日仍未处理，视为结构性阻塞（人手、依赖或数据问题），
  与"当天可补"的短时逾期区分开。

分桶区间（左闭右开，``overdue_seconds = now - due_at``）：

- ``LT_24H``：``[0, 24h)``
- ``D1_TO_D3``：``[24h, 72h)``
- ``GT_3D``：``[72h, ∞)``

恰好等于 24h 落入 ``D1_TO_D3``，恰好等于 72h 落入 ``GT_3D``，与
:func:`app.domain.annotation.task.is_overdue` 的严格大于语义一致。

本模块不写库、不推进版本、不自动改派或关闭任务；未知状态一律显式失败，
不存在"未知状态默认成功"的分支。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.annotation.enums import AnnotationTaskStatus, TaskPriority
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.validation import (
    aware_datetime,
    coerce_enum,
    non_negative_int,
    optional_aware_datetime,
    required_uuid,
)

#: 分桶切点（小时）。理由见模块文档；SQL 与测试都从这一处派生。
OVERDUE_BUCKET_HOURS: tuple[int, int] = (24, 72)

_SECONDS_PER_HOUR = 3600


class OverdueBucket(StrEnum):
    """逾期时长分桶（稳定契约值，前端按它渲染固定顺序）。"""

    LT_24H = "LT_24H"
    D1_TO_D3 = "D1_TO_D3"
    GT_3D = "GT_3D"


@dataclass(frozen=True, slots=True)
class OverdueBucketDefinition:
    """一个分桶的边界；``upper_bound_hours=None`` 表示无上界。"""

    bucket: OverdueBucket
    lower_bound_hours: int
    upper_bound_hours: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bucket", coerce_enum(self.bucket, OverdueBucket, "bucket"))
        object.__setattr__(
            self,
            "lower_bound_hours",
            non_negative_int(self.lower_bound_hours, "lower_bound_hours"),
        )
        if self.upper_bound_hours is not None:
            upper = non_negative_int(self.upper_bound_hours, "upper_bound_hours")
            if upper <= self.lower_bound_hours:
                raise DomainValidationError("upper_bound_hours 必须大于 lower_bound_hours")
            object.__setattr__(self, "upper_bound_hours", upper)


def _bucket_definitions() -> tuple[OverdueBucketDefinition, ...]:
    first, second = OVERDUE_BUCKET_HOURS
    return (
        OverdueBucketDefinition(OverdueBucket.LT_24H, 0, first),
        OverdueBucketDefinition(OverdueBucket.D1_TO_D3, first, second),
        OverdueBucketDefinition(OverdueBucket.GT_3D, second, None),
    )


#: 分桶定义（顺序即对外展示顺序）。
OVERDUE_BUCKETS: tuple[OverdueBucketDefinition, ...] = _bucket_definitions()


def bucket_for(overdue_seconds: int) -> OverdueBucket:
    """按逾期秒数返回分桶；与 SQL ``CASE`` 表达式同一口径。"""
    seconds = non_negative_int(overdue_seconds, "overdue_seconds")
    for definition in OVERDUE_BUCKETS:
        if definition.upper_bound_hours is None:
            return definition.bucket
        if seconds < definition.upper_bound_hours * _SECONDS_PER_HOUR:
            return definition.bucket
    raise AssertionError("分桶定义必须覆盖全部非负时长")  # pragma: no cover


@dataclass(frozen=True, slots=True)
class OverdueGroup:
    """仓储聚合出的一组逾期任务（优先级 × 状态 × 分桶）。"""

    priority: TaskPriority | str
    status: AnnotationTaskStatus | str
    bucket: OverdueBucket | str
    count: int
    earliest_due_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "priority", coerce_enum(self.priority, TaskPriority, "priority"))
        object.__setattr__(self, "status", coerce_enum(self.status, AnnotationTaskStatus, "status"))
        object.__setattr__(self, "bucket", coerce_enum(self.bucket, OverdueBucket, "bucket"))
        object.__setattr__(self, "count", non_negative_int(self.count, "count"))
        object.__setattr__(
            self,
            "earliest_due_at",
            optional_aware_datetime(self.earliest_due_at, "earliest_due_at"),
        )


@dataclass(frozen=True, slots=True)
class OverduePriorityCount:
    priority: TaskPriority
    count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "priority", coerce_enum(self.priority, TaskPriority, "priority"))
        object.__setattr__(self, "count", non_negative_int(self.count, "count"))


@dataclass(frozen=True, slots=True)
class OverdueStatusCount:
    status: AnnotationTaskStatus
    count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", coerce_enum(self.status, AnnotationTaskStatus, "status"))
        object.__setattr__(self, "count", non_negative_int(self.count, "count"))


@dataclass(frozen=True, slots=True)
class OverdueBucketCount:
    bucket: OverdueBucket
    count: int
    lower_bound_hours: int
    upper_bound_hours: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bucket", coerce_enum(self.bucket, OverdueBucket, "bucket"))
        object.__setattr__(self, "count", non_negative_int(self.count, "count"))
        object.__setattr__(
            self,
            "lower_bound_hours",
            non_negative_int(self.lower_bound_hours, "lower_bound_hours"),
        )
        if self.upper_bound_hours is not None:
            object.__setattr__(
                self,
                "upper_bound_hours",
                non_negative_int(self.upper_bound_hours, "upper_bound_hours"),
            )


@dataclass(frozen=True, slots=True)
class OverdueSummary:
    """项目级逾期汇总；所有计数都由同一批聚合行派生，保证自洽。

    ``by_status`` 只列出**实际出现**的状态（计数为 0 的状态不占位），
    顺序按状态机枚举顺序；``by_priority`` 与 ``buckets`` 是封闭集合，
    零值也显式返回，前端可稳定渲染固定顺序。
    """

    project_id: UUID
    evaluated_at: datetime
    total_overdue: int
    earliest_due_at: datetime | None
    by_priority: tuple[OverduePriorityCount, ...] = field(default_factory=tuple)
    by_status: tuple[OverdueStatusCount, ...] = field(default_factory=tuple)
    buckets: tuple[OverdueBucketCount, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", required_uuid(self.project_id, "project_id"))
        object.__setattr__(self, "evaluated_at", aware_datetime(self.evaluated_at, "evaluated_at"))
        total = non_negative_int(self.total_overdue, "total_overdue")
        object.__setattr__(self, "total_overdue", total)
        object.__setattr__(
            self,
            "earliest_due_at",
            optional_aware_datetime(self.earliest_due_at, "earliest_due_at"),
        )
        object.__setattr__(self, "by_priority", tuple(self.by_priority))
        object.__setattr__(self, "by_status", tuple(self.by_status))
        object.__setattr__(self, "buckets", tuple(self.buckets))
        for name, entry_type in (
            ("by_priority", OverduePriorityCount),
            ("by_status", OverdueStatusCount),
            ("buckets", OverdueBucketCount),
        ):
            for entry in getattr(self, name):
                if not isinstance(entry, entry_type):
                    raise DomainValidationError(f"{name} 元素类型非法")
        if sum(item.count for item in self.by_priority) != total:
            raise DomainValidationError("by_priority 计数之和必须等于 total_overdue")
        if sum(item.count for item in self.by_status) != total:
            raise DomainValidationError("by_status 计数之和必须等于 total_overdue")
        if sum(item.count for item in self.buckets) != total:
            raise DomainValidationError("buckets 计数之和必须等于 total_overdue")
        if total == 0 and self.earliest_due_at is not None:
            raise DomainValidationError("没有逾期任务时 earliest_due_at 必须为空")
        if total > 0 and self.earliest_due_at is None:
            raise DomainValidationError("存在逾期任务时 earliest_due_at 不能为空")


def build_overdue_summary(
    project_id: UUID,
    groups: Iterable[OverdueGroup],
    now: datetime,
) -> OverdueSummary:
    """把仓储聚合行折叠成汇总值对象；顺序稳定，便于测试与前端渲染。"""
    moment = aware_datetime(now, "now")
    priority_counts: dict[TaskPriority, int] = {item: 0 for item in TaskPriority}
    status_counts: dict[AnnotationTaskStatus, int] = {}
    bucket_counts: dict[OverdueBucket, int] = {item.bucket: 0 for item in OVERDUE_BUCKETS}
    total = 0
    earliest: datetime | None = None

    for group in groups:
        if not isinstance(group, OverdueGroup):
            raise DomainValidationError("groups 元素必须是 OverdueGroup")
        total += group.count
        priority_counts[TaskPriority(group.priority)] += group.count
        status_counts[group.status] = status_counts.get(group.status, 0) + group.count
        bucket_counts[OverdueBucket(group.bucket)] += group.count
        if group.earliest_due_at is not None:
            earliest = (
                group.earliest_due_at if earliest is None else min(earliest, group.earliest_due_at)
            )

    by_priority = tuple(
        OverduePriorityCount(priority=priority, count=priority_counts[priority])
        for priority in TaskPriority
    )
    by_status = tuple(
        OverdueStatusCount(status=status, count=status_counts[status])
        for status in AnnotationTaskStatus
        if status in status_counts
    )
    buckets = tuple(
        OverdueBucketCount(
            bucket=definition.bucket,
            count=bucket_counts[definition.bucket],
            lower_bound_hours=definition.lower_bound_hours,
            upper_bound_hours=definition.upper_bound_hours,
        )
        for definition in OVERDUE_BUCKETS
    )
    # 没有任何逾期行时，earliest 必须是 None（而不是由空聚合臆造时间）。
    return OverdueSummary(
        project_id=project_id,
        evaluated_at=moment,
        total_overdue=total,
        earliest_due_at=earliest if total > 0 else None,
        by_priority=by_priority,
        by_status=by_status,
        buckets=buckets,
    )


__all__ = [
    "OVERDUE_BUCKETS",
    "OVERDUE_BUCKET_HOURS",
    "OverdueBucket",
    "OverdueBucketCount",
    "OverdueBucketDefinition",
    "OverdueGroup",
    "OverduePriorityCount",
    "OverdueStatusCount",
    "OverdueSummary",
    "bucket_for",
    "build_overdue_summary",
]
