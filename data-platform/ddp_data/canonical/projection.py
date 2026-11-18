"""把现有 LeRobot ``CanonicalEpisode`` 只读投影为 Canonical Schema v1。

**不重写解析器**：``lerobot/parser.py`` 仍是唯一映射逻辑；本模块只做一次
方向明确的转换，把 Episode 及其媒体资产投影成 v1 的 ``Episode`` 与
``VideoStream``。没有 Frame/Action/Observation 的来源数据，就不产出它们，
也不伪造空帧或零动作。

ID 规则（ADR-0035）：v1 实体 ID 是**投影内稳定**的合成标识
``<episode_key>:video:<序号>``；持久化 ID 由服务端后续分配，不在只读层生成。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ddp_data.canonical.v1 import (
    EPISODE_FIELDS,
    VIDEO_STREAM_FIELDS,
    Episode,
    VideoStream,
    declared_missing_fields,
)
from ddp_data.lerobot.parser import CanonicalEpisode as LegacyCanonicalEpisode
from ddp_data.lerobot.parser import CanonicalMediaAsset

_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class EpisodeProjection:
    """一次投影的完整产物：Episode 与其视频流。

    只包含来源真实具备的内容；``sensor_streams`` / ``actions`` / ``observations``
    等留待后续 Adapter 提供真实数据后再扩展，不在此伪造。
    """

    episode: Episode
    video_streams: tuple[VideoStream, ...]


def project_episode(episode: LegacyCanonicalEpisode) -> EpisodeProjection:
    """把现有 Episode 映射为 v1 Episode + VideoStream 序列。"""
    streams = tuple(
        _project_stream(episode.episode_key, index, asset)
        for index, asset in enumerate(episode.media_assets)
    )
    values: dict[str, object] = {
        "episode_key": episode.episode_key,
        "task_name": episode.task_name,
        "instruction": episode.instruction,
        "video_stream_ids": tuple(stream.stream_id for stream in streams),
        "environment": episode.environment,
        "setting": episode.setting,
        "episode_seconds": episode.episode_seconds,
        "source_fps": episode.source_fps,
        "has_wrist": episode.has_wrist,
        "raw": dict(episode.raw) if episode.raw else None,
        "sensor_stream_ids": None,
        "action_ids": None,
        "observation_ids": None,
        "frame_count": None,
        # 以下 ID/时间由服务端或后续处理阶段产生；投影阶段如实留空。
        "episode_id": None,
        "project_id": None,
        "start_time_ns": None,
        "end_time_ns": None,
    }
    return EpisodeProjection(
        episode=Episode(
            **values,
            missing_fields=declared_missing_fields(EPISODE_FIELDS, values),
        ),
        video_streams=streams,
    )


def _project_stream(episode_key: str, index: int, asset: CanonicalMediaAsset) -> VideoStream:
    values: dict[str, object] = {
        "stream_id": f"{episode_key}:video:{index}",
        "camera_name": asset.camera or None,
        "camera_name_raw": asset.view_raw or None,
        # 解析器的 "unknown" 是占位而非真实模态；按缺失处理，避免伪装有数据。
        "modality": None if asset.modality in ("", "unknown") else asset.modality,
        "media_format": None,
        "storage_reference": asset.path or None,
        "excerpt_start_ns": _seconds_to_ns(asset.excerpt_start_seconds),
        "duration_ns": _seconds_to_ns(asset.excerpt_seconds),
        "fps": asset.source_fps,
        "frame_count": None,
        "width": None,
        "height": None,
        "size_bytes": asset.size_bytes_hint,
        "checksum": asset.sha256,
        "encoding": None,
        "dropped_count": None,
    }
    return VideoStream(
        **values,
        missing_fields=declared_missing_fields(VIDEO_STREAM_FIELDS, values),
    )


def _seconds_to_ns(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value * _NS_PER_SECOND))


def projection_to_dict(projection: EpisodeProjection) -> Mapping[str, object]:
    """便于落盘/比对：Episode 与视频流的 dict 形式，不改变字段语义。"""
    return {
        "episode": projection.episode.to_dict(),
        "video_streams": [stream.to_dict() for stream in projection.video_streams],
    }


__all__ = ["EpisodeProjection", "project_episode", "projection_to_dict"]
