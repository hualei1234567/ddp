"""Canonical 图像 Observation 投影测试（V61-006）。

钉住三条硬规则：

1. ``payload_ref`` 是**定位符**而非新写入对象（原始视频零改动）；
2. 缺失字段显式进 ``missing_fields``，不用 0/空串冒充；
3. 原始时间与归一化时间分开，不互相顶替。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ddp_data.canonical import (
    FRAME_LOCATOR_SEPARATOR,
    OBSERVATION_TYPE_IMAGE,
    FrameLocator,
    project_frame_observation,
    project_stream_observations,
)

SOURCE_KEY = "raw/0190-abc/2026/11/29/content.mp4"


def _frame(**overrides: object) -> FrameLocator:
    values: dict[str, object] = {
        "asset_id": "asset-1",
        "stream_id": "front",
        "frame_index": 7,
        "source_object_key": SOURCE_KEY,
        "pts_seconds": Decimal("0.700"),
        "width": 96,
        "height": 96,
        "pixel_format": "yuv420p",
        "codec": "av1",
        "parser_version": "ddp.video_decode/1.0.0",
    }
    values.update(overrides)
    return FrameLocator(**values)  # type: ignore[arg-type]


def test_payload_ref_locates_frame_in_source_video() -> None:
    """未物化像素时，payload_ref 指向源视频 + 帧号（定位符，不是新对象）。"""
    frame = _frame()
    observation = project_frame_observation(
        frame, episode_key="ep-1", observation_id="ep-1:front:7"
    )

    assert observation.payload_ref == f"{SOURCE_KEY}{FRAME_LOCATOR_SEPARATOR}7"
    assert observation.payload_ref == "raw/0190-abc/2026/11/29/content.mp4#frame=7"


def test_materialized_frame_object_takes_precedence() -> None:
    """若帧已被物化成独立对象，payload_ref 必须用该对象而不是源视频定位。"""
    frame = _frame(frame_object_ref="processing/frames/asset-1/front/7.png")

    observation = project_frame_observation(
        frame, episode_key="ep-1", observation_id="ep-1:front:7"
    )

    assert observation.payload_ref == "processing/frames/asset-1/front/7.png"


def test_shape_and_encoding_come_from_decoder_facts() -> None:
    observation = project_frame_observation(
        _frame(), episode_key="ep-1", observation_id="ep-1:front:7"
    )

    assert observation.observation_type == OBSERVATION_TYPE_IMAGE
    assert observation.shape == (96, 96, 3)
    assert observation.encoding == "yuv420p"
    assert observation.frame_index == 7
    assert observation.stream_id == "front"
    assert observation.dtype == "uint8"


def test_missing_dimensions_are_declared_not_faked() -> None:
    """宽高未知时 shape 为 None 且进入 missing_fields——不写成 (0,0,3)。"""
    observation = project_frame_observation(
        _frame(width=None, height=None),
        episode_key="ep-1",
        observation_id="ep-1:front:7",
    )

    assert observation.shape is None
    assert "shape" in observation.missing_fields


def test_raw_and_normalized_time_are_kept_separate() -> None:
    """归一化存在时 timestamp_ns 用它，但原始时间仍保留在 timestamp_raw_ns。"""
    frame = _frame(
        pts_seconds=Decimal("0.700"),
        timestamp_normalized_seconds=Decimal("0.701"),
    )

    observation = project_frame_observation(
        frame, episode_key="ep-1", observation_id="ep-1:front:7"
    )

    assert observation.timestamp_raw_ns == 700_000_000
    assert observation.timestamp_normalized_ns == 701_000_000
    assert observation.timestamp_ns == 701_000_000
    assert observation.source_time_ns == 700_000_000


def test_without_normalized_time_uses_raw() -> None:
    observation = project_frame_observation(
        _frame(), episode_key="ep-1", observation_id="ep-1:front:7"
    )

    assert observation.timestamp_normalized_ns is None
    assert observation.timestamp_ns == 700_000_000
    assert observation.valid is True


def test_decode_error_frame_is_invalid_with_reason() -> None:
    observation = project_frame_observation(
        _frame(decode_status="DECODE_ERROR"),
        episode_key="ep-1",
        observation_id="ep-1:front:7",
    )

    assert observation.valid is False
    assert observation.invalid_reason == "decode_status=DECODE_ERROR"


def test_frame_without_any_timestamp_is_rejected_explicitly() -> None:
    """两类时间都缺 → 显式拒绝（契约禁止无时间戳的 Observation），不伪造观测。"""
    with pytest.raises(ValueError, match="缺少任何可用时间戳"):
        project_frame_observation(
            _frame(pts_seconds=None),
            episode_key="ep-1",
            observation_id="ep-1:front:7",
        )


def test_nanosecond_conversion_is_exact_for_repeating_decimal() -> None:
    """0.1 秒这类十进制在 float 里不精确；Decimal 路径必须给出精确 100000000。"""
    observation = project_frame_observation(
        _frame(pts_seconds=Decimal("0.1")),
        episode_key="ep-1",
        observation_id="ep-1:front:7",
    )

    assert observation.timestamp_ns == 100_000_000


def test_stream_projection_is_sorted_and_deterministic() -> None:
    frames = [_frame(frame_index=index) for index in (5, 1, 3)]
    first = project_stream_observations(frames, episode_key="ep-1")
    second = project_stream_observations(list(reversed(frames)), episode_key="ep-1")

    assert [item.frame_index for item in first.observations] == [1, 3, 5]
    assert [item.to_dict() for item in first.observations] == [
        item.to_dict() for item in second.observations
    ]
    assert [item.observation_id for item in first.observations] == [
        "ep-1:front:1",
        "ep-1:front:3",
        "ep-1:front:5",
    ]
    assert first.skipped_frames == ()


def test_stream_projection_reports_skipped_frames_instead_of_faking() -> None:
    """缺时间戳的帧进入 skipped_frames，数量可核对，不静默丢失。"""
    frames = [
        _frame(frame_index=0),
        _frame(frame_index=1, pts_seconds=None),
        _frame(frame_index=2, pts_seconds=Decimal("0.2")),
    ]

    projection = project_stream_observations(frames, episode_key="ep-1")

    assert [item.frame_index for item in projection.observations] == [0, 2]
    assert projection.skipped_frames == (("front", 1),)
    assert projection.skipped_count == 1
    assert projection.to_payload()["observation_count"] == 2


def test_round_trip_through_dict() -> None:
    from ddp_data.canonical import Observation

    observation = project_frame_observation(
        _frame(), episode_key="ep-1", observation_id="ep-1:front:7"
    )
    restored = Observation.from_dict(observation.to_dict())

    assert restored == observation


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"frame_index": -1}, "frame_index"),
        ({"source_object_key": ""}, "source_object_key"),
        ({"decode_status": "BROKEN"}, "decode_status"),
    ],
)
def test_invalid_locator_fails_explicitly(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _frame(**overrides)
