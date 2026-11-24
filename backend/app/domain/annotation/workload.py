"""标注工作量周期汇总值对象。

阈值与容量由调用方传入，不写死；缺失数据必须显式区分
``N/A/UNKNOWN/STALE/PARTIAL``，不得用 0 冒充缺失。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.annotation.enums import MissingSemantics, WorkloadStatus
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.validation import (
    coerce_enum,
    non_negative_int,
    required_uuid,
)


@dataclass(frozen=True, slots=True)
class CapacityPolicy:
    """可配置容量；``None`` 表示该项未配置，不进行限制。"""

    max_open_items: int | None = None
    max_planned_minutes: int | None = None
    capacity_minutes: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_open_items", "max_planned_minutes", "capacity_minutes"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, non_negative_int(value, name))

    def exceeded(
        self,
        *,
        open_item_count: int = 0,
        open_planned_minutes: int = 0,
        planned_item_count: int = 0,
        planned_minutes: int = 0,
    ) -> tuple[str, ...]:
        """本次分配后将突破的上限名（``max_open_items`` / ``max_planned_minutes``）。

        硬阻断（分配/领取）与分配前预览必须共用这一处判定：预览说"会超"与
        提交被拒必须一一对应，不允许出现两套工时/项数公式。未配置的上限
        （``None``）不参与判定，因此也不会产生"超容量"结论。
        """
        open_items = non_negative_int(open_item_count, "open_item_count")
        open_minutes = non_negative_int(open_planned_minutes, "open_planned_minutes")
        add_items = non_negative_int(planned_item_count, "planned_item_count")
        add_minutes = non_negative_int(planned_minutes, "planned_minutes")
        breached: list[str] = []
        if self.max_open_items is not None and open_items + add_items > self.max_open_items:
            breached.append("max_open_items")
        if (
            self.max_planned_minutes is not None
            and open_minutes + add_minutes > self.max_planned_minutes
        ):
            breached.append("max_planned_minutes")
        return tuple(breached)


@dataclass(frozen=True, slots=True)
class AssignmentCapacityPreview:
    """分配前单人容量预览（纯计算，只读，不落库）。

    ``open_*`` 是该人员当前进行中分配的计划项数/计划工时（与硬阻断同一口径）；
    ``projected_*`` 是"再加上本次分配"之后的值；比率在对应上限未配置时为
    ``None``（不是 0）。``will_exceed`` 与 :meth:`CapacityPolicy.exceeded` 同源，
    客户端不必自己再算一遍。
    """

    assignee_id: UUID
    capacity: CapacityPolicy
    open_item_count: int = 0
    open_planned_minutes: int = 0
    planned_item_count: int = 0
    planned_minutes: int = 0

    def __post_init__(self) -> None:
        required_uuid(self.assignee_id, "assignee_id")
        if not isinstance(self.capacity, CapacityPolicy):
            raise DomainValidationError("capacity 必须是 CapacityPolicy")
        for name in (
            "open_item_count",
            "open_planned_minutes",
            "planned_item_count",
            "planned_minutes",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))

    @property
    def projected_item_count(self) -> int:
        return self.open_item_count + self.planned_item_count

    @property
    def projected_planned_minutes(self) -> int:
        return self.open_planned_minutes + self.planned_minutes

    @property
    def breached_limits(self) -> tuple[str, ...]:
        return self.capacity.exceeded(
            open_item_count=self.open_item_count,
            open_planned_minutes=self.open_planned_minutes,
            planned_item_count=self.planned_item_count,
            planned_minutes=self.planned_minutes,
        )

    @property
    def exceeds_open_items(self) -> bool:
        return "max_open_items" in self.breached_limits

    @property
    def exceeds_planned_minutes(self) -> bool:
        return "max_planned_minutes" in self.breached_limits

    @property
    def will_exceed(self) -> bool:
        return bool(self.breached_limits)

    @property
    def item_capacity_ratio(self) -> float | None:
        if self.capacity.max_open_items is None or self.capacity.max_open_items == 0:
            return None
        return self.projected_item_count / self.capacity.max_open_items

    @property
    def minutes_capacity_ratio(self) -> float | None:
        limit = self.capacity.max_planned_minutes
        if limit is None or limit == 0:
            return None
        return self.projected_planned_minutes / limit

    @property
    def status(self) -> WorkloadStatus:
        """与 :meth:`WorkloadSummary.effective_status` 同一判定方向。

        只有**严格大于**上限才算 ``OVER_CAPACITY``；正好等于上限是
        ``AT_CAPACITY``，不阻断分配（硬阻断同样只在 ``>`` 时触发）。
        """
        if self.will_exceed:
            return WorkloadStatus.OVER_CAPACITY
        limits = (self.capacity.max_open_items, self.capacity.max_planned_minutes)
        projected = (self.projected_item_count, self.projected_planned_minutes)
        for limit, value in zip(limits, projected, strict=True):
            if limit is not None and value == limit:
                return WorkloadStatus.AT_CAPACITY
        return WorkloadStatus.NORMAL


@dataclass(frozen=True, slots=True)
class WorkloadSummary:
    project_id: UUID
    user_id: UUID
    period_start: datetime
    period_end: datetime
    assigned_item_count: int = 0
    claimed_item_count: int = 0
    submitted_item_count: int = 0
    approved_item_count: int = 0
    rework_item_count: int = 0
    total_minutes: int = 0
    capacity_minutes: int | None = None
    status: WorkloadStatus | str = WorkloadStatus.NORMAL
    missing_semantics: MissingSemantics | str = MissingSemantics.NONE
    source_version: str = "annotation-assignment-events/1"

    def __post_init__(self) -> None:
        required_uuid(self.project_id, "project_id")
        required_uuid(self.user_id, "user_id")
        if self.period_end < self.period_start:
            raise DomainValidationError("period_end 不得早于 period_start")
        for name in (
            "assigned_item_count",
            "claimed_item_count",
            "submitted_item_count",
            "approved_item_count",
            "rework_item_count",
            "total_minutes",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        if self.capacity_minutes is not None:
            object.__setattr__(
                self,
                "capacity_minutes",
                non_negative_int(self.capacity_minutes, "capacity_minutes"),
            )
        object.__setattr__(self, "status", coerce_enum(self.status, WorkloadStatus, "status"))
        object.__setattr__(
            self,
            "missing_semantics",
            coerce_enum(self.missing_semantics, MissingSemantics, "missing_semantics"),
        )

    @property
    def utilization_ratio(self) -> float | None:
        if self.capacity_minutes is None or self.capacity_minutes == 0:
            return None
        return self.total_minutes / self.capacity_minutes

    @property
    def effective_status(self) -> WorkloadStatus:
        if self.missing_semantics is not MissingSemantics.NONE:
            return WorkloadStatus(self.missing_semantics.value)
        if self.capacity_minutes is None:
            return WorkloadStatus.NORMAL
        if self.total_minutes > self.capacity_minutes:
            return WorkloadStatus.OVER_CAPACITY
        if self.total_minutes == self.capacity_minutes:
            return WorkloadStatus.AT_CAPACITY
        return WorkloadStatus.NORMAL
