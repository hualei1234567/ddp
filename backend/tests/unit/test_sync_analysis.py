"""多路时间同步增强分析（N4-003）。

关键语义：**判不了就是判不了**。时钟域冲突、缺帧率、帧索引越界、负漂移
都必须显式 ``UNKNOWN``/``OUT_OF_RANGE``/``UNSTABLE``，不能因为纳秒数值
"看起来接近"就报对齐；同时分析只产出只读校正建议，绝不改写原始时间。
"""

from __future__ import annotations

import json
import math

import pytest
from app.domain.episodes.sync_analysis import (
    ClockDomainStatus,
    DriftStatus,
    FrameIndexStatus,
    FrameRate,
    StreamSyncProfile,
    analyze_stream_sync,
)
from app.domain.episodes.sync_drift import (
    DEFAULT_TOLERANCE_NS,
    StreamWindow,
    SyncDriftError,
    SyncDriftStatus,
)

_MS = 1_000_000
_SEC = 1_000_000_000


def _win(stream_id: str, start_ms: int, duration_ms: int) -> StreamWindow:
    return StreamWindow(
        stream_id,
        start_ms * _MS,
        (start_ms + duration_ms) * _MS,
    )


def _profile(
    *,
    fps: float | None = 30.0,
    clock_source: str | None = "PTP",
    time_domain: str | None = "UTC",
    **overrides: object,
) -> StreamSyncProfile:
    return StreamSyncProfile(
        frame_rate=FrameRate.from_fps(fps),
        clock_source=clock_source,
        time_domain=time_domain,
        **overrides,  # type: ignore[arg-type]
    )


def test_aligned_streams_derive_frames_exactly() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 100, 1000)],
        {"main": _profile(), "wrist": _profile()},
    )

    assert report.status is SyncDriftStatus.EXCEEDED  # 100ms 超过默认 40ms
    assert report.frame_index_status is FrameIndexStatus.DERIVED
    by_id = {item.stream_id: item for item in report.streams}
    assert by_id["main"].frame_count_derived == 30
    assert by_id["wrist"].frame_count_derived == 30
    # 100ms @ 30fps = 3 帧，精确有理数，无浮点尾差。
    assert by_id["wrist"].start_offset_frames is not None
    assert by_id["wrist"].start_offset_frames == 3
    assert by_id["wrist"].start_offset_frames.denominator == 1


def test_same_clock_source_and_domain_is_consistent() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {
            "main": _profile(clock_source="PTP", time_domain="UTC", clock_id="ptp0"),
            "wrist": _profile(clock_source="PTP", time_domain="UTC", clock_id="ptp0"),
        },
    )

    assert report.clock_domain_status is ClockDomainStatus.CONSISTENT
    assert report.status is SyncDriftStatus.ALIGNED
    assert report.issues == ()


def test_different_clock_sources_are_not_comparable() -> None:
    """不同时钟域的纳秒相减没有意义：显式 UNKNOWN，禁止给出校正建议。"""
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {
            "main": _profile(clock_source="PTP", time_domain="UTC"),
            "wrist": _profile(clock_source="NTP", time_domain="UTC"),
        },
    )

    assert report.clock_domain_status is ClockDomainStatus.MIXED
    assert report.status is SyncDriftStatus.UNKNOWN
    assert "CLOCK_DOMAIN_MIXED" in report.issues
    assert report.correction_available is False
    assert report.correction_unavailable_reason == "CLOCK_DOMAIN_MIXED"
    assert all(item.correction is None for item in report.streams)


def test_different_clock_ids_are_mixed() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {
            "main": _profile(clock_source="PTP", time_domain="UTC", clock_id="ptp0"),
            "wrist": _profile(clock_source="PTP", time_domain="UTC", clock_id="ptp1"),
        },
    )

    assert report.clock_domain_status is ClockDomainStatus.MIXED
    assert report.status is SyncDriftStatus.UNKNOWN


def test_partial_clock_declaration_is_unknown_not_consistent() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {"main": _profile(clock_source="PTP", time_domain="UTC")},
    )

    assert report.clock_domain_status is ClockDomainStatus.UNKNOWN
    assert "CLOCK_PARTIAL" in report.issues
    # 偏移仍可测量，未声明时钟不改变既有通过口径。
    assert report.status is SyncDriftStatus.ALIGNED


def test_no_clock_declaration_is_explicitly_unknown() -> None:
    report = analyze_stream_sync([_win("main", 0, 1000), _win("wrist", 0, 1000)])

    assert report.clock_domain_status is ClockDomainStatus.UNKNOWN
    assert report.drift_status is DriftStatus.UNKNOWN
    assert report.frame_index_status is FrameIndexStatus.UNKNOWN
    assert "FRAME_RATE_UNKNOWN" in report.issues
    assert report.status is SyncDriftStatus.ALIGNED


def test_missing_frame_rate_makes_frame_index_unknown_without_failing_sync() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {"main": _profile(), "wrist": _profile(fps=None)},
    )

    assert report.frame_index_status is FrameIndexStatus.UNKNOWN
    assert "FRAME_RATE_UNKNOWN" in report.issues
    by_id = {item.stream_id: item for item in report.streams}
    assert by_id["main"].frame_count_derived == 30
    assert by_id["wrist"].frame_count_derived is None
    assert by_id["wrist"].start_offset_frames is None
    assert "FRAME_RATE_UNKNOWN" in by_id["wrist"].issues
    # 缺帧率不是"同步失败"，纳秒偏移仍然可判定。
    assert report.status is SyncDriftStatus.ALIGNED


def test_inconsistent_frame_rates_cannot_share_one_frame_grid() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {"main": _profile(fps=30), "wrist": _profile(fps=60)},
    )

    assert report.frame_index_status is FrameIndexStatus.INCONSISTENT
    assert "FRAME_RATE_INCONSISTENT" in report.issues


def test_declared_frame_count_smaller_than_derived_is_out_of_range() -> None:
    """声明帧数少于时长×帧率推出的帧数：元数据自相矛盾，不能校正。"""
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {
            "main": _profile(declared_frame_count=10),
            "wrist": _profile(declared_frame_count=30),
        },
    )

    assert report.frame_index_status is FrameIndexStatus.OUT_OF_RANGE
    assert report.status is SyncDriftStatus.UNKNOWN
    assert "FRAME_INDEX_OUT_OF_RANGE" in report.issues
    assert report.correction_available is False
    assert report.correction_unavailable_reason == "FRAME_INDEX_OUT_OF_RANGE"


def test_drift_equal_to_tolerance_is_not_exceeded() -> None:
    """边界口径与既有 drift 一致：严格大于才超限。"""
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {"main": _profile(drift_ppm=0), "wrist": _profile(drift_ppm=40_000)},
    )

    assert report.drift_status is DriftStatus.CONSISTENT
    assert report.status is SyncDriftStatus.ALIGNED
    relative = {item.stream_id: item.relative_drift_ns for item in report.streams}
    assert relative["wrist"] == DEFAULT_TOLERANCE_NS


def test_drift_above_tolerance_is_exceeded_and_needs_resampling() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {"main": _profile(drift_ppm=0), "wrist": _profile(drift_ppm=40_001)},
    )

    assert report.drift_status is DriftStatus.EXCEEDED
    assert report.status is SyncDriftStatus.EXCEEDED
    assert "DRIFT_EXCEEDED" in report.issues
    # 起点裁剪修不了持续漂移，必须显式拒绝给出"伪校正"。
    assert report.correction_available is False
    assert report.correction_unavailable_reason == "DRIFT_REQUIRES_RESAMPLING"


def test_declared_drift_is_unknown_when_clock_domains_differ() -> None:
    """时钟域不可比时，两个 ppm 的差没有物理含义，不能算出"漂移合格"。"""
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 0, 1000)],
        {
            "main": _profile(clock_source="PTP", drift_ppm=0),
            "wrist": _profile(clock_source="NTP", drift_ppm=100),
        },
    )

    assert report.drift_status is DriftStatus.UNKNOWN
    assert report.status is SyncDriftStatus.UNKNOWN
    by_id = {item.stream_id: item for item in report.streams}
    assert "DRIFT_UNKNOWN" in by_id["wrist"].issues


def test_negative_drift_that_reverses_offset_is_unstable() -> None:
    """漂移让相对偏移在窗口内反向：不存在单一恒定校正量。"""
    report = analyze_stream_sync(
        [_win("main", 0, 10_000), _win("wrist", 33, 10_000)],
        {"main": _profile(drift_ppm=0), "wrist": _profile(drift_ppm=-5_000)},
    )

    assert report.drift_status is DriftStatus.UNSTABLE
    assert report.status is SyncDriftStatus.UNKNOWN
    assert "NEGATIVE_DRIFT" in report.issues
    assert report.correction_unavailable_reason == "NEGATIVE_DRIFT"
    relative = {item.stream_id: item.relative_drift_ns for item in report.streams}
    assert relative["wrist"] == -50 * _MS


def test_correction_is_a_proposal_never_applied() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 50, 1200)],
        {"main": _profile(), "wrist": _profile()},
    )

    by_id = {item.stream_id: item for item in report.streams}
    correction = by_id["wrist"].correction
    assert correction is not None
    assert correction.trim_start_ns == 50 * _MS
    assert correction.trim_start_frames == 1  # 1.5 帧向下取整
    assert correction.frame_aligned is False
    assert correction.tail_trim_ns == 200 * _MS
    assert correction.tail_pad_ns == 0
    assert correction.applied is False
    # 原始窗口没有被改写。
    assert by_id["wrist"].base.start_offset_ns == 50 * _MS


def test_duration_shortage_proposes_padding_not_trimming() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1200), _win("wrist", 0, 1000)],
        {"main": _profile(), "wrist": _profile()},
    )

    wrist = next(item for item in report.streams if item.stream_id == "wrist")
    assert wrist.correction is not None
    assert wrist.correction.tail_trim_ns == 0
    assert wrist.correction.tail_pad_ns == 200 * _MS


def test_single_stream_has_no_sync_correction() -> None:
    report = analyze_stream_sync([_win("main", 0, 1000)], {"main": _profile()})

    assert report.status is SyncDriftStatus.SINGLE_STREAM
    assert report.frame_index_status is FrameIndexStatus.DERIVED
    assert report.correction_available is False
    assert report.correction_unavailable_reason == "ONLY_ONE_STREAM"
    assert report.streams[0].correction is None


def test_single_stream_with_contradictory_frame_count_is_unknown() -> None:
    """单流没有同步可言，但"声明帧数 < 时长×帧率"仍是不可判定事实。"""
    report = analyze_stream_sync(
        [_win("main", 0, 1000)],
        {"main": _profile(declared_frame_count=10)},
    )

    assert report.frame_index_status is FrameIndexStatus.OUT_OF_RANGE
    assert report.status is SyncDriftStatus.UNKNOWN
    assert "FRAME_INDEX_OUT_OF_RANGE" in report.issues
    assert report.correction_unavailable_reason == "FRAME_INDEX_OUT_OF_RANGE"


def test_result_is_independent_of_input_order() -> None:
    windows = [_win("main", 0, 1000), _win("wrist", 10, 1000), _win("stereo", 5, 1000)]
    profiles = {
        "main": _profile(),
        "wrist": _profile(),
        "stereo": _profile(),
    }
    forward = analyze_stream_sync(windows, profiles).to_payload()
    backward = analyze_stream_sync(list(reversed(windows)), profiles).to_payload()
    assert forward == backward


def test_payload_is_json_serializable_and_stable() -> None:
    report = analyze_stream_sync(
        [_win("main", 0, 1000), _win("wrist", 10, 1000)],
        {"main": _profile(), "wrist": _profile()},
    )
    payload = report.to_payload()

    assert set(payload) == {
        "status",
        "clock_domain_status",
        "frame_index_status",
        "drift_status",
        "tolerance_ns",
        "reference_stream_id",
        "correction_available",
        "correction_unavailable_reason",
        "issues",
        "base",
        "streams",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert "NaN" not in encoded
    stream = payload["streams"][0]
    assert isinstance(stream, dict)
    assert stream["start_offset_frames"] == {"numerator": 0, "denominator": 1}


def test_fractional_frame_rate_is_converted_exactly() -> None:
    """29.97 必须按声明值转有理数，不能用浮点 29.969999... 参与换算。"""
    rate = FrameRate.from_fps(29.97)

    assert rate is not None
    assert (rate.num, rate.den) == (2997, 100)
    assert rate.frame_count(_SEC) == 29


def test_unknown_profile_stream_is_rejected() -> None:
    with pytest.raises(SyncDriftError, match="不存在的流"):
        analyze_stream_sync([_win("main", 0, 1000)], {"ghost": _profile()})


def test_invalid_frame_rate_type_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        FrameRate.from_fps("30")  # type: ignore[arg-type]


def test_non_positive_frame_rate_is_unknown_not_aligned() -> None:
    assert FrameRate.from_fps(0) is None
    assert FrameRate.from_fps(-1) is None
    assert FrameRate.from_fps(math.inf) is None


def test_negative_declared_frame_count_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        StreamSyncProfile(declared_frame_count=-1)


def test_non_finite_drift_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        StreamSyncProfile(drift_ppm=float("nan"))
    with pytest.raises(SyncDriftError):
        StreamSyncProfile(drift_ppm=float("inf"))


def test_unknown_clock_source_value_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        StreamSyncProfile(clock_source="MAGIC")
