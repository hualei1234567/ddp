"""Canonical Schema v1 的 LeRobot 只读投影（DDP-V21-N3-001）。

验证重点：投影不重写解析器、原始字段保留、没有来源的内容全部显式登记为缺失。
"""

from __future__ import annotations

import json

from ddp_data.adapters import parse_human1m_preview
from ddp_data.canonical import (
    Episode,
    VideoStream,
    project_episode,
    projection_to_dict,
)


def _human1m_bytes() -> bytes:
    rows = [
        {
            "example_id": "demo-1",
            "file_name": "videos/main.mp4",
            "view": "Egocentric main",
            "modality": "video",
            "task_name": "pick",
            "instruction": "pick the cube",
            "episode_seconds": 12.0,
            "source_fps": 30.0,
            "has_wrist": False,
            "excerpt_start_seconds": 1.0,
            "excerpt_seconds": 5.0,
        }
    ]
    return "\n".join(json.dumps(row) for row in rows).encode("utf-8")


def test_projection_from_real_human1m_metadata_is_explicit_about_gaps() -> None:
    legacy = parse_human1m_preview(_human1m_bytes())[0]
    projection = project_episode(legacy)

    episode = projection.episode
    assert episode.episode_key == "demo-1"
    assert episode.task_name == "pick"
    assert episode.instruction == "pick the cube"
    assert episode.environment is None
    assert episode.episode_seconds == 12.0
    assert episode.source_fps == 30.0
    assert episode.has_wrist is False
    assert episode.raw == {
        "example_id": "demo-1",
        "file_name": "videos/main.mp4",
        "view": "Egocentric main",
        "modality": "video",
        "task_name": "pick",
        "instruction": "pick the cube",
        "episode_seconds": 12.0,
        "source_fps": 30.0,
        "has_wrist": False,
        "excerpt_start_seconds": 1.0,
        "excerpt_seconds": 5.0,
    }
    assert episode.video_stream_ids == ("demo-1:video:0",)
    # preview 元数据没有的内容必须显式登记，而不是伪造 0/空数组
    for gap in (
        "action_ids",
        "observation_ids",
        "sensor_stream_ids",
        "frame_count",
        "episode_id",
        "project_id",
        "start_time_ns",
        "end_time_ns",
        "environment",
        "setting",
    ):
        assert gap in episode.missing_fields

    assert len(projection.video_streams) == 1
    stream = projection.video_streams[0]
    assert stream.stream_id == "demo-1:video:0"
    assert stream.camera_name == "main"
    assert stream.camera_name_raw == "Egocentric main"
    assert stream.storage_reference == "videos/main.mp4"
    assert stream.excerpt_start_ns == 1_000_000_000
    assert stream.duration_ns == 5_000_000_000
    assert stream.fps == 30.0
    for gap in ("media_format", "width", "height", "frame_count", "encoding", "checksum"):
        assert gap in stream.missing_fields


def test_projection_roundtrips_and_is_json_serializable() -> None:
    projection = project_episode(parse_human1m_preview(_human1m_bytes())[0])
    payload = projection_to_dict(projection)
    json.dumps(payload, allow_nan=False)

    restored_episode = Episode.from_dict(payload["episode"])
    assert restored_episode == projection.episode
    restored_streams = tuple(VideoStream.from_dict(item) for item in payload["video_streams"])
    assert restored_streams == projection.video_streams


def test_projection_does_not_invent_frames_actions_or_observations() -> None:
    projection = project_episode(parse_human1m_preview(_human1m_bytes())[0])
    assert projection.episode.action_ids is None
    assert projection.episode.observation_ids is None
    assert projection.episode.sensor_stream_ids is None
    assert projection.episode.frame_count is None
