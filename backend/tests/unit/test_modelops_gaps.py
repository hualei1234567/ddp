"""数据缺口聚合单测（FBK-002）。

改造前 ``aggregate_gaps`` 没有任何单测，只有一条 API 用例间接断言。这里补齐
三条关键口径：

1. 维度聚合与「目标上限 − 当前覆盖」的计算；
2. 任一未知时建议补采为 0（不臆测）；
3. ``coverage_source`` 三态（MEASURED / MANUAL / UNKNOWN）与 ``freshness`` 三态。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.modelops.enums import CoverageSource, GapFreshness
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.gaps import (
    CoverageDimension,
    GapDimension,
    MeasuredCoverage,
    aggregate_gaps,
    build_gap_summary,
    coverage_freshness,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(hours=24)


def _feedback(*dimensions: GapDimension) -> list[GapDimension]:
    return list(dimensions)


def _by_value(items) -> dict[str, object]:
    return {item.dimension_value: item for item in items}


# --------------------------------------------------------------------------- #
# 维度聚合与建议补采量
# --------------------------------------------------------------------------- #
def test_aggregates_same_dimension_across_feedback() -> None:
    items = aggregate_gaps(
        [
            _feedback(GapDimension("SCENE", "kitchen", current_count=2, target_count=10)),
            _feedback(GapDimension("SCENE", "kitchen", current_count=5, target_count=8)),
        ],
        {},
    )
    assert len(items) == 1
    item = items[0]
    assert item.dimension_type == "SCENE"
    assert item.dimension_value == "kitchen"
    assert item.open_feedback_count == 2
    # 当前覆盖取各反馈的人工值最大值，目标同样取最大值。
    assert item.current_count == 5
    assert item.target_count == 10
    assert item.suggested_episode_count == 5


def test_dimensions_are_aggregated_independently() -> None:
    items = aggregate_gaps(
        [
            _feedback(
                GapDimension("SCENE", "kitchen", current_count=1, target_count=4),
                GapDimension("ROBOT", "r-01", current_count=1, target_count=3),
            ),
            _feedback(GapDimension("SCENE", "warehouse", current_count=0, target_count=2)),
        ],
        {},
    )
    by_key = {(item.dimension_type, item.dimension_value): item for item in items}
    assert set(by_key) == {("SCENE", "kitchen"), ("ROBOT", "r-01"), ("SCENE", "warehouse")}
    assert by_key[("SCENE", "kitchen")].suggested_episode_count == 3
    assert by_key[("ROBOT", "r-01")].suggested_episode_count == 2
    assert by_key[("SCENE", "warehouse")].suggested_episode_count == 2
    # 顺序稳定（按 (dimension_type, dimension_value) 排序），便于前端与快照比对。
    assert [item.dimension_value for item in items] == ["r-01", "kitchen", "warehouse"]


def test_suggested_never_negative_when_coverage_exceeds_target() -> None:
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", current_count=0, target_count=3))],
        {},
        measured_coverage=[MeasuredCoverage("SCENE", "kitchen", count=9, measured_at=NOW)],
    )
    assert items[0].current_count == 9
    assert items[0].suggested_episode_count == 0


@pytest.mark.parametrize(
    ("current", "target"),
    [(None, 5), (2, None), (None, None)],
)
def test_unknown_side_yields_zero_suggestion(current: int | None, target: int | None) -> None:
    """任一侧未知时建议补采为 0：绝不把"没统计"当成 0 覆盖去臆测补采量。"""
    items = aggregate_gaps([_feedback(GapDimension("SCENE", "kitchen", current, target))], {})
    assert items[0].suggested_episode_count == 0
    assert items[0].coverage_source == (
        CoverageSource.MANUAL.value if current is not None else "UNKNOWN"
    )


def test_requirement_links_are_attached_per_dimension() -> None:
    requirement_id = str(UUID(int=7))
    items = aggregate_gaps(
        [
            _feedback(GapDimension("SCENE", "kitchen", current_count=1, target_count=5)),
            _feedback(GapDimension("SCENE", "warehouse", current_count=1, target_count=5)),
        ],
        {("SCENE", "kitchen"): [requirement_id]},
    )
    by_value = _by_value(items)
    assert by_value["kitchen"].requirement_ids == (requirement_id,)
    assert by_value["warehouse"].requirement_ids == ()


# --------------------------------------------------------------------------- #
# coverage_source 三态
# --------------------------------------------------------------------------- #
def test_measured_coverage_wins_over_manual_value() -> None:
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", current_count=2, target_count=10))],
        {},
        measured_coverage=[MeasuredCoverage("SCENE", "kitchen", count=6, measured_at=NOW)],
    )
    item = items[0]
    assert item.coverage_source == CoverageSource.MEASURED.value
    assert item.current_count == 6
    assert item.suggested_episode_count == 4


def test_measured_zero_is_real_measurement_not_unknown() -> None:
    """实测到 0 与"测不到"必须区分：0 是 MEASURED，None 才是 UNKNOWN。"""
    items = aggregate_gaps(
        [_feedback(GapDimension("ROBOT", "r-01", current_count=None, target_count=3))],
        {},
        measured_coverage=[MeasuredCoverage("ROBOT", "r-01", count=0, measured_at=NOW)],
    )
    assert items[0].coverage_source == CoverageSource.MEASURED.value
    assert items[0].current_count == 0
    assert items[0].suggested_episode_count == 3


def test_manual_value_kept_when_coverage_unavailable() -> None:
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", current_count=2, target_count=10))],
        {},
        measured_coverage=[MeasuredCoverage("SCENE", "warehouse", count=0, measured_at=NOW)],
    )
    item = items[0]
    assert item.coverage_source == CoverageSource.MANUAL.value
    assert item.current_count == 2
    assert item.suggested_episode_count == 8


def test_unknown_when_neither_measured_nor_manual() -> None:
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", current_count=None, target_count=10))],
        {},
        measured_coverage=[],
    )
    item = items[0]
    assert item.coverage_source == CoverageSource.UNKNOWN.value
    assert item.current_count is None
    assert item.suggested_episode_count == 0


def test_coverage_dimension_accepts_quality_status() -> None:
    """覆盖维度比反馈维度多一个 QUALITY_STATUS，且不能被误当成非法值。"""
    entry = MeasuredCoverage(
        CoverageDimension.QUALITY_STATUS.value, "PASS", count=3, measured_at=NOW
    )
    assert entry.dimension_type == "QUALITY_STATUS"
    assert entry.count == 3


def test_measured_coverage_rejects_unknown_dimension() -> None:
    with pytest.raises(DomainValidationError):
        MeasuredCoverage("NOT_A_DIMENSION", "x", count=1)


# --------------------------------------------------------------------------- #
# freshness 三态
# --------------------------------------------------------------------------- #
def test_freshness_fresh_within_threshold() -> None:
    summary = build_gap_summary(
        aggregate_gaps(
            [_feedback(GapDimension("SCENE", "kitchen", None, 5))],
            {},
            measured_coverage=[MeasuredCoverage("SCENE", "kitchen", 1, NOW - timedelta(hours=1))],
        ),
        measured_coverage=[MeasuredCoverage("SCENE", "kitchen", 1, NOW - timedelta(hours=1))],
        now=NOW,
        stale_after=STALE_AFTER,
    )
    assert summary.freshness == GapFreshness.FRESH.value


def test_freshness_stale_beyond_threshold() -> None:
    old = NOW - timedelta(days=3)
    measured = [MeasuredCoverage("SCENE", "kitchen", 1, old)]
    summary = build_gap_summary(
        aggregate_gaps(
            [_feedback(GapDimension("SCENE", "kitchen", None, 5))],
            {},
            measured_coverage=measured,
        ),
        measured_coverage=measured,
        now=NOW,
        stale_after=STALE_AFTER,
    )
    assert summary.freshness == GapFreshness.STALE.value


def test_freshness_unknown_without_measured_timestamp() -> None:
    summary = build_gap_summary(
        aggregate_gaps([_feedback(GapDimension("SCENE", "kitchen", 1, 5))], {}),
        measured_coverage=None,
        now=NOW,
        stale_after=STALE_AFTER,
    )
    assert summary.freshness == GapFreshness.UNKNOWN.value


def test_freshness_uses_oldest_measured_dimension() -> None:
    """任一维度过期，整体就不算最新（保守口径，不掩盖陈旧维度）。"""
    measured = [
        MeasuredCoverage("SCENE", "kitchen", 1, NOW - timedelta(hours=1)),
        MeasuredCoverage("ROBOT", "r-01", 1, NOW - timedelta(days=5)),
    ]
    items = aggregate_gaps(
        [
            _feedback(
                GapDimension("SCENE", "kitchen", None, 5),
                GapDimension("ROBOT", "r-01", None, 5),
            )
        ],
        {},
        measured_coverage=measured,
    )
    summary = build_gap_summary(items, measured_coverage=measured, now=NOW, stale_after=STALE_AFTER)
    assert summary.freshness == GapFreshness.STALE.value


def test_freshness_ignores_unrelated_dimensions() -> None:
    """未参与本次聚合的维度再陈旧，也不影响结果。"""
    measured = [
        MeasuredCoverage("SCENE", "kitchen", 1, NOW - timedelta(minutes=5)),
        MeasuredCoverage("ROBOT", "r-99", 1, NOW - timedelta(days=30)),
    ]
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", None, 5))],
        {},
        measured_coverage=measured,
    )
    summary = build_gap_summary(items, measured_coverage=measured, now=NOW, stale_after=STALE_AFTER)
    assert summary.freshness == GapFreshness.FRESH.value


@pytest.mark.parametrize(
    ("measured_at", "stale_after", "expected"),
    [
        (NOW - timedelta(seconds=1), STALE_AFTER, GapFreshness.FRESH.value),
        (NOW - STALE_AFTER, STALE_AFTER, GapFreshness.FRESH.value),
        (NOW - STALE_AFTER - timedelta(seconds=1), STALE_AFTER, GapFreshness.STALE.value),
        # 未来时间戳（时钟漂移）不判过期。
        (NOW + timedelta(hours=5), STALE_AFTER, GapFreshness.FRESH.value),
        # naive 时间无法可靠比较，按 UNKNOWN 处理。
        (datetime(2026, 11, 16, 12, 0), STALE_AFTER, GapFreshness.UNKNOWN.value),
        # 未配置阈值：判不了就不假装最新。
        (NOW, None, GapFreshness.UNKNOWN.value),
    ],
)
def test_coverage_freshness_boundaries(
    measured_at: datetime,
    stale_after: timedelta | None,
    expected: str,
) -> None:
    assert coverage_freshness(measured_at, now=NOW, stale_after=stale_after) == expected


def test_backward_compatible_call_without_measured_coverage() -> None:
    """既有调用点（只传两个位置参数）行为不变。"""
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", current_count=2, target_count=10))],
        {},
    )
    assert items[0].suggested_episode_count == 8
    assert items[0].coverage_source == CoverageSource.MANUAL.value


def test_freshness_unknown_when_any_participating_dimension_lacks_timestamp() -> None:
    """有计数但缺时间戳时不得报 FRESH（回归）。

    缺陷：``_freshness_anchor`` 只收集非空时间戳并取最小值，缺时间戳的维度被
    静默跳过，于是「一部分数据新鲜」被当成「整体新鲜」。缺时间戳的那部分可能
    已经很旧，报 FRESH 就是拿部分证据冒充全体结论。
    """
    measured = [
        MeasuredCoverage("SCENE", "kitchen", 1, NOW - timedelta(minutes=5)),
        MeasuredCoverage("ROBOT", "r-01", 1, None),
    ]
    items = aggregate_gaps(
        [
            _feedback(
                GapDimension("SCENE", "kitchen", None, 5),
                GapDimension("ROBOT", "r-01", None, 5),
            )
        ],
        {},
        measured_coverage=measured,
    )
    summary = build_gap_summary(items, measured_coverage=measured, now=NOW, stale_after=STALE_AFTER)
    assert summary.freshness == GapFreshness.UNKNOWN.value
    # 计数本身仍然是可信的实测值，只是新鲜度无从判定。
    assert {item.coverage_source for item in summary.items} == {CoverageSource.MEASURED.value}


def test_freshness_ignores_missing_timestamp_on_unrelated_dimension() -> None:
    """未参与本次聚合的维度缺时间戳，不影响本次结论。"""
    measured = [
        MeasuredCoverage("SCENE", "kitchen", 1, NOW - timedelta(minutes=5)),
        MeasuredCoverage("ROBOT", "r-99", 1, None),
    ]
    items = aggregate_gaps(
        [_feedback(GapDimension("SCENE", "kitchen", None, 5))],
        {},
        measured_coverage=measured,
    )
    summary = build_gap_summary(items, measured_coverage=measured, now=NOW, stale_after=STALE_AFTER)
    assert summary.freshness == GapFreshness.FRESH.value
