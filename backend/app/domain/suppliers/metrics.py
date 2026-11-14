"""供应商绩效指标计算。

口径见 PRD §7：缺失数据返回 ``None``（前端显示"暂无"），
不得以 0 冒充，也不得把未知计入分母。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BatchSample:
    """绩效计算所需的批次最小投影；由应用层从实体转换。"""

    settled: bool
    status: str
    arrived_at: datetime | None
    settled_at: datetime | None
    planned_arrival_date: datetime | None
    rework_count: int


@dataclass(frozen=True, slots=True)
class SupplierMetrics:
    batch_count: int
    settled_batch_count: int
    on_time_rate: float | None
    first_pass_rate: float | None
    rework_rate: float | None
    average_cycle_hours: float | None
    accepted_segments: int
    decided_segments: int
    segment_pass_rate: float | None


def _ratio(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None，避免把"无数据"显示成 0。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def summarize(
    batches: list[BatchSample],
    *,
    accepted_segments: int,
    decided_segments: int,
) -> SupplierMetrics:
    settled = [item for item in batches if item.settled]
    with_plan = [item for item in settled if item.planned_arrival_date is not None]

    on_time = sum(
        1
        for item in with_plan
        if item.arrived_at is not None
        and _as_utc(item.arrived_at) <= _as_utc(item.planned_arrival_date)  # type: ignore[arg-type]
    )
    first_pass = sum(1 for item in settled if item.status == "ACCEPTED")
    reworked = sum(1 for item in settled if item.rework_count > 0)

    cycles = [
        (_as_utc(item.settled_at) - _as_utc(item.arrived_at)).total_seconds() / 3600
        for item in settled
        if item.settled_at is not None and item.arrived_at is not None
    ]
    average_cycle = round(sum(cycles) / len(cycles), 2) if cycles else None

    return SupplierMetrics(
        batch_count=len(batches),
        settled_batch_count=len(settled),
        on_time_rate=_ratio(on_time, len(with_plan)),
        first_pass_rate=_ratio(first_pass, len(settled)),
        rework_rate=_ratio(reworked, len(settled)),
        average_cycle_hours=average_cycle,
        accepted_segments=accepted_segments,
        decided_segments=decided_segments,
        segment_pass_rate=_ratio(accepted_segments, decided_segments),
    )


def _as_utc(value: datetime) -> datetime:
    from datetime import UTC

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = ["BatchSample", "SupplierMetrics", "summarize"]
