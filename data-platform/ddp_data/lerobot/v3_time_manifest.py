"""LeRobot v3 episode 元数据到 DDP 多流时间 manifest 的纯映射。

只使用 ``meta/info.json`` 明确声明的 video feature，以及
``meta/episodes/*.parquet`` 中每路视频各自的 from/to timestamp。不得按字段顺序、
文件位置或全局 FPS 推断不存在的逐流事实。来源没有声明物理时钟，因此时钟域和
时钟源显式标为 ``UNKNOWN``。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from ddp_data.lerobot.parser import LeRobotParseError

_SCHEMA_VERSION = "ddp.processing.time-alignment-input/1.0.0"
_MAX_DATASET_ID_LENGTH = 160
_MAX_STREAM_ID_LENGTH = 200
_MAX_VIDEO_STREAMS = 64
_NS_PER_SECOND = Decimal(1_000_000_000)
_FLOAT_NOISE_TOLERANCE_NS = Decimal("0.001")


def build_v3_time_alignment_manifest(
    *,
    dataset_id: object,
    info: object,
    episode: object,
) -> dict[str, object]:
    """从一个 LeRobot v3 episode 行构造单组 DDP 时间 manifest。"""
    source_id = _text(dataset_id, "dataset_id", max_length=_MAX_DATASET_ID_LENGTH)
    info_map = _mapping(info, "info")
    episode_map = _mapping(episode, "episode")
    features = _mapping(info_map.get("features"), "info.features")
    episode_index = _non_negative_int(episode_map.get("episode_index"), "episode.episode_index")
    frame_count = _positive_int(episode_map.get("length"), "episode.length")

    video_keys = sorted(
        key
        for key, raw_feature in features.items()
        if _is_video_feature(raw_feature)
    )
    if not video_keys:
        raise LeRobotParseError("info.features 未声明 video 流")
    if len(video_keys) > _MAX_VIDEO_STREAMS:
        raise LeRobotParseError(f"video 流数量超过 {_MAX_VIDEO_STREAMS} 上限")

    streams = [
        _stream_document(
            key,
            feature=_mapping(features[key], f"info.features.{key}"),
            episode=episode_map,
            frame_count=frame_count,
        )
        for key in video_keys
    ]
    return {
        "schema_version": _SCHEMA_VERSION,
        "time_unit": "ns",
        "groups": [
            {
                "group_key": f"{source_id}:episode:{episode_index}",
                "streams": streams,
            }
        ],
    }


def _stream_document(
    stream_id: str,
    *,
    feature: Mapping[str, object],
    episode: Mapping[str, object],
    frame_count: int,
) -> dict[str, object]:
    _text(stream_id, "stream_id", max_length=_MAX_STREAM_ID_LENGTH)
    prefix = f"videos/{stream_id}"
    start = _seconds_to_ns(episode.get(f"{prefix}/from_timestamp"), f"{prefix}/from_timestamp")
    end = _seconds_to_ns(episode.get(f"{prefix}/to_timestamp"), f"{prefix}/to_timestamp")
    if start >= end:
        raise LeRobotParseError(f"{prefix} 的 from_timestamp 必须小于 to_timestamp")

    stream: dict[str, object] = {
        "stream_id": stream_id,
        "start_time_ns": str(start),
        "end_time_ns": str(end),
        "declared_frame_count": frame_count,
        "time_domain": "UNKNOWN",
        "clock_source": "UNKNOWN",
    }
    rate = _frame_rate(feature)
    if rate is not None:
        stream["frame_rate"] = {"num": rate.numerator, "den": rate.denominator}
    return stream


def _frame_rate(feature: Mapping[str, object]) -> Fraction | None:
    raw_video_info = feature.get("video_info")
    if raw_video_info is None:
        return None
    video_info = _mapping(raw_video_info, "video_info")
    raw_fps = video_info.get("video.fps")
    if raw_fps is None:
        return None
    fps = _decimal(raw_fps, "video.fps")
    if fps <= 0:
        raise LeRobotParseError("video.fps 必须是正数")
    return Fraction(fps)


def _seconds_to_ns(value: object, field: str) -> int:
    seconds = _decimal(value, field)
    if seconds < 0:
        raise LeRobotParseError(f"{field} 不能为负")
    nanoseconds = seconds * _NS_PER_SECOND
    integral = nanoseconds.to_integral_value()
    difference = abs(nanoseconds - integral)
    allow_float_noise = isinstance(value, float) and difference <= _FLOAT_NOISE_TOLERANCE_NS
    if difference and not allow_float_noise:
        raise LeRobotParseError(f"{field} 不能精确表示为纳秒 timestamp")
    resolved = int(integral)
    if resolved > 2**63 - 1:
        raise LeRobotParseError(f"{field} timestamp 超出 int64 范围")
    return resolved


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise LeRobotParseError(f"{field} timestamp 必须是有限数字")
    if isinstance(value, float) and not math.isfinite(value):
        raise LeRobotParseError(f"{field} timestamp 必须是有限数字")
    try:
        resolved = Decimal(str(value))
    except InvalidOperation:
        raise LeRobotParseError(f"{field} timestamp 必须是有限数字") from None
    if not resolved.is_finite():
        raise LeRobotParseError(f"{field} timestamp 必须是有限数字")
    return resolved


def _is_video_feature(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("dtype") == "video"


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise LeRobotParseError(f"{field} 必须是对象")
    return value


def _text(value: object, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LeRobotParseError(f"{field} 必须是无首尾空白的非空字符串")
    if len(value) > max_length:
        raise LeRobotParseError(f"{field} 超过 {max_length} 字符")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LeRobotParseError(f"{field} 必须是非负整数")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LeRobotParseError(f"{field} 必须是正整数")
    return value


__all__ = ["build_v3_time_alignment_manifest"]
