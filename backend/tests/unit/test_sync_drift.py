"""多路媒体流同步偏差检测（N3）。

关键语义：**"没有数据"不等于"对齐"**。缺流返回 UNKNOWN、单流返回
SINGLE_STREAM，都不报 ALIGNED——否则质量报告会把"没检查"说成"检查通过"。
"""

from __future__ import annotations

import pytest
from app.domain.episodes.sync_drift import (
    DEFAULT_TOLERANCE_NS,
    StreamWindow,
    SyncDriftError,
    SyncDriftStatus,
    detect_sync_drift,
)

_MS = 1_000_000
_SEC = 1_000_000_000


def _win(stream_id: str, start_ms: int, duration_ms: int) -> StreamWindow:
    return StreamWindow(stream_id, start_ms * _MS, (start_ms + duration_ms) * _MS)


def test_aligned_streams_pass() -> None:
    report = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 0, 1000)])
    assert report.status is SyncDriftStatus.ALIGNED
    assert report.aligned is True
    assert report.issues == ()


def test_small_offset_within_tolerance_is_aligned() -> None:
    """10ms 偏移在默认 40ms 容忍内，应判对齐。"""
    report = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 10, 1000)])
    assert report.status is SyncDriftStatus.ALIGNED
    assert report.max_start_offset_ns == 10 * _MS


def test_large_offset_is_exceeded() -> None:
    report = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 500, 1000)])
    assert report.status is SyncDriftStatus.EXCEEDED
    assert "START_OFFSET_EXCEEDED" in report.issues
    assert report.max_start_offset_ns == 500 * _MS


def test_duration_mismatch_is_detected() -> None:
    """起点对齐但时长差 200ms，也必须报出来。"""
    report = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 0, 1200)])
    assert report.status is SyncDriftStatus.EXCEEDED
    assert "DURATION_DELTA_EXCEEDED" in report.issues


def test_both_problems_are_reported_together() -> None:
    report = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 500, 2000)])
    assert set(report.issues) == {"START_OFFSET_EXCEEDED", "DURATION_DELTA_EXCEEDED"}


def test_no_streams_is_unknown_not_aligned() -> None:
    """没有流不等于对齐——失败关闭。"""
    report = detect_sync_drift([])
    assert report.status is SyncDriftStatus.UNKNOWN
    assert report.aligned is False
    assert report.issues == ("NO_STREAMS",)


def test_single_stream_is_not_aligned() -> None:
    """单流没有"同步"这一说，不能报通过。"""
    report = detect_sync_drift([_win("main", 0, 1000)])
    assert report.status is SyncDriftStatus.SINGLE_STREAM
    assert report.aligned is False
    assert report.issues == ("ONLY_ONE_STREAM",)


def test_reference_is_earliest_start_then_stream_id() -> None:
    """基准选择必须确定性：起点最早，并列时按 stream_id 字典序。"""
    report = detect_sync_drift([_win("zebra", 0, 1000), _win("alpha", 0, 1000)])
    assert report.reference_stream_id == "alpha"


def test_result_is_independent_of_input_order() -> None:
    """同输入集合、不同顺序，结果必须一致（可复现）。"""
    windows = [_win("main", 0, 1000), _win("wrist", 10, 1000), _win("stereo", 5, 1000)]
    forward = detect_sync_drift(windows).to_payload()
    backward = detect_sync_drift(list(reversed(windows))).to_payload()
    assert forward == backward


def test_per_stream_drift_is_reported() -> None:
    report = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 500, 1000)])
    by_id = {item.stream_id: item for item in report.streams}
    assert by_id["main"].start_offset_ns == 0
    assert by_id["wrist"].start_offset_ns == 500 * _MS


def test_custom_tolerance_is_honoured() -> None:
    windows = [_win("main", 0, 1000), _win("wrist", 100, 1000)]
    strict = detect_sync_drift(windows, tolerance_ns=50 * _MS)
    loose = detect_sync_drift(windows, tolerance_ns=200 * _MS)
    assert strict.status is SyncDriftStatus.EXCEEDED
    assert loose.status is SyncDriftStatus.ALIGNED


def test_duplicate_stream_ids_are_rejected() -> None:
    with pytest.raises(SyncDriftError) as excinfo:
        detect_sync_drift([_win("main", 0, 1000), _win("main", 0, 1000)])
    assert "重复" in str(excinfo.value)


def test_inverted_window_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        StreamWindow("main", 1000, 500)


def test_empty_stream_id_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        StreamWindow("", 0, 1000)


def test_negative_tolerance_is_rejected() -> None:
    with pytest.raises(SyncDriftError):
        detect_sync_drift([_win("main", 0, 1000)], tolerance_ns=-1)


def test_payload_shape_is_stable() -> None:
    payload = detect_sync_drift([_win("main", 0, 1000), _win("wrist", 500, 1000)]).to_payload()
    assert set(payload) == {
        "status",
        "tolerance_ns",
        "reference_stream_id",
        "max_start_offset_ns",
        "max_duration_delta_ns",
        "streams",
        "issues",
    }
    assert payload["tolerance_ns"] == DEFAULT_TOLERANCE_NS
