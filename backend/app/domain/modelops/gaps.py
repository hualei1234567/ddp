"""数据缺口维度值对象与聚合。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.modelops.enums import GapDimensionType
from app.domain.modelops.errors import FeedbackDimensionsInvalidError
from app.domain.modelops.validation import coerce_enum, non_negative_int, text_value


@dataclass(frozen=True, slots=True)
class GapDimension:
    dimension_type: str
    dimension_value: str
    current_count: int | None = None
    target_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dimension_type",
            coerce_enum(self.dimension_type, GapDimensionType, "dimension_type").value,
        )
        object.__setattr__(
            self,
            "dimension_value",
            text_value(self.dimension_value, "dimension_value", max_length=200),
        )
        if self.current_count is not None:
            object.__setattr__(
                self,
                "current_count",
                non_negative_int(self.current_count, "current_count"),
            )
        if self.target_count is not None:
            object.__setattr__(
                self,
                "target_count",
                non_negative_int(self.target_count, "target_count"),
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "dimension_type": self.dimension_type,
            "dimension_value": self.dimension_value,
            "current_count": self.current_count,
            "target_count": self.target_count,
        }

    @classmethod
    def from_payload(cls, value: object) -> GapDimension:
        if not isinstance(value, dict):
            raise FeedbackDimensionsInvalidError("dimension 必须是对象")
        return cls(
            dimension_type=str(value.get("dimension_type", "")),
            dimension_value=str(value.get("dimension_value", "")),
            current_count=value.get("current_count"),  # type: ignore[arg-type]
            target_count=value.get("target_count"),  # type: ignore[arg-type]
        )


def parse_dimensions(value: object, *, min_items: int = 1) -> list[GapDimension]:
    if not isinstance(value, list | tuple):
        raise FeedbackDimensionsInvalidError("dimensions 必须是数组")
    result: list[GapDimension] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        dimension = item if isinstance(item, GapDimension) else GapDimension.from_payload(item)
        key = (dimension.dimension_type, dimension.dimension_value)
        if key in seen:
            raise FeedbackDimensionsInvalidError("dimensions 存在重复维度")
        seen.add(key)
        result.append(dimension)
    if len(result) < min_items:
        raise FeedbackDimensionsInvalidError(f"dimensions 至少需要 {min_items} 项")
    return result


@dataclass(frozen=True, slots=True)
class GapSummaryItem:
    dimension_type: str
    dimension_value: str
    open_feedback_count: int
    current_count: int | None
    target_count: int | None
    suggested_episode_count: int
    requirement_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "dimension_type": self.dimension_type,
            "dimension_value": self.dimension_value,
            "open_feedback_count": self.open_feedback_count,
            "current_count": self.current_count,
            "target_count": self.target_count,
            "suggested_episode_count": self.suggested_episode_count,
            "requirement_ids": list(self.requirement_ids),
        }


def aggregate_gaps(
    feedback_items: list[list[GapDimension]],
    requirement_links: dict[tuple[str, str], list[str]],
) -> list[GapSummaryItem]:
    """按维度聚合反馈；建议补采量 = 目标上限 − 当前覆盖（未知时不臆测）。"""
    buckets: dict[tuple[str, str], dict[str, object]] = {}
    for dimensions in feedback_items:
        for dimension in dimensions:
            key = (dimension.dimension_type, dimension.dimension_value)
            bucket = buckets.setdefault(
                key,
                {"count": 0, "current": None, "target": None},
            )
            bucket["count"] = int(bucket["count"]) + 1
            if dimension.current_count is not None:
                current = bucket["current"]
                bucket["current"] = (
                    dimension.current_count
                    if current is None
                    else max(int(current), dimension.current_count)
                )
            if dimension.target_count is not None:
                target = bucket["target"]
                bucket["target"] = (
                    dimension.target_count
                    if target is None
                    else max(int(target), dimension.target_count)
                )
    items: list[GapSummaryItem] = []
    for (dimension_type, dimension_value), bucket in sorted(buckets.items()):
        current = bucket["current"]
        target = bucket["target"]
        if current is None or target is None:
            suggested = 0
        else:
            suggested = max(int(target) - int(current), 0)
        items.append(
            GapSummaryItem(
                dimension_type=dimension_type,
                dimension_value=dimension_value,
                open_feedback_count=int(bucket["count"]),
                current_count=int(current) if current is not None else None,
                target_count=int(target) if target is not None else None,
                suggested_episode_count=suggested,
                requirement_ids=tuple(requirement_links.get((dimension_type, dimension_value), [])),
            )
        )
    return items
