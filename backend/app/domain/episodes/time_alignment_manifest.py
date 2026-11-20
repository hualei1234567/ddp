"""版本化多流时间事实 manifest（DDP-V21-N4-011 前置契约）。

该模块只把不可信 JSON 转为既有 ``StreamWindow`` / ``StreamSyncProfile``
值对象，不执行同步分析，也不补齐缺失事实。分组、时间窗口和时钟声明必须由
上游明确提供；缺失可选声明保持 ``None``，由 N4-003 产出 ``UNKNOWN``。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.episodes.sync_drift import StreamWindow, SyncDriftError
from app.domain.episodes.sync_frame_index import FrameRate, StreamSyncProfile

TIME_ALIGNMENT_INPUT_SCHEMA_VERSION = "ddp.processing.time-alignment-input/1.0.0"
TIME_ALIGNMENT_INPUT_TIME_UNIT = "ns"
_MAX_KEY_LENGTH = 200
_DECIMAL_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_ROOT_KEYS = frozenset({"schema_version", "time_unit", "groups"})
_GROUP_KEYS = frozenset({"group_key", "streams"})
_STREAM_REQUIRED_KEYS = frozenset({"stream_id", "start_time_ns", "end_time_ns"})
_STREAM_KEYS = _STREAM_REQUIRED_KEYS | frozenset(
    {
        "frame_rate",
        "declared_frame_count",
        "time_domain",
        "clock_source",
        "clock_id",
        "drift_ppm",
    }
)
_FRAME_RATE_KEYS = frozenset({"num", "den"})


@dataclass(frozen=True, slots=True)
class TimeAlignmentGroup:
    """一个由上游稳定键明确声明的多流比较组。"""

    group_key: str
    windows: tuple[StreamWindow, ...]
    profiles: Mapping[str, StreamSyncProfile]


@dataclass(frozen=True, slots=True)
class TimeAlignmentManifest:
    """已严格校验的时间对齐输入；不包含任何推断值。"""

    schema_version: str
    time_unit: str
    groups: tuple[TimeAlignmentGroup, ...]


def parse_time_alignment_manifest(document: object) -> TimeAlignmentManifest:
    """严格解析时间事实 manifest；结构或语义非法时失败关闭。"""
    root = _mapping(document, "manifest")
    _exact_keys(root, required=_ROOT_KEYS, allowed=_ROOT_KEYS, field="manifest")
    if root["schema_version"] != TIME_ALIGNMENT_INPUT_SCHEMA_VERSION:
        raise SyncDriftError("schema_version 不受支持")
    if root["time_unit"] != TIME_ALIGNMENT_INPUT_TIME_UNIT:
        raise SyncDriftError("time_unit 必须是 ns")

    raw_groups = _sequence(root["groups"], "groups")
    groups: list[TimeAlignmentGroup] = []
    seen_groups: set[str] = set()
    for index, raw_group in enumerate(raw_groups):
        group = _parse_group(raw_group, index=index)
        if group.group_key in seen_groups:
            raise SyncDriftError(f"group_key 重复：{group.group_key}")
        seen_groups.add(group.group_key)
        groups.append(group)
    return TimeAlignmentManifest(
        schema_version=TIME_ALIGNMENT_INPUT_SCHEMA_VERSION,
        time_unit=TIME_ALIGNMENT_INPUT_TIME_UNIT,
        groups=tuple(groups),
    )


def _parse_group(value: object, *, index: int) -> TimeAlignmentGroup:
    field = f"groups[{index}]"
    group = _mapping(value, field)
    _exact_keys(group, required=_GROUP_KEYS, allowed=_GROUP_KEYS, field=field)
    group_key = _text(group["group_key"], f"{field}.group_key")
    raw_streams = _sequence(group["streams"], f"{field}.streams")
    if not raw_streams:
        raise SyncDriftError(f"{field}.streams 不得为空")

    windows: list[StreamWindow] = []
    profiles: dict[str, StreamSyncProfile] = {}
    for stream_index, raw_stream in enumerate(raw_streams):
        stream_id, window, profile = _parse_stream(
            raw_stream,
            field=f"{field}.streams[{stream_index}]",
        )
        if stream_id in profiles:
            raise SyncDriftError(f"{field} 内 stream_id 重复：{stream_id}")
        windows.append(window)
        profiles[stream_id] = profile
    return TimeAlignmentGroup(group_key, tuple(windows), profiles)


def _parse_stream(
    value: object,
    *,
    field: str,
) -> tuple[str, StreamWindow, StreamSyncProfile]:
    stream = _mapping(value, field)
    _exact_keys(stream, required=_STREAM_REQUIRED_KEYS, allowed=_STREAM_KEYS, field=field)
    stream_id = _text(stream["stream_id"], f"{field}.stream_id")
    window = StreamWindow(
        stream_id=stream_id,
        start_time_ns=_decimal_integer(stream["start_time_ns"], f"{field}.start_time_ns"),
        end_time_ns=_decimal_integer(stream["end_time_ns"], f"{field}.end_time_ns"),
    )
    profile = StreamSyncProfile(
        frame_rate=_frame_rate(stream.get("frame_rate"), f"{field}.frame_rate"),
        declared_frame_count=_optional_non_negative_int(
            stream.get("declared_frame_count"),
            f"{field}.declared_frame_count",
        ),
        time_domain=stream.get("time_domain"),  # type: ignore[arg-type]
        clock_source=stream.get("clock_source"),  # type: ignore[arg-type]
        clock_id=stream.get("clock_id"),  # type: ignore[arg-type]
        drift_ppm=_optional_finite_number(stream.get("drift_ppm"), f"{field}.drift_ppm"),
    )
    return stream_id, window, profile


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise SyncDriftError(f"{field} 必须是 JSON 对象")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list | tuple):
        raise SyncDriftError(f"{field} 必须是数组")
    return value


def _exact_keys(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    field: str,
) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise SyncDriftError(f"{field} 缺少字段：{'、'.join(missing)}")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SyncDriftError(f"{field} 包含未知字段：{'、'.join(unknown)}")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SyncDriftError(f"{field} 必须是无首尾空白的非空字符串")
    if len(value) > _MAX_KEY_LENGTH:
        raise SyncDriftError(f"{field} 超过 {_MAX_KEY_LENGTH} 字符")
    return value


def _decimal_integer(value: object, field: str) -> int:
    if not isinstance(value, str) or _DECIMAL_INTEGER.fullmatch(value) is None:
        raise SyncDriftError(f"{field} 必须是规范十进制整数字符串")
    return int(value, 10)


def _frame_rate(value: object, field: str) -> FrameRate | None:
    if value is None:
        return None
    rate = _mapping(value, field)
    _exact_keys(rate, required=_FRAME_RATE_KEYS, allowed=_FRAME_RATE_KEYS, field=field)
    return FrameRate(num=rate["num"], den=rate["den"])  # type: ignore[arg-type]


def _optional_non_negative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SyncDriftError(f"{field} 必须是非负整数或 null")
    return value


def _optional_finite_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SyncDriftError(f"{field} 必须是数字或 null")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise SyncDriftError(f"{field} 必须是有限数值")
    return resolved


__all__ = [
    "TIME_ALIGNMENT_INPUT_SCHEMA_VERSION",
    "TIME_ALIGNMENT_INPUT_TIME_UNIT",
    "TimeAlignmentGroup",
    "TimeAlignmentManifest",
    "parse_time_alignment_manifest",
]
