"""多路媒体流的时间同步偏差检测（N3）。

**它回答什么问题**

一个 Episode 通常由多路媒体流组成（main / wrist / stereo …）。如果各路
视频的起始时刻不一致，拼起来就会出现"不同步"——人眼能看出，但人工逐条
核对不可行。本模块把"这组流是否对齐"变成一个可计算、可复现、可加断言的
结论。

**为什么放在领域层**

纯函数、无 IO、无框架依赖：输入是各流的时间区间，输出是偏差结论。放在这里
才能被 Episode 创建、数据集准入、质量报告共同复用，而不是各写一份阈值判断。

**不清零、不四舍五入、不猜**

- 缺少任意一路的区间 → ``UNKNOWN``，不当作"对齐"（失败关闭）；
- 只有一路 → ``SINGLE_STREAM``，没有"同步"这个概念，不报 PASS；
- 偏差超出容忍 → ``EXCEEDED`` 并给出具体差值，便于定位是哪一路。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# 默认容忍阈值：40 毫秒。30fps 下约 1.2 帧，人眼通常察觉不到；
# 超过这个量级在慢动作回放与动作标注里就会明显错位。
DEFAULT_TOLERANCE_NS = 40_000_000


class SyncDriftStatus(StrEnum):
    ALIGNED = "ALIGNED"
    EXCEEDED = "EXCEEDED"
    UNKNOWN = "UNKNOWN"
    SINGLE_STREAM = "SINGLE_STREAM"


class SyncDriftError(Exception):
    """输入结构非法（负区间、空标识等）。"""


@dataclass(frozen=True, slots=True)
class StreamWindow:
    """一路媒体流的时间窗口；``stream_id`` 是机位/轨道标识。"""

    stream_id: str
    start_time_ns: int
    end_time_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.stream_id, str) or not self.stream_id.strip():
            raise SyncDriftError("stream_id 必须是非空字符串")
        for name in ("start_time_ns", "end_time_ns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise SyncDriftError(f"{name} 必须是整数")
        if self.start_time_ns >= self.end_time_ns:
            raise SyncDriftError(f"{self.stream_id} 的 start_time_ns 必须小于 end_time_ns")

    @property
    def duration_ns(self) -> int:
        return self.end_time_ns - self.start_time_ns


@dataclass(frozen=True, slots=True)
class StreamDrift:
    """单路流相对基准的偏差。"""

    stream_id: str
    start_offset_ns: int
    duration_delta_ns: int


@dataclass(frozen=True, slots=True)
class SyncDriftReport:
    status: SyncDriftStatus
    tolerance_ns: int
    # 基准流：起点最早的那路。用它当基准是确定性的（同输入必同输出）。
    reference_stream_id: str | None
    max_start_offset_ns: int | None
    max_duration_delta_ns: int | None
    streams: tuple[StreamDrift, ...]
    issues: tuple[str, ...]

    @property
    def aligned(self) -> bool:
        return self.status is SyncDriftStatus.ALIGNED

    def to_payload(self) -> dict[str, object]:
        """稳定形状，供 JSON 落库或写进质量报告。"""
        return {
            "status": self.status.value,
            "tolerance_ns": self.tolerance_ns,
            "reference_stream_id": self.reference_stream_id,
            "max_start_offset_ns": self.max_start_offset_ns,
            "max_duration_delta_ns": self.max_duration_delta_ns,
            "streams": [
                {
                    "stream_id": item.stream_id,
                    "start_offset_ns": item.start_offset_ns,
                    "duration_delta_ns": item.duration_delta_ns,
                }
                for item in self.streams
            ],
            "issues": list(self.issues),
        }


def detect_sync_drift(
    windows: tuple[StreamWindow, ...] | list[StreamWindow],
    *,
    tolerance_ns: int = DEFAULT_TOLERANCE_NS,
) -> SyncDriftReport:
    """检测多路流的起点偏移与时长差异。

    判定规则（全部确定性，同输入必同输出）：

    1. 0 路 → ``UNKNOWN``（没有数据不等于对齐）；
    2. 1 路 → ``SINGLE_STREAM``（没有可比较对象）；
    3. 起点最早的那路为基准；**最大起点偏移**与**最大时长差**都不得超过
       ``tolerance_ns``，否则 ``EXCEEDED``；
    4. 起点相同时按 ``stream_id`` 字典序取基准，避免结果依赖输入顺序。
    """
    if isinstance(tolerance_ns, bool) or not isinstance(tolerance_ns, int):
        raise SyncDriftError("tolerance_ns 必须是整数")
    if tolerance_ns < 0:
        raise SyncDriftError("tolerance_ns 不能为负")

    ordered = tuple(windows)
    if not ordered:
        return SyncDriftReport(
            status=SyncDriftStatus.UNKNOWN,
            tolerance_ns=tolerance_ns,
            reference_stream_id=None,
            max_start_offset_ns=None,
            max_duration_delta_ns=None,
            streams=(),
            issues=("NO_STREAMS",),
        )
    _assert_unique_streams(ordered)
    if len(ordered) == 1:
        only = ordered[0]
        return SyncDriftReport(
            status=SyncDriftStatus.SINGLE_STREAM,
            tolerance_ns=tolerance_ns,
            reference_stream_id=only.stream_id,
            max_start_offset_ns=0,
            max_duration_delta_ns=0,
            streams=(StreamDrift(only.stream_id, 0, 0),),
            issues=("ONLY_ONE_STREAM",),
        )

    reference = min(ordered, key=lambda item: (item.start_time_ns, item.stream_id))
    drifts = tuple(
        StreamDrift(
            stream_id=item.stream_id,
            start_offset_ns=item.start_time_ns - reference.start_time_ns,
            duration_delta_ns=item.duration_ns - reference.duration_ns,
        )
        for item in sorted(ordered, key=lambda w: w.stream_id)
    )
    max_start = max(abs(item.start_offset_ns) for item in drifts)
    max_duration = max(abs(item.duration_delta_ns) for item in drifts)
    issues: list[str] = []
    if max_start > tolerance_ns:
        issues.append("START_OFFSET_EXCEEDED")
    if max_duration > tolerance_ns:
        issues.append("DURATION_DELTA_EXCEEDED")
    return SyncDriftReport(
        status=SyncDriftStatus.EXCEEDED if issues else SyncDriftStatus.ALIGNED,
        tolerance_ns=tolerance_ns,
        reference_stream_id=reference.stream_id,
        max_start_offset_ns=max_start,
        max_duration_delta_ns=max_duration,
        streams=drifts,
        issues=tuple(issues),
    )


def _assert_unique_streams(windows: tuple[StreamWindow, ...]) -> None:
    seen: set[str] = set()
    for item in windows:
        if item.stream_id in seen:
            raise SyncDriftError(f"stream_id 重复：{item.stream_id}")
        seen.add(item.stream_id)


__all__ = [
    "DEFAULT_TOLERANCE_NS",
    "StreamDrift",
    "StreamWindow",
    "SyncDriftError",
    "SyncDriftReport",
    "SyncDriftStatus",
    "detect_sync_drift",
]
