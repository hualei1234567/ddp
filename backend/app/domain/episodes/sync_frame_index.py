"""帧索引推导与单路流同步声明（N4-003）。

**为什么单独成文件**

帧索引推导只服务于"由声明帧率把纳秒偏移换算成帧"，与时钟域/漂移判定是
两个独立关注点。拆开后每个文件保持在可评审的体量，且 `FrameRate` 可以被
其他只关心帧数换算的用例直接复用，不必拖入整个同步分析。

**精度**

换算全程使用 ``fractions.Fraction``：``29.97`` 这类十进制帧率按声明值转
有理数（分母上限 1,000,000），不会产生浮点尾差；缺失/非正/非有限帧率一律
返回 ``None``，由调用方显式记为 ``UNKNOWN``。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from app.domain.episodes.sync_drift import (
    StreamDrift,
    StreamWindow,
    SyncDriftError,
)
from app.domain.episodes.validation import CLOCK_SOURCES, TIME_DOMAINS

_NS_PER_SECOND = 1_000_000_000
# 浮点帧率转有理数时的分母上限：足以精确表达 29.97 / 59.94 等常见帧率，
# 又不会让极端浮点值产生巨大的整数运算。
_MAX_FPS_DENOMINATOR = 1_000_000
_CLOCK_ID_MAX_LENGTH = 200


class FrameIndexStatus(StrEnum):
    """帧索引映射的可判定程度。"""

    DERIVED = "DERIVED"
    INCONSISTENT = "INCONSISTENT"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class FrameRate:
    """精确帧率（帧/秒），用有理数避免浮点误差。"""

    num: int
    den: int

    def __post_init__(self) -> None:
        for name in ("num", "den"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SyncDriftError(f"frame_rate.{name} 必须是正整数")

    @classmethod
    def from_fps(cls, fps: object) -> FrameRate | None:
        """把声明帧率转成精确有理数；缺失/非正/非有限值返回 ``None``。

        声明为 0 或负数时，帧索引**不可判定**，返回 ``None`` 而不是抛异常：
        让调用方得到显式 ``UNKNOWN``，而不是整批质量检查失败。
        类型非法（字符串、布尔）仍视为输入结构错误。
        """
        if fps is None:
            return None
        if isinstance(fps, bool) or not isinstance(fps, int | float):
            raise SyncDriftError("frame_rate 必须是数字或 None")
        value = float(fps)
        if not math.isfinite(value) or value <= 0:
            return None
        fraction = Fraction(str(value)).limit_denominator(_MAX_FPS_DENOMINATOR)
        return cls(fraction.numerator, fraction.denominator)

    def frame_count(self, duration_ns: int) -> int:
        """按声明帧率推导的帧数（向下取整）。"""
        return (duration_ns * self.num) // (self.den * _NS_PER_SECOND)

    def frame_position(self, offset_ns: int) -> Fraction:
        """偏移对应的帧位置（可为分数）。"""
        return Fraction(offset_ns * self.num, self.den * _NS_PER_SECOND)

    def to_payload(self) -> dict[str, int]:
        return {"num": self.num, "den": self.den}


@dataclass(frozen=True, slots=True)
class StreamSyncProfile:
    """单路流的可选同步声明；缺省字段一律按"未声明"处理。

    所有字段都有默认值，因此历史调用方（只有 ``StreamWindow``）无需改动，
    得到的是显式 ``UNKNOWN`` 而不是异常。
    """

    frame_rate: FrameRate | None = None
    declared_frame_count: int | None = None
    clock_source: str | None = None
    time_domain: str | None = None
    clock_id: str | None = None
    drift_ppm: float | None = None

    def __post_init__(self) -> None:
        if self.frame_rate is not None and not isinstance(self.frame_rate, FrameRate):
            raise SyncDriftError("frame_rate 必须是 FrameRate 或 None")
        if self.declared_frame_count is not None:
            value = self.declared_frame_count
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SyncDriftError("declared_frame_count 必须是非负整数")
        _check_clock_value(self.time_domain, "time_domain", TIME_DOMAINS)
        _check_clock_value(self.clock_source, "clock_source", CLOCK_SOURCES)
        if self.clock_id is not None:
            if not isinstance(self.clock_id, str) or not self.clock_id.strip():
                raise SyncDriftError("clock_id 必须是非空字符串或 None")
            if len(self.clock_id) > _CLOCK_ID_MAX_LENGTH:
                raise SyncDriftError(f"clock_id 超过 {_CLOCK_ID_MAX_LENGTH} 字符")
        if self.drift_ppm is not None:
            value = self.drift_ppm
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise SyncDriftError("drift_ppm 必须是数字或 None")
            if not math.isfinite(float(value)):
                raise SyncDriftError("drift_ppm 必须是有限数值")


@dataclass(frozen=True, slots=True)
class FrameFacts:
    """单路流的帧索引事实；``None`` 表示不可判定。"""

    derived_count: int | None
    declared_count: int | None
    start_offset_frames: Fraction | None
    rate: FrameRate | None


def evaluate_frames(
    windows: Sequence[StreamWindow],
    profiles: Mapping[str, StreamSyncProfile],
    base_by_id: Mapping[str, StreamDrift],
) -> tuple[FrameIndexStatus, dict[str, list[str]], dict[str, FrameFacts]]:
    """逐流推导帧索引，并给出整体可判定程度与每流问题码。"""
    facts: dict[str, FrameFacts] = {}
    issues: dict[str, list[str]] = {item.stream_id: [] for item in windows}
    missing: list[str] = []
    empty: list[str] = []
    out_of_range: list[str] = []
    rates: set[FrameRate] = set()
    for item in windows:
        profile = profiles[item.stream_id]
        rate = profile.frame_rate
        derived: int | None = None
        offset_frames: Fraction | None = None
        if rate is None:
            missing.append(item.stream_id)
            issues[item.stream_id].append("FRAME_RATE_UNKNOWN")
        else:
            rates.add(rate)
            derived = rate.frame_count(item.duration_ns)
            offset_frames = rate.frame_position(base_by_id[item.stream_id].start_offset_ns)
            if derived <= 0:
                empty.append(item.stream_id)
                issues[item.stream_id].append("FRAME_INDEX_EMPTY")
        declared = profile.declared_frame_count
        if derived is not None and declared is not None and derived > declared:
            out_of_range.append(item.stream_id)
            issues[item.stream_id].append("FRAME_INDEX_OUT_OF_RANGE")
        facts[item.stream_id] = FrameFacts(derived, declared, offset_frames, rate)

    if not windows:
        status = FrameIndexStatus.UNKNOWN
    elif out_of_range:
        status = FrameIndexStatus.OUT_OF_RANGE
    elif missing or empty:
        status = FrameIndexStatus.UNKNOWN
    elif len(rates) > 1:
        status = FrameIndexStatus.INCONSISTENT
    else:
        status = FrameIndexStatus.DERIVED
    return status, issues, facts


def _check_clock_value(value: str | None, name: str, allowed: frozenset[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str) or value not in allowed:
        raise SyncDriftError(f"{name} 取值非法")


__all__ = [
    "FrameFacts",
    "FrameIndexStatus",
    "FrameRate",
    "StreamSyncProfile",
    "evaluate_frames",
]
