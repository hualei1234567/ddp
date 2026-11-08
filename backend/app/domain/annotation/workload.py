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
