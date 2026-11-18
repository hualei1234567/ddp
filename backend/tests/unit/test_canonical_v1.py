"""Canonical Schema v1 只读契约（DDP-V21-N3-001）。

覆盖：

- 6 个实体的往返序列化与 JSON 安全（纳秒转十进制字符串）；
- 版本精确校验；
- ``missing_fields`` 的显式性（空值未登记、声明缺失却有值、未知/必填字段）；
- 非法输入拒绝；
- 跨字段约束；
- 与现有 Adapter 字段声明的一致性。

LeRobot 投影用例见 `test_canonical_v1_projection.py`。
"""

from __future__ import annotations

import json

import pytest
from ddp_data.adapters import CANONICAL_EPISODE_FIELDS, CanonicalField
from ddp_data.canonical import (
    ACTION_FIELDS,
    EPISODE_FIELDS,
    FRAME_FIELDS,
    LEGACY_EPISODE_FIELD_MAPPING,
    OBSERVATION_FIELDS,
    SCHEMA_VERSION,
    SENSOR_STREAM_FIELDS,
    VIDEO_STREAM_FIELDS,
    Action,
    CanonicalFieldError,
    CanonicalSchemaVersionError,
    Episode,
    FieldSpec,
    Frame,
    Observation,
    SensorStream,
    VideoStream,
    ensure_supported_version,
)

ENTITIES = (Frame, VideoStream, SensorStream, Action, Observation, Episode)


def _missing(specs: tuple[FieldSpec, ...], **present: object) -> tuple[str, ...]:
    """列出所有未提供值的可选字段，供"空实体"构造使用。"""
    return tuple(
        sorted(spec.name for spec in specs if not spec.required and spec.name not in present)
    )


def _frame(**overrides: object) -> Frame:
    values: dict[str, object] = {
        "frame_index": 0,
        "sequence": 0,
        "timestamp_ns": 1_700_000_000_000_000_000,
        "source_time_ns": 1_700_000_000_000_000_000,
        "source_time_domain": "UTC",
        "clock_id": "host-01",
        "time_base_ref": "ep1-time-base",
        "uncertainty_ns": 1_000_000,
        "observation_ids": ("obs-1",),
        "is_dropped": False,
        "drop_reason": None,
        "missing_fields": ("drop_reason",),
    }
    values.update(overrides)
    return Frame(**values)  # type: ignore[arg-type]


def _video_stream(**overrides: object) -> VideoStream:
    values: dict[str, object] = {
        "stream_id": "ep1:video:0",
        "camera_name": "main",
        "camera_name_raw": "Egocentric main",
        "modality": "video",
        "media_format": "mp4",
        "storage_reference": "s3://ddp/raw/videos/main.mp4",
        "excerpt_start_ns": 0,
        "duration_ns": 5_000_000_000,
        "fps": 30.0,
        "frame_count": 150,
        "width": 1280,
        "height": 720,
        "size_bytes": 1024,
        "checksum": "sha256:abc",
        "encoding": "h264",
        "dropped_count": 0,
        "missing_fields": (),
    }
    values.update(overrides)
    return VideoStream(**values)  # type: ignore[arg-type]


def _sensor_stream(**overrides: object) -> SensorStream:
    values: dict[str, object] = {
        "stream_id": "ep1:imu",
        "sensor_name": "imu0",
        "sensor_type": "IMU",
        "modality": "imu",
        "sample_rate_hz": 200.0,
        "channel_names": ("ax", "ay", "az"),
        "unit": "m/s^2",
        "quantity_kind": "linear_acceleration",
        "frame_id": "sensor-01/imu_link",
        "frame_convention": "FLU",
        "duration_ns": 5_000_000_000,
        "sample_count": 1000,
        "storage_reference": "s3://ddp/raw/imu.parquet",
        "checksum": "sha256:def",
        "calibration_id": "cal-1",
        "calibration_version": 1,
        "missing_fields": (),
    }
    values.update(overrides)
    return SensorStream(**values)  # type: ignore[arg-type]


def _action(**overrides: object) -> Action:
    values: dict[str, object] = {
        "action_id": "act-1",
        "episode_key": "ep1",
        "action_type": "JOINT_TARGET",
        "start_time_ns": 100,
        "end_time_ns": 200,
        "values": (0.1, 0.2),
        "unit": "rad",
        "frame_id": "joint/0",
        "joint_names": ("j0", "j1"),
        "source": "HUMAN_DEMO",
        "confidence": 0.9,
        "action_schema_ref": "ddp.action.joint_target/1.0.0",
        "frame_index": 0,
        "missing_fields": (),
    }
    values.update(overrides)
    return Action(**values)  # type: ignore[arg-type]


def _observation(**overrides: object) -> Observation:
    values: dict[str, object] = {
        "observation_id": "obs-1",
        "episode_key": "ep1",
        "observation_type": "IMU",
        "stream_id": "ep1:imu",
        "sensor_id": "sensor-1",
        "frame_index": 0,
        "timestamp_ns": 100,
        "source_time_ns": 100,
        "sequence": 0,
        "frame_id": "sensor-01/imu_link",
        "calibration_id": "cal-1",
        "calibration_version": 1,
        "unit": "m/s^2",
        "quantity_kind": "linear_acceleration",
        "shape": (3,),
        "dtype": "float32",
        "encoding": "ARROW",
        "payload_ref": "s3://ddp/canonical/obs-1.parquet",
        "checksum": "sha256:ghi",
        "valid": True,
        "invalid_reason": None,
        "quality_flags": ("OK",),
        "missing_fields": ("invalid_reason",),
    }
    values.update(overrides)
    return Observation(**values)  # type: ignore[arg-type]


def _episode(**overrides: object) -> Episode:
    values: dict[str, object] = {
        "episode_key": "ep1",
        "task_name": "pick",
        "instruction": "pick the cube",
        "video_stream_ids": ("ep1:video:0",),
        "environment": "lab",
        "setting": "table",
        "episode_seconds": 10.0,
        "source_fps": 30.0,
        "has_wrist": False,
        "raw": {"example_id": "ep1"},
        "sensor_stream_ids": ("ep1:imu",),
        "action_ids": ("act-1",),
        "observation_ids": ("obs-1",),
        "frame_count": 300,
        "episode_id": "018f1f7e-7c00-7a2b-9d10-2f6c1a9b3c4d",
        "project_id": "018e2a10-0000-7000-8000-000000000001",
        "start_time_ns": 0,
        "end_time_ns": 10_000_000_000,
        "missing_fields": (),
    }
    values.update(overrides)
    return Episode(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "entity",
    [
        _frame(),
        _video_stream(),
        _sensor_stream(),
        _action(),
        _observation(),
        _episode(),
    ],
    ids=[cls.__name__ for cls in ENTITIES],
)
def test_entity_roundtrips_through_json_safe_dict(entity: object) -> None:
    payload = entity.to_dict()  # type: ignore[attr-defined]
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "missing_fields" in payload
    json.dumps(payload, allow_nan=False)  # 必须可被标准 JSON 序列化
    restored = type(entity).from_dict(payload)
    assert restored == entity


def test_nanosecond_fields_are_decimal_strings_in_json() -> None:
    payload = _frame().to_dict()
    assert payload["timestamp_ns"] == "1700000000000000000"
    assert payload["source_time_ns"] == "1700000000000000000"
    # from_dict 同时接受契约字符串与内部整数
    assert Frame.from_dict(payload).timestamp_ns == 1_700_000_000_000_000_000


def test_every_entity_exposes_schema_version_and_field_list() -> None:
    for cls in ENTITIES:
        instance = {
            Frame: _frame,
            VideoStream: _video_stream,
            SensorStream: _sensor_stream,
            Action: _action,
            Observation: _observation,
            Episode: _episode,
        }[cls]()
        assert instance.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION == "ddp.canonical/1.0.0"


@pytest.mark.parametrize(
    "factory",
    [_frame, _video_stream, _sensor_stream, _action, _observation, _episode],
)
def test_wrong_schema_version_is_rejected(factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(CanonicalSchemaVersionError):
        factory(schema_version="ddp.canonical/1.0.0-draft")


def test_ensure_supported_version_rejects_unknown() -> None:
    ensure_supported_version(SCHEMA_VERSION)
    with pytest.raises(CanonicalSchemaVersionError):
        ensure_supported_version("ddp.canonical/2.0.0")


def test_empty_optional_field_must_be_declared_missing() -> None:
    with pytest.raises(CanonicalFieldError, match="未在 missing_fields 中声明"):
        VideoStream(stream_id="s1")


def test_fully_empty_entity_is_valid_when_all_gaps_are_declared() -> None:
    missing = _missing(VIDEO_STREAM_FIELDS)
    stream = VideoStream(stream_id="s1", missing_fields=missing)
    assert "camera_name" in stream.missing_fields
    assert "frame_count" in stream.missing_fields
    assert stream.to_dict()["missing_fields"] == list(missing)


def test_declaring_missing_field_with_value_is_rejected() -> None:
    with pytest.raises(CanonicalFieldError, match="已声明缺失，但存在值"):
        VideoStream(
            stream_id="s1",
            camera_name="main",
            missing_fields=_missing(VIDEO_STREAM_FIELDS),
        )


def test_unknown_missing_field_is_rejected() -> None:
    with pytest.raises(CanonicalFieldError, match="未定义字段"):
        VideoStream(stream_id="s1", missing_fields=("not_a_field",))


def test_required_field_cannot_be_declared_missing() -> None:
    with pytest.raises(CanonicalFieldError, match="必填字段不能声明为缺失"):
        VideoStream(stream_id="s1", missing_fields=("stream_id",))


def test_missing_fields_are_sorted_on_output() -> None:
    missing = tuple(reversed(_missing(SENSOR_STREAM_FIELDS)))
    stream = SensorStream(stream_id="s1", missing_fields=missing)
    assert list(stream.missing_fields) == sorted(stream.missing_fields)


def test_duplicate_missing_field_is_rejected() -> None:
    with pytest.raises(CanonicalFieldError, match="不允许重复"):
        SensorStream(stream_id="s1", missing_fields=("camera_name", "camera_name"))


def test_from_dict_rejects_unknown_field() -> None:
    payload = _video_stream().to_dict()
    payload["bogus"] = 1
    with pytest.raises(CanonicalFieldError, match="未知字段"):
        VideoStream.from_dict(payload)


def test_from_dict_requires_schema_version() -> None:
    payload = _video_stream().to_dict()
    payload.pop("schema_version")
    with pytest.raises(CanonicalSchemaVersionError, match="缺少 schema_version"):
        VideoStream.from_dict(payload)


def test_from_dict_rejects_unsupported_version() -> None:
    payload = _video_stream().to_dict()
    payload["schema_version"] = "ddp.canonical/0.9.0"
    with pytest.raises(CanonicalSchemaVersionError):
        VideoStream.from_dict(payload)


def test_from_dict_rejects_bad_types() -> None:
    payload = _frame().to_dict()
    payload["frame_index"] = "0"
    with pytest.raises(CanonicalFieldError, match="必须是整数"):
        Frame.from_dict(payload)

    payload = _frame().to_dict()
    payload["timestamp_ns"] = "1.5"
    with pytest.raises(CanonicalFieldError, match="十进制纳秒字符串"):
        Frame.from_dict(payload)


def test_frame_requires_at_least_one_timestamp() -> None:
    with pytest.raises(CanonicalFieldError, match="至少有 timestamp_ns"):
        _frame(
            timestamp_ns=None,
            source_time_ns=None,
            missing_fields=("drop_reason", "source_time_ns", "timestamp_ns"),
        )


def test_frame_dropped_flag_and_reason_must_agree() -> None:
    with pytest.raises(CanonicalFieldError, match="drop_reason"):
        _frame(is_dropped=True, drop_reason=None, missing_fields=("drop_reason",))
    with pytest.raises(CanonicalFieldError, match="不应出现 drop_reason"):
        _frame(is_dropped=False, drop_reason="DEVICE_LOST", missing_fields=())


def test_action_time_order_and_confidence() -> None:
    with pytest.raises(CanonicalFieldError, match="至少有 start_time_ns"):
        _action(
            start_time_ns=None,
            end_time_ns=None,
            missing_fields=("end_time_ns", "start_time_ns"),
        )
    with pytest.raises(CanonicalFieldError, match="不能早于"):
        _action(start_time_ns=200, end_time_ns=100)
    with pytest.raises(CanonicalFieldError, match="confidence"):
        _action(confidence=1.5)


def test_observation_time_and_validity_rules() -> None:
    with pytest.raises(CanonicalFieldError, match="至少有 timestamp_ns"):
        _observation(
            timestamp_ns=None,
            source_time_ns=None,
            missing_fields=("invalid_reason", "source_time_ns", "timestamp_ns"),
        )
    with pytest.raises(CanonicalFieldError, match="invalid_reason"):
        _observation(valid=False, invalid_reason=None, missing_fields=("invalid_reason",))


def test_unknown_frame_convention_is_rejected() -> None:
    with pytest.raises(CanonicalFieldError, match="frame_convention"):
        _sensor_stream(frame_convention="LEFT_HANDED")


def test_sensor_stream_frame_convention_may_be_missing() -> None:
    stream = _sensor_stream(frame_convention=None, missing_fields=("frame_convention",))
    assert "frame_convention" in stream.missing_fields


def test_episode_time_order_is_checked() -> None:
    with pytest.raises(CanonicalFieldError, match="不能早于"):
        _episode(start_time_ns=10, end_time_ns=5)


def test_episode_fields_cover_existing_adapter_declaration() -> None:
    """现有 Adapter 声明的每个字段都必须被 v1 Episode 直接承接或显式映射。"""
    by_name = {spec.name: spec for spec in EPISODE_FIELDS}
    for legacy in CANONICAL_EPISODE_FIELDS:
        target = LEGACY_EPISODE_FIELD_MAPPING.get(legacy.name, legacy.name)
        assert target in by_name, f"{legacy.name} 在 v1 Episode 中无落点"
        assert by_name[target].required == legacy.required, f"{legacy.name} 必填性不一致"
    # 旧声明与 v1 都使用同一个字段描述契约，便于后续 Adapter 直接迁移
    assert isinstance(CANONICAL_EPISODE_FIELDS[0], CanonicalField)
    assert isinstance(EPISODE_FIELDS[0], FieldSpec)


def test_field_lists_are_declared_per_entity() -> None:
    for fields, required in (
        (FRAME_FIELDS, "frame_index"),
        (VIDEO_STREAM_FIELDS, "stream_id"),
        (SENSOR_STREAM_FIELDS, "stream_id"),
        (ACTION_FIELDS, "action_id"),
        (OBSERVATION_FIELDS, "observation_id"),
        (EPISODE_FIELDS, "episode_key"),
    ):
        assert required in {spec.name for spec in fields if spec.required}
