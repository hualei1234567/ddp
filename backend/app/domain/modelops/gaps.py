"""数据缺口维度值对象与聚合。

``aggregate_gaps`` 是纯函数：反馈侧（人工维度 + 目标）与真实覆盖侧（Episode
统计投影）分别传入，聚合时不访问数据库、不读时钟——"现在几点"由调用方传入，
保证同一输入永远得到同一输出，也便于单测覆盖新鲜度边界。

覆盖口径（按 ADR-0013 §3.4「``current_count`` 来自数据集统计，缺失显示 UNKNOWN，
不得记为 0」）：

- 真实覆盖可得 → ``current_count`` 取实测值，来源 ``MEASURED``；
- 实测不可得但反馈里人工填了 → 保留人工值，来源 ``MANUAL``；
- 两者都没有 → ``current_count=None``、来源 ``UNKNOWN``，建议补采记 0（不臆测）。

反过来，**实测到但没有任何反馈条目**的维度也必须出现在结果里（``open_feedback_count=0``、
来源 ``MEASURED``、目标未知）。缺口视图同时承担"项目里有哪些数据"的追溯职责；
若只从人工反馈建桶，零反馈项目会返回空数组，被界面读成"没有任何数据"。
测不到且无反馈的维度仍然不出现——不凭空造维度。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.modelops.enums import (
    CoverageDimension,
    CoverageSource,
    GapDimensionType,
    GapFreshness,
)
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
class MeasuredCoverage:
    """某个维度的实测覆盖。

    ``dimension_type`` 用 :class:`CoverageDimension` 校验（比契约的
    ``GapDimensionType`` 多出 ``QUALITY_STATUS`` 这一个可在 Episode 上直接聚合、
    但反馈维度尚未收录的轴）；与反馈维度同名同值的轴才能参与聚合，
    其余仅作为统计端口输出，不会凭空造出缺口条目。

    ``measured_at`` 是该维度覆盖数据的最后变更时间（取 Episode ``updated_at`` 的
    最大值），用于计算整体新鲜度；为 ``None`` 表示"计数有了但时间戳缺失"，
    此时新鲜度不得报 FRESH。
    """

    dimension_type: str
    dimension_value: str
    count: int
    measured_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dimension_type",
            coerce_enum(self.dimension_type, CoverageDimension, "dimension_type").value,
        )
        object.__setattr__(
            self,
            "dimension_value",
            text_value(self.dimension_value, "dimension_value", max_length=200),
        )
        object.__setattr__(self, "count", non_negative_int(self.count, "count"))


@dataclass(frozen=True, slots=True)
class GapSummaryItem:
    dimension_type: str
    dimension_value: str
    open_feedback_count: int
    current_count: int | None
    target_count: int | None
    suggested_episode_count: int
    requirement_ids: tuple[str, ...]
    coverage_source: str = CoverageSource.UNKNOWN.value

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "coverage_source",
            coerce_enum(self.coverage_source, CoverageSource, "coverage_source").value,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "dimension_type": self.dimension_type,
            "dimension_value": self.dimension_value,
            "open_feedback_count": self.open_feedback_count,
            "current_count": self.current_count,
            "target_count": self.target_count,
            "suggested_episode_count": self.suggested_episode_count,
            "requirement_ids": list(self.requirement_ids),
            "coverage_source": self.coverage_source,
        }


@dataclass(frozen=True, slots=True)
class GapSummary:
    """缺口摘要：条目 + 由真实覆盖推导出的新鲜度。"""

    items: tuple[GapSummaryItem, ...]
    freshness: str = GapFreshness.UNKNOWN.value

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "freshness",
            coerce_enum(self.freshness, GapFreshness, "freshness").value,
        )


_GAP_DIMENSION_VALUES = frozenset(item.value for item in GapDimensionType)


def _measured_index(
    measured: Iterable[MeasuredCoverage] | None,
) -> dict[tuple[str, str], MeasuredCoverage]:
    if measured is None:
        return {}
    index: dict[tuple[str, str], MeasuredCoverage] = {}
    for entry in measured:
        index[(entry.dimension_type, entry.dimension_value)] = entry
    return index


def aggregate_gaps(
    feedback_items: list[list[GapDimension]],
    requirement_links: dict[tuple[str, str], list[str]],
    *,
    measured_coverage: Iterable[MeasuredCoverage] | None = None,
) -> list[GapSummaryItem]:
    """按维度聚合反馈；建议补采量 = 目标上限 − 当前覆盖（未知时不臆测）。

    ``measured_coverage`` 为可选的真实覆盖投影（来自 Episode SQL 聚合）。
    不传时行为与改造前完全一致：只用反馈里人工填写的 ``current_count``。

    传入时，实测到但没有任何反馈条目的维度也会生成条目（``open_feedback_count=0``、
    ``coverage_source=MEASURED``、目标未知故建议补采 0）：缺口视图首先是"项目里
    有什么数据"的追溯入口，不能因为没人登记缺口就返回空数组。测不到的维度仍然
    不出现——绝不凭空造维度。
    """
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
    measured_by_key = _measured_index(measured_coverage)
    # 实测到、但没有任何反馈条目的维度也要出现在结果里：否则"数据有、缺口没登记"
    # 会返回空数组，被界面读成"没有任何数据"——这正是"没数据 ≠ 0"的反面。
    # 只收录契约里的缺口维度（GapDimensionType）；QUALITY_STATUS 这类
    # 仅用于统计端口的轴不得凭空造出缺口条目（见 MeasuredCoverage 文档）。
    for key in measured_by_key:
        if key[0] in _GAP_DIMENSION_VALUES:
            buckets.setdefault(key, {"count": 0, "current": None, "target": None})
    items: list[GapSummaryItem] = []
    for (dimension_type, dimension_value), bucket in sorted(buckets.items()):
        target = bucket["target"]
        manual_current = bucket["current"]
        entry = measured_by_key.get((dimension_type, dimension_value))
        if entry is not None:
            current: int | None = entry.count
            source = CoverageSource.MEASURED
        elif manual_current is not None:
            current = int(manual_current)
            source = CoverageSource.MANUAL
        else:
            current = None
            source = CoverageSource.UNKNOWN
        if current is None or target is None:
            suggested = 0
        else:
            suggested = max(int(target) - int(current), 0)
        items.append(
            GapSummaryItem(
                dimension_type=dimension_type,
                dimension_value=dimension_value,
                open_feedback_count=int(bucket["count"]),
                current_count=current,
                target_count=int(target) if target is not None else None,
                suggested_episode_count=suggested,
                requirement_ids=tuple(requirement_links.get((dimension_type, dimension_value), [])),
                coverage_source=source.value,
            )
        )
    return items


def coverage_freshness(
    measured_at: datetime | None,
    *,
    now: datetime,
    stale_after: timedelta | None,
) -> str:
    """按实测数据的最近更新时间计算新鲜度。

    - 没有任何实测时间戳 → ``UNKNOWN``（例如覆盖全部来自人工填写）；
    - 未配置阈值 → ``UNKNOWN``（判不了新鲜度就不假装最新）；
    - 超过 ``stale_after`` → ``STALE``；
    - 否则 → ``FRESH``（未来时间戳按钟差处理，不误判为过期）。

    阈值由调用方从配置传入，领域层不硬编码。
    """
    if measured_at is None or stale_after is None:
        return GapFreshness.UNKNOWN.value
    if measured_at.tzinfo is None or measured_at.utcoffset() is None:
        # naive 时间无法可靠比较；按 UNKNOWN 处理，不臆测成 FRESH。
        return GapFreshness.UNKNOWN.value
    if now - measured_at > stale_after:
        return GapFreshness.STALE.value
    return GapFreshness.FRESH.value


def _freshness_anchor(
    measured: Mapping[tuple[str, str], MeasuredCoverage],
    wanted: frozenset[tuple[str, str]],
) -> tuple[datetime | None, bool]:
    """取参与聚合的维度里最早的实测时间。

    返回 ``(anchor, incomplete)``：``incomplete`` 表示至少有一个参与聚合的维度
    有计数却没有时间戳。此时不能用其余维度的时间戳证明整体新鲜度——缺时间戳
    的那部分数据可能已经很旧，报 FRESH 就是拿部分证据冒充全体结论。
    """
    relevant = [entry for key, entry in measured.items() if key in wanted]
    if not relevant:
        return None, False
    incomplete = any(entry.measured_at is None for entry in relevant)
    stamps = [entry.measured_at for entry in relevant if entry.measured_at is not None]
    return (min(stamps) if stamps else None), incomplete


def build_gap_summary(
    items: list[GapSummaryItem],
    *,
    measured_coverage: Iterable[MeasuredCoverage] | None,
    now: datetime,
    stale_after: timedelta | None,
) -> GapSummary:
    """把聚合结果与实测时间戳合成最终视图（含新鲜度）。

    只要参与聚合的维度里有一个「有计数、无时间戳」，新鲜度一律 UNKNOWN：
    缺证据时不能报 FRESH（与 ``MeasuredCoverage`` 的文档承诺一致）。
    """
    measured = _measured_index(measured_coverage)
    wanted = frozenset((item.dimension_type, item.dimension_value) for item in items)
    anchor, incomplete = _freshness_anchor(measured, wanted)
    freshness = (
        GapFreshness.UNKNOWN.value
        if incomplete
        else coverage_freshness(anchor, now=now, stale_after=stale_after)
    )
    return GapSummary(items=tuple(items), freshness=freshness)
