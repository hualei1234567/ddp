"""LeRobot 逐帧导出器的**视频 feature** 支持测试（V6.1 / V61-008）。

覆盖：
- 传入 video feature 时 ``features`` 出现该条目，dtype=video、names 三元组；
- ``ddp.video_provided`` 随实际内容**真实反映**（此前硬编码 False）；
- 不传 video feature 时保持旧行为（``video_provided=False``、features 不变）——
  **向后兼容**：既有 Loader 验证与 parquet adapter 测试依赖它；
- 非 video dtype 或 names 不符时**显式失败**（官方 Loader 依赖 names 做轴序解释，
  错了会静默错位）。
"""

from __future__ import annotations

import json

import pytest
from ddp_data.lerobot.frame_writer import (
    FrameFeature,
    build_frame_features,
    write_frame_episode,
)

_VIDEO = FrameFeature(
    "observation.image",
    "video",
    (96, 96, 3),
    names=("height", "width", "channels"),
)


def _frames(count: int = 4) -> list[dict[str, object]]:
    return [
        {
            "observation": [float(i)] * 3,
            "action": [float(i)] * 2,
            "timestamp": i * 0.1,
        }
        for i in range(count)
    ]


def test_video_feature_is_declared() -> None:
    """传入 video feature → features 里出现该条目且 names 正确。"""
    features = build_frame_features(
        observation_dim=3,
        action_dim=2,
        video_features=(_VIDEO,),
    )
    names = [f.name for f in features]
    assert "observation.image" in names
    video = next(f for f in features if f.name == "observation.image")
    assert video.dtype == "video"
    assert video.shape == (96, 96, 3)
    assert video.names == ("height", "width", "channels")


def test_video_provided_reflects_reality() -> None:
    """``ddp.video_provided`` 必须反映实际是否含视频（此前硬编码 False）。"""
    result = write_frame_episode(
        frames=_frames(),
        episode_index=0,
        fps=10.0,
        observation_dim=3,
        action_dim=2,
        video_features=(_VIDEO,),
    )
    info = json.loads(result.info_json.decode("utf-8"))
    assert info["ddp"]["video_provided"] is True
    assert "observation.image" in info["features"]
    assert info["features"]["observation.image"]["dtype"] == "video"
    assert info["features"]["observation.image"]["names"] == [
        "height",
        "width",
        "channels",
    ]


def test_without_video_feature_keeps_legacy_behavior() -> None:
    """不传 video feature → 旧行为不变（向后兼容）。"""
    result = write_frame_episode(
        frames=_frames(),
        episode_index=0,
        fps=10.0,
        observation_dim=3,
        action_dim=2,
    )
    info = json.loads(result.info_json.decode("utf-8"))
    assert info["ddp"]["video_provided"] is False
    assert "observation.image" not in info["features"]
    assert "observation.state" in info["features"]
    assert "action" in info["features"]


def test_invalid_video_feature_is_rejected() -> None:
    """dtype 不是 video 或 names 不符 → 显式失败。"""
    with pytest.raises(ValueError, match="dtype 必须是 video"):
        build_frame_features(
            observation_dim=3,
            action_dim=2,
            video_features=(FrameFeature("observation.image", "float32", (3,)),),
        )
    with pytest.raises(ValueError, match="names 必须是"):
        build_frame_features(
            observation_dim=3,
            action_dim=2,
            video_features=(
                FrameFeature("observation.image", "video", (96, 96, 3), names=("h", "w", "c")),
            ),
        )
