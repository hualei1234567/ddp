"""视频帧索引 → Canonical 图像 Observation 的只读投影（V61-006）。

## 这个模块解决什么

V6.1 视频链在上游已经落地「解码 → 逐帧索引（``video_frames`` 表）」，
但 Canonical Schema v1 的 ``Observation`` 里声明了 ``payload_ref`` /
``frame_index`` / ``shape`` / ``checksum`` 等像素定位字段，**全仓没有任何
写入点**——下游（数据集导出、审看、训练消费）无法从 Canonical 侧回答
「这一帧的像素在哪」。

本模块是**唯一**的投影实现：把一帧的解码事实映射成 Canonical ``Observation``。

## 三条硬规则（对齐 ADR-0035 / Canonical v1 契约）

1. **原始视频永不可变**：``payload_ref`` 是**定位符**，不是新写入的对象。
   当前形态为 ``<source_object_key>#frame=<index>``——它明确表达「像素需要
   从源视频按帧号取」，而不是假装我们已经抽出了像素对象。将来若帧被物化
   成独立对象（``frame_object_ref`` 非空），则优先使用该引用。
2. **缺失必须显式**：拿不到的字段（如未做流级探测时的宽高、编码）一律
   留 ``None`` 并进入 ``missing_fields``，禁止用 0 / 空串冒充。
3. **原始时间与归一化时间分开**：``timestamp_raw_ns`` 来自解码器的原始
   pts，``timestamp_normalized_ns`` 来自归一化阶段；两者**永不互相顶替**。
   两者都缺时按契约要求置 ``valid=False`` 并写明 ``invalid_reason``。

## 边界

- 本模块在 ``data-platform`` 层（纯数据能力），**不依赖 ORM / Session**：
  输入是普通的行事实（:class:`FrameLocator`），调用方（应用层/脚本）负责
  从 ``video_frames`` 读出这些值。
- 不做 IO、不写库、不读对象存储。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ddp_data.canonical.v1 import (
    OBSERVATION_FIELDS,
    Observation,
    declared_missing_fields,
)

#: 观测类型：图像帧观测。命名与数据集导出侧 ``observation.image`` 同源。
OBSERVATION_TYPE_IMAGE = "image"

#: payload 定位符分隔符。用 ``#frame=`` 是因为它不会被误读为对象 key 的一部分
#: （对象 key 不允许 ``#``），且人可读。
FRAME_LOCATOR_SEPARATOR = "#frame="

#: 默认编码：解码器给出像素格式时以其为准，否则如实登记 ARRAY（未压缩数组）。
DEFAULT_ENCODING = "ARRAY"

_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class FrameLocator:
    """一帧的解码事实（与 ``video_frames`` 行同形，但不依赖 ORM）。

    ``pts_seconds`` / ``timestamp_normalized_seconds`` 用 ``Decimal`` 承载：
    纳秒换算不能经过 float，否则末位会漂移（MEM-INC-008 同族问题）。
    """

    asset_id: str
    stream_id: str
    frame_index: int
    source_object_key: str
    pts_seconds: Decimal | None = None
    timestamp_normalized_seconds: Decimal | None = None
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    codec: str | None = None
    decode_status: str = "OK"
    frame_object_ref: str | None = None
    frame_hash: str | None = None
    parser_version: str | None = None

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index 必须非负")
        if not self.source_object_key:
            raise ValueError("source_object_key 不能为空")
        if self.decode_status not in {"OK", "DECODE_ERROR", "SKIPPED"}:
            raise ValueError(f"decode_status 非法：{self.decode_status!r}")

    @property
    def payload_ref(self) -> str:
        """像素定位符：已物化帧优先生成独立对象引用，否则回到源视频帧号。"""
        if self.frame_object_ref:
            return self.frame_object_ref
        return f"{self.source_object_key}{FRAME_LOCATOR_SEPARATOR}{self.frame_index}"


def _seconds_to_ns(value: Decimal | None) -> int | None:
    """秒 → 纳秒，全程 ``Decimal``；``None`` 保持 ``None``（不补 0）。"""
    if value is None:
        return None
    return int((Decimal(value) * _NS_PER_SECOND).to_integral_value())


def project_frame_observation(
    frame: FrameLocator,
    *,
    episode_key: str,
    observation_id: str,
    sensor_id: str | None = None,
) -> Observation:
    """把一帧投影成 Canonical ``Observation``（纯函数）。

    ``observation_id`` 由调用方给出并保证在投影内稳定（通常
    ``<episode_key>:<stream_id>:<frame_index>``）；本模块不生成全局唯一 ID，
    ——那属于服务端分配职责。
    """
    timestamp_raw_ns = _seconds_to_ns(frame.pts_seconds)
    timestamp_normalized_ns = _seconds_to_ns(frame.timestamp_normalized_seconds)
    # 双时间缺一不可：契约要求至少一个可解释的时间戳。归一化缺失不是错误
    # （尚未跑归一化阶段），但两者都缺就不能声称这是一帧"可定位"的观测。
    timestamp_ns = timestamp_normalized_ns if timestamp_normalized_ns is not None else timestamp_raw_ns

    channel_count = 3  # RGB/ BGR 均为三通道；灰度与深度由 pixel_format 区分
    shape: tuple[int, ...] | None = None
    if frame.width is not None and frame.height is not None:
        shape = (frame.height, frame.width, channel_count)

    if timestamp_ns is None:
        # Canonical v1 契约硬性要求「至少一个可解释时间戳」。没有时间的帧
        # **无法**表达为 Observation——它们在 video_frames 里照常留痕，
        # 但不是 Canonical 观测。这里显式拒绝，不构造一个无时间的伪造观测。
        raise ValueError(
            f"帧 {frame.stream_id}/{frame.frame_index} 缺少任何可用时间戳，"
            "无法投影为 Canonical Observation（帧本身仍保留在 video_frames）"
        )

    decode_ok = frame.decode_status == "OK"
    if not decode_ok:
        valid: bool | None = False
        invalid_reason = f"decode_status={frame.decode_status}"
    else:
        valid = True
        invalid_reason = None

    values: dict[str, object] = {
        "observation_id": observation_id,
        "episode_key": episode_key,
        "observation_type": OBSERVATION_TYPE_IMAGE,
        "stream_id": frame.stream_id,
        "sensor_id": sensor_id,
        "frame_index": frame.frame_index,
        "timestamp_ns": timestamp_ns,
        "source_time_ns": timestamp_raw_ns,
        "shape": shape,
        "dtype": "uint8",
        "encoding": frame.pixel_format or DEFAULT_ENCODING,
        "payload_ref": frame.payload_ref,
        "checksum": frame.frame_hash,
        "valid": valid,
        "invalid_reason": invalid_reason,
        "timestamp_raw_ns": timestamp_raw_ns,
        "timestamp_normalized_ns": timestamp_normalized_ns,
        "parser_version": frame.parser_version,
        "transform_provenance": (
            f"source={frame.source_object_key};codec={frame.codec}"
            if frame.codec
            else f"source={frame.source_object_key}"
        ),
    }
    # 契约要求：值为空的字段必须同时出现在 missing_fields 里，不能靠默认值冒充。
    values["missing_fields"] = declared_missing_fields(OBSERVATION_FIELDS, values)
    return Observation(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class StreamObservationProjection:
    """批量投影结果：可投影的观测 + 因缺时间戳被跳过的帧号。

    ``skipped_frames`` 让调用方能把「多少帧没进 Canonical」写成可见数字，
    而不是投影完发现数量变少了却不知道为什么。
    """

    observations: tuple[Observation, ...]
    skipped_frames: tuple[tuple[str, int], ...]

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_frames)

    def to_payload(self) -> dict[str, object]:
        return {
            "observation_count": len(self.observations),
            "skipped_frames": [{"stream_id": s, "frame_index": i} for s, i in self.skipped_frames],
        }


def project_stream_observations(
    frames: Sequence[FrameLocator],
    *,
    episode_key: str,
    sensor_id: str | None = None,
) -> StreamObservationProjection:
    """把一个流的多帧批量投影；按 ``(stream_id, frame_index)`` 稳定排序。

    缺时间戳的帧**不伪造**：它们进入 ``skipped_frames`` 由调用方如实上报。
    """
    ordered = sorted(frames, key=lambda item: (item.stream_id, item.frame_index))
    observations: list[Observation] = []
    skipped: list[tuple[str, int]] = []
    for frame in ordered:
        try:
            observations.append(
                project_frame_observation(
                    frame,
                    episode_key=episode_key,
                    observation_id=f"{episode_key}:{frame.stream_id}:{frame.frame_index}",
                    sensor_id=sensor_id,
                )
            )
        except ValueError:
            skipped.append((frame.stream_id, frame.frame_index))
    return StreamObservationProjection(
        observations=tuple(observations),
        skipped_frames=tuple(skipped),
    )


__all__ = [
    "DEFAULT_ENCODING",
    "FRAME_LOCATOR_SEPARATOR",
    "OBSERVATION_TYPE_IMAGE",
    "FrameLocator",
    "StreamObservationProjection",
    "project_frame_observation",
    "project_stream_observations",
]
