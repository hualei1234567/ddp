"""多路媒体流时间同步增强分析（N4-003）。

**与 ``sync_drift`` 的关系**

``sync_drift.detect_sync_drift`` 只回答"起点与时长是否对齐"。本模块**复用**
它的结论，补上三个它刻意不涉及的维度：

1. 每路流的时钟声明（``time_domain`` / ``clock_source`` / ``clock_id``）。
   不同时钟域的纳秒时间戳不可直接相减；混用时显式 ``UNKNOWN``，不拿
   "数值上接近"冒充"同一时钟域"。
2. 帧索引推导：由**声明**的帧率把偏移换算为帧。换算全程用精确有理数
   （``fractions.Fraction``），不引入浮点误差；缺帧率时显式 ``UNKNOWN``。
3. 时钟漂移（ppm）：漂移可能让相对偏移在窗口末端反向（负漂移），此时
   不存在单一恒定校正量，同样显式 ``UNKNOWN``。

**只读取、只建议**

本模块产出分析结论与"建议裁剪量"，从不改写原始时间戳、帧索引或媒体文件。
``CorrectionProposal.applied`` 恒为 ``False``，避免把建议误当成已校正。

**为什么不写进 ``time_base``**

``time_base`` 是 Episode 级、契约 ``additionalProperties: false`` 的封闭对象，
把每流映射塞进去等于改契约。每流明细通过既有质量证据 JSON
（``quality_findings.evidence``）承载，无迁移、不篡改历史 Episode。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from app.domain.episodes.sync_drift import (
    DEFAULT_TOLERANCE_NS,
    StreamDrift,
    StreamWindow,
    SyncDriftError,
    SyncDriftReport,
    SyncDriftStatus,
    detect_sync_drift,
)
from app.domain.episodes.sync_frame_index import (
    FrameFacts,
    FrameIndexStatus,
    FrameRate,
    StreamSyncProfile,
    evaluate_frames,
)

_PPM_SCALE = 1_000_000


class ClockDomainStatus(StrEnum):
    """多路流的时钟声明是否可比较。"""

    CONSISTENT = "CONSISTENT"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class DriftStatus(StrEnum):
    """时钟漂移判定。"""

    CONSISTENT = "CONSISTENT"
    EXCEEDED = "EXCEEDED"
    UNSTABLE = "UNSTABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    """只读校正建议：常量裁剪/补齐，**从未应用**到任何原始数据。"""

    trim_start_ns: int
    tail_trim_ns: int
    tail_pad_ns: int
    trim_start_frames: int | None
    frame_aligned: bool
    applied: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "trim_start_ns": self.trim_start_ns,
            "tail_trim_ns": self.tail_trim_ns,
            "tail_pad_ns": self.tail_pad_ns,
            "trim_start_frames": self.trim_start_frames,
            "frame_aligned": self.frame_aligned,
            "applied": self.applied,
        }


@dataclass(frozen=True, slots=True)
class StreamSyncDetail:
    """单路流的增强同步结论。"""

    base: StreamDrift
    clock_source: str | None
    time_domain: str | None
    clock_id: str | None
    drift_ppm: float | None
    relative_drift_ns: int | None
    frame_rate: FrameRate | None
    frame_count_derived: int | None
    frame_count_declared: int | None
    start_offset_frames: Fraction | None
    correction: CorrectionProposal | None
    issues: tuple[str, ...]

    @property
    def stream_id(self) -> str:
        return self.base.stream_id

    def to_payload(self) -> dict[str, object]:
        frames = self.start_offset_frames
        return {
            "stream_id": self.stream_id,
            "start_offset_ns": self.base.start_offset_ns,
            "duration_delta_ns": self.base.duration_delta_ns,
            "clock_source": self.clock_source,
            "time_domain": self.time_domain,
            "clock_id": self.clock_id,
            "drift_ppm": self.drift_ppm,
            "relative_drift_ns": self.relative_drift_ns,
            "frame_rate": self.frame_rate.to_payload() if self.frame_rate else None,
            "frame_count_derived": self.frame_count_derived,
            "frame_count_declared": self.frame_count_declared,
            "start_offset_frames": (
                {"numerator": frames.numerator, "denominator": frames.denominator}
                if frames is not None
                else None
            ),
            "correction": self.correction.to_payload() if self.correction else None,
            "issues": list(self.issues),
        }


@dataclass(frozen=True, slots=True)
class StreamSyncAnalysis:
    """增强同步分析报告；``base`` 保留既有 ``SyncDriftReport`` 原样。"""

    status: SyncDriftStatus
    clock_domain_status: ClockDomainStatus
    frame_index_status: FrameIndexStatus
    drift_status: DriftStatus
    tolerance_ns: int
    reference_stream_id: str | None
    correction_available: bool
    correction_unavailable_reason: str | None
    base: SyncDriftReport
    streams: tuple[StreamSyncDetail, ...]
    issues: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "clock_domain_status": self.clock_domain_status.value,
            "frame_index_status": self.frame_index_status.value,
            "drift_status": self.drift_status.value,
            "tolerance_ns": self.tolerance_ns,
            "reference_stream_id": self.reference_stream_id,
            "correction_available": self.correction_available,
            "correction_unavailable_reason": self.correction_unavailable_reason,
            "issues": list(self.issues),
            "base": self.base.to_payload(),
            "streams": [item.to_payload() for item in self.streams],
        }


def analyze_stream_sync(
    windows: Sequence[StreamWindow],
    profiles: Mapping[str, StreamSyncProfile] | None = None,
    *,
    tolerance_ns: int = DEFAULT_TOLERANCE_NS,
) -> StreamSyncAnalysis:
    """在既有漂移检测之上补充时钟域、帧索引与漂移结论。

    ``profiles`` 以 ``stream_id`` 为键；缺失的流按"未声明"处理（历史数据
    兼容），声明了不存在的流则视为调用错误。
    """
    ordered = tuple(windows)
    base = detect_sync_drift(list(ordered), tolerance_ns=tolerance_ns)
    resolved = _resolve_profiles(ordered, profiles)
    base_by_id = {item.stream_id: item for item in base.streams}

    clock_status, clock_issues = _evaluate_clock_domain(ordered, resolved)
    drift_status, drift_issues, relative_drift = _evaluate_drift(
        ordered,
        resolved,
        base_by_id,
        base.reference_stream_id,
        clock_status,
        tolerance_ns,
    )
    frame_status, frame_issues, frame_data = evaluate_frames(ordered, resolved, base_by_id)

    status, overall_issues = _overall_status(
        base, clock_status, clock_issues, frame_status, drift_status
    )
    unavailable_reason = _correction_unavailable_reason(
        base, clock_status, frame_status, drift_status
    )
    correction_available = unavailable_reason is None
    details = tuple(
        _detail_for(
            base_by_id[window.stream_id],
            resolved[window.stream_id],
            relative_drift.get(window.stream_id),
            frame_data.get(window.stream_id),
            drift_issues.get(window.stream_id, ()),
            frame_issues.get(window.stream_id, ()),
            correction_available,
        )
        for window in sorted(ordered, key=lambda item: item.stream_id)
    )
    return StreamSyncAnalysis(
        status=status,
        clock_domain_status=clock_status,
        frame_index_status=frame_status,
        drift_status=drift_status,
        tolerance_ns=tolerance_ns,
        reference_stream_id=base.reference_stream_id,
        correction_available=correction_available,
        correction_unavailable_reason=unavailable_reason,
        base=base,
        streams=details,
        issues=overall_issues,
    )


def _resolve_profiles(
    windows: Sequence[StreamWindow],
    profiles: Mapping[str, StreamSyncProfile] | None,
) -> dict[str, StreamSyncProfile]:
    stream_ids = {item.stream_id for item in windows}
    provided = dict(profiles or {})
    unknown = sorted(set(provided) - stream_ids)
    if unknown:
        raise SyncDriftError(f"profile 指向不存在的流：{'、'.join(unknown)}")
    resolved: dict[str, StreamSyncProfile] = {}
    for stream_id in stream_ids:
        profile = provided.get(stream_id)
        if profile is None:
            profile = StreamSyncProfile()
        elif not isinstance(profile, StreamSyncProfile):
            raise SyncDriftError("profile 必须是 StreamSyncProfile")
        resolved[stream_id] = profile
    return resolved


def _declared(value: str | None) -> bool:
    return value is not None and value != "UNKNOWN"


def _evaluate_clock_domain(
    windows: Sequence[StreamWindow],
    profiles: Mapping[str, StreamSyncProfile],
) -> tuple[ClockDomainStatus, tuple[str, ...]]:
    if not windows:
        return ClockDomainStatus.UNKNOWN, ()

    fully_declared: list[tuple[str, str, str | None]] = []
    partially_declared: list[str] = []
    for item in windows:
        profile = profiles[item.stream_id]
        domain = profile.time_domain if _declared(profile.time_domain) else None
        source = profile.clock_source if _declared(profile.clock_source) else None
        if domain is None and source is None:
            if profile.clock_id is not None:
                partially_declared.append(item.stream_id)
            continue
        if domain is None or source is None:
            partially_declared.append(item.stream_id)
            continue
        fully_declared.append((domain, source, profile.clock_id))

    if not fully_declared and not partially_declared:
        # 所有流都没有声明时钟：可比性未知，但保留"未声明"与"不一致"的区别。
        return ClockDomainStatus.UNKNOWN, ()
    if partially_declared:
        return ClockDomainStatus.UNKNOWN, ("CLOCK_PARTIAL",)
    if len(fully_declared) < len(windows):
        # 已声明与未声明混用：无法证明它们在同一时钟域。
        return ClockDomainStatus.UNKNOWN, ("CLOCK_PARTIAL",)

    identities = {(domain, source) for domain, source, _ in fully_declared}
    if len(identities) > 1:
        return ClockDomainStatus.MIXED, ("CLOCK_DOMAIN_MIXED",)

    clock_ids = {clock_id for _, _, clock_id in fully_declared}
    if None in clock_ids and len(clock_ids) > 1:
        return ClockDomainStatus.UNKNOWN, ("CLOCK_ID_PARTIAL",)
    if len(clock_ids) > 1:
        return ClockDomainStatus.MIXED, ("CLOCK_DOMAIN_MIXED",)
    return ClockDomainStatus.CONSISTENT, ()


def _evaluate_drift(
    windows: Sequence[StreamWindow],
    profiles: Mapping[str, StreamSyncProfile],
    base_by_id: Mapping[str, StreamDrift],
    reference_stream_id: str | None,
    clock_status: ClockDomainStatus,
    tolerance_ns: int,
) -> tuple[DriftStatus, dict[str, tuple[str, ...]], dict[str, int | None]]:
    relative: dict[str, int | None] = {item.stream_id: None for item in windows}
    issues: dict[str, list[str]] = {item.stream_id: [] for item in windows}
    if not windows or reference_stream_id is None:
        return DriftStatus.UNKNOWN, {}, {}
    # 基准必须已知，否则"相对漂移"无从计算；部分声明同样不可判定。
    unknown = [item.stream_id for item in windows if profiles[item.stream_id].drift_ppm is None]
    if len(unknown) == len(windows):
        # 全部未声明：不是"漂移合格"，而是"没有可判定的漂移信息"。
        # 不写 per-stream issue，避免每条历史数据都出现无意义的噪声。
        return DriftStatus.UNKNOWN, {key: () for key in issues}, relative
    if clock_status is not ClockDomainStatus.CONSISTENT:
        # 时钟域不可比时，两个 ppm 的差没有可解释的物理含义；显式 UNKNOWN。
        for window in windows:
            if profiles[window.stream_id].drift_ppm is not None:
                issues[window.stream_id].append("DRIFT_UNKNOWN")
        return DriftStatus.UNKNOWN, {key: tuple(value) for key, value in issues.items()}, relative
    if unknown:
        for stream_id in unknown:
            issues[stream_id].append("DRIFT_UNKNOWN")
        return DriftStatus.UNKNOWN, {key: tuple(value) for key, value in issues.items()}, relative

    # 基准复用 ``detect_sync_drift`` 的权威选择（起点最早，并列按 stream_id），
    # 不在这里再写一遍同样的规则。
    reference_ppm = Fraction(str(profiles[reference_stream_id].drift_ppm))
    durations = {item.stream_id: item.duration_ns for item in windows}
    unstable = False
    exceeded = False
    for item in windows:
        stream_id = item.stream_id
        stream_ppm = Fraction(str(profiles[stream_id].drift_ppm))
        # 只在实际重叠的时间尺度上比较漂移，避免用更长窗口夸大结论。
        common_ns = min(durations[reference_stream_id], durations[stream_id])
        delta_ns = _round_half_up(Fraction(common_ns) * (stream_ppm - reference_ppm) / _PPM_SCALE)
        relative[stream_id] = delta_ns
        end_offset_ns = base_by_id[stream_id].start_offset_ns + delta_ns
        if abs(delta_ns) > tolerance_ns:
            exceeded = True
            issues[stream_id].append("DRIFT_EXCEEDED")
        if end_offset_ns < 0:
            # 相对偏移在窗口内反向：恒定裁剪/补齐无法同时对齐两端。
            unstable = True
            issues[stream_id].append("NEGATIVE_DRIFT")
    if unstable:
        status = DriftStatus.UNSTABLE
    elif exceeded:
        status = DriftStatus.EXCEEDED
    else:
        status = DriftStatus.CONSISTENT
    return (
        status,
        {key: tuple(value) for key, value in issues.items()},
        relative,
    )


def _overall_status(
    base: SyncDriftReport,
    clock_status: ClockDomainStatus,
    clock_issues: tuple[str, ...],
    frame_status: FrameIndexStatus,
    drift_status: DriftStatus,
) -> tuple[SyncDriftStatus, tuple[str, ...]]:
    issues = list(base.issues)
    if base.status is SyncDriftStatus.UNKNOWN:
        issues.extend(clock_issues)
        return SyncDriftStatus.UNKNOWN, tuple(dict.fromkeys(issues))
    if base.status is SyncDriftStatus.SINGLE_STREAM:
        issues.extend(clock_issues)
        if frame_status is FrameIndexStatus.OUT_OF_RANGE:
            # 单流没有"同步"概念，但声明帧数与时长×帧率矛盾仍是不可判定事实。
            issues.append("FRAME_INDEX_OUT_OF_RANGE")
            return SyncDriftStatus.UNKNOWN, tuple(dict.fromkeys(issues))
        if frame_status is FrameIndexStatus.UNKNOWN:
            issues.append("FRAME_RATE_UNKNOWN")
        return SyncDriftStatus.SINGLE_STREAM, tuple(dict.fromkeys(issues))
    if clock_status is ClockDomainStatus.MIXED:
        issues.extend(clock_issues)
        return SyncDriftStatus.UNKNOWN, tuple(dict.fromkeys(issues))
    if frame_status is FrameIndexStatus.OUT_OF_RANGE:
        issues.append("FRAME_INDEX_OUT_OF_RANGE")
        return SyncDriftStatus.UNKNOWN, tuple(dict.fromkeys(issues))
    if frame_status is FrameIndexStatus.UNKNOWN:
        issues.append("FRAME_RATE_UNKNOWN")
    elif frame_status is FrameIndexStatus.INCONSISTENT:
        issues.append("FRAME_RATE_INCONSISTENT")
    if drift_status is DriftStatus.UNSTABLE:
        issues.append("NEGATIVE_DRIFT")
        return SyncDriftStatus.UNKNOWN, tuple(dict.fromkeys(issues))
    issues.extend(clock_issues)
    if base.status is SyncDriftStatus.EXCEEDED or drift_status is DriftStatus.EXCEEDED:
        if drift_status is DriftStatus.EXCEEDED:
            issues.append("DRIFT_EXCEEDED")
        return SyncDriftStatus.EXCEEDED, tuple(dict.fromkeys(issues))
    return SyncDriftStatus.ALIGNED, tuple(dict.fromkeys(issues))


def _correction_unavailable_reason(
    base: SyncDriftReport,
    clock_status: ClockDomainStatus,
    frame_status: FrameIndexStatus,
    drift_status: DriftStatus,
) -> str | None:
    if base.status is SyncDriftStatus.UNKNOWN:
        return "NO_STREAMS"
    if frame_status is FrameIndexStatus.OUT_OF_RANGE:
        return "FRAME_INDEX_OUT_OF_RANGE"
    if base.status is SyncDriftStatus.SINGLE_STREAM:
        return "ONLY_ONE_STREAM"
    if clock_status is ClockDomainStatus.MIXED:
        return "CLOCK_DOMAIN_MIXED"
    if drift_status is DriftStatus.UNSTABLE:
        return "NEGATIVE_DRIFT"
    if drift_status is DriftStatus.EXCEEDED:
        # 起点裁剪修不了持续漂移；需要重采样，超出本模块建议范围。
        return "DRIFT_REQUIRES_RESAMPLING"
    return None


def _detail_for(
    base: StreamDrift,
    profile: StreamSyncProfile,
    relative_drift_ns: int | None,
    frame: FrameFacts | None,
    drift_issues: tuple[str, ...],
    frame_issues: tuple[str, ...],
    correction_available: bool,
) -> StreamSyncDetail:
    correction: CorrectionProposal | None = None
    if correction_available:
        offset_frames = frame.start_offset_frames if frame is not None else None
        correction = CorrectionProposal(
            trim_start_ns=base.start_offset_ns,
            tail_trim_ns=max(0, base.duration_delta_ns),
            tail_pad_ns=max(0, -base.duration_delta_ns),
            trim_start_frames=(math.floor(offset_frames) if offset_frames is not None else None),
            frame_aligned=(offset_frames is not None and offset_frames.denominator == 1),
        )
    return StreamSyncDetail(
        base=base,
        clock_source=profile.clock_source,
        time_domain=profile.time_domain,
        clock_id=profile.clock_id,
        drift_ppm=profile.drift_ppm,
        relative_drift_ns=relative_drift_ns,
        frame_rate=frame.rate if frame is not None else None,
        frame_count_derived=frame.derived_count if frame is not None else None,
        frame_count_declared=frame.declared_count if frame is not None else None,
        start_offset_frames=frame.start_offset_frames if frame is not None else None,
        correction=correction,
        issues=tuple(dict.fromkeys((*drift_issues, *frame_issues))),
    )


def _round_half_up(value: Fraction) -> int:
    """确定性四舍五入（避免浮点与 Python 银行家舍入带来的不可复现）。"""
    return math.floor(value + Fraction(1, 2))


__all__ = [
    "ClockDomainStatus",
    "CorrectionProposal",
    "DriftStatus",
    "FrameIndexStatus",
    "FrameRate",
    "StreamSyncAnalysis",
    "StreamSyncDetail",
    "StreamSyncProfile",
    "analyze_stream_sync",
]
