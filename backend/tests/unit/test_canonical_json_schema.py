"""Canonical v1 共享 JSON Schema 与字段声明的一致性（V31-017）。

字段声明（``*_FIELDS``）是唯一权威，``packages/contracts/schemas/canonical-v1/``
是它的提交产物。本文件锁定三件事：

1. 提交产物与生成结果**逐字节**一致——手改生成文件会立刻失败；
2. schema 本身是合法 draft 2020-12，且**闭合**（``additionalProperties: false``）；
3. schema 与领域校验**语义一致**：每个实体的合法 ``to_dict`` 输出通过校验，
   空值未登记 ``missing_fields``、未知字段、错误版本都被拒绝。

第 3 点是关键：只比字段名不够。若 schema 把 ``timestamp_ns`` 声明成整数，
而 ``to_dict`` 输出字符串，字段名一致但消费者会全部拒绝——这类漂移只有用
真实 ``to_dict`` 输出跑一遍才能发现。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ddp_data.canonical.json_schema import (
    ENTITIES,
    canonical_json_schemas,
    render_json_schema,
)
from ddp_data.canonical.v1 import (
    SCHEMA_VERSION,
    Action,
    Episode,
    Frame,
    Observation,
    SensorStream,
    VideoStream,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "packages" / "contracts" / "schemas" / "canonical-v1"

# 每个实体的"全字段填充"样本；与 test_canonical_v1.py 的构造一致，
# 保证 schema 校验的是领域真正会产出的形状。
_SAMPLES: dict[str, object] = {
    "Frame": Frame(
        frame_index=0,
        sequence=0,
        timestamp_ns=1_700_000_000_000_000_000,
        source_time_ns=1_700_000_000_000_000_000,
        source_time_domain="UTC",
        clock_id="host-01",
        time_base_ref="ep1-time-base",
        uncertainty_ns=1_000_000,
        observation_ids=("obs-1",),
        is_dropped=False,
        drop_reason=None,
        missing_fields=("drop_reason",),
    ),
    "VideoStream": VideoStream(
        stream_id="ep1:video:0",
        camera_name="main",
        camera_name_raw="Egocentric main",
        modality="video",
        media_format="mp4",
        storage_reference="s3://ddp/raw/videos/main.mp4",
        excerpt_start_ns=0,
        duration_ns=5_000_000_000,
        fps=30.0,
        frame_count=150,
        width=1280,
        height=720,
        size_bytes=1024,
        checksum="sha256:abc",
        encoding="h264",
        dropped_count=0,
        missing_fields=(),
    ),
    "SensorStream": SensorStream(
        stream_id="ep1:imu",
        sensor_name="imu0",
        sensor_type="IMU",
        modality="imu",
        sample_rate_hz=200.0,
        channel_names=("ax", "ay", "az"),
        unit="m/s^2",
        quantity_kind="linear_acceleration",
        frame_id="sensor-01/imu_link",
        frame_convention="FLU",
        duration_ns=5_000_000_000,
        sample_count=1000,
        storage_reference="s3://ddp/raw/imu.parquet",
        checksum="sha256:def",
        calibration_id="cal-1",
        calibration_version=1,
        # 1.0 完整形态：显式声明版本，避免被 1.1 的新增字段校验规则影响。
        schema_version="ddp.canonical/1.0.0",
        missing_fields=(),
    ),
    "Action": Action(
        action_id="act-1",
        episode_key="ep1",
        action_type="JOINT_TARGET",
        start_time_ns=100,
        end_time_ns=200,
        values=(0.1, 0.2),
        unit="rad",
        frame_id="joint/0",
        joint_names=("j0", "j1"),
        source="HUMAN_DEMO",
        confidence=0.9,
        action_schema_ref="ddp.action.joint_target/1.0.0",
        frame_index=0,
        missing_fields=(),
    ),
    "Observation": Observation(
        observation_id="obs-1",
        episode_key="ep1",
        observation_type="IMU",
        stream_id="ep1:imu",
        sensor_id="sensor-1",
        frame_index=0,
        timestamp_ns=100,
        source_time_ns=100,
        sequence=0,
        frame_id="sensor-01/imu_link",
        calibration_id="cal-1",
        calibration_version=1,
        unit="m/s^2",
        quantity_kind="linear_acceleration",
        shape=(3,),
        dtype="float32",
        encoding="ARROW",
        payload_ref="s3://ddp/canonical/obs-1.parquet",
        checksum="sha256:ghi",
        valid=True,
        invalid_reason=None,
        quality_flags=("OK",),
        missing_fields=("invalid_reason",),
    ),
    "Episode": Episode(
        episode_key="ep1",
        task_name="pick",
        instruction="pick the cube",
        video_stream_ids=("ep1:video:0",),
        environment="lab",
        setting="table",
        episode_seconds=10.0,
        source_fps=30.0,
        has_wrist=False,
        raw={"example_id": "ep1"},
        sensor_stream_ids=("ep1:imu",),
        action_ids=("act-1",),
        observation_ids=("obs-1",),
        frame_count=300,
        episode_id="018f1f7e-7c00-7a2b-9d10-2f6c1a9b3c4d",
        project_id="018e2a10-0000-7000-8000-000000000001",
        start_time_ns=0,
        end_time_ns=10_000_000_000,
        missing_fields=(),
    ),
}


def _committed(filename: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))


def _validator(entity_name: str) -> Draft202012Validator:
    entity = next(item for item in ENTITIES if item.name == entity_name)
    return Draft202012Validator(_committed(entity.filename))


@pytest.mark.parametrize("entity", ENTITIES, ids=[item.name for item in ENTITIES])
def test_committed_schema_matches_generator(entity) -> None:  # type: ignore[no-untyped-def]
    """提交产物必须逐字节等于生成结果；手改生成文件会立刻失败。"""
    expected = render_json_schema(canonical_json_schemas()[entity.filename])
    assert (SCHEMA_DIR / entity.filename).read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("entity", ENTITIES, ids=[item.name for item in ENTITIES])
def test_schema_is_valid_and_closed(entity) -> None:  # type: ignore[no-untyped-def]
    schema = _committed(entity.filename)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    # schema_version 用 enum（而非 const）：1.0 产物仍须通过校验，
    # 只允许当前版本会让所有历史产物校验失败（ADR-0049 向后兼容要求）。
    version_property = schema["properties"]["schema_version"]
    allowed = version_property.get("enum") or [version_property["const"]]
    assert SCHEMA_VERSION in allowed
    assert "ddp.canonical/1.0.0" in allowed
    # 每个领域字段都必须出现在 schema 里，否则消费者看不到它。
    declared = {spec.name for spec in entity.fields}
    assert declared <= set(schema["properties"])
    # 必填性必须与 FieldSpec 一致。
    required = set(schema["required"])
    assert {spec.name for spec in entity.fields if spec.required} <= required


@pytest.mark.parametrize("entity", ENTITIES, ids=[item.name for item in ENTITIES])
def test_domain_output_passes_schema(entity) -> None:  # type: ignore[no-untyped-def]
    """真实 ``to_dict`` 输出必须通过 schema——这才能证明类型映射正确。"""
    payload = _SAMPLES[entity.name].to_dict()  # type: ignore[attr-defined]
    errors = list(_validator(entity.name).iter_errors(payload))
    assert errors == [], [error.message for error in errors]


@pytest.mark.parametrize("entity", ENTITIES, ids=[item.name for item in ENTITIES])
def test_schema_rejects_unknown_field(entity) -> None:  # type: ignore[no-untyped-def]
    payload = _SAMPLES[entity.name].to_dict()  # type: ignore[attr-defined]
    payload["__unexpected__"] = 1
    with pytest.raises(ValidationError):
        _validator(entity.name).validate(payload)


@pytest.mark.parametrize("entity", ENTITIES, ids=[item.name for item in ENTITIES])
def test_schema_rejects_wrong_version(entity) -> None:  # type: ignore[no-untyped-def]
    payload = _SAMPLES[entity.name].to_dict()  # type: ignore[attr-defined]
    payload["schema_version"] = "ddp.canonical/1.0.0-draft"
    with pytest.raises(ValidationError):
        _validator(entity.name).validate(payload)


def test_missing_fields_enum_only_lists_optional_fields() -> None:
    """``missing_fields`` 的枚举必须只含可选字段：必填字段不能声明缺失。"""
    for entity in ENTITIES:
        schema = _committed(entity.filename)
        allowed = set(schema["properties"]["missing_fields"]["items"]["enum"])
        required = {spec.name for spec in entity.fields if spec.required}
        assert allowed.isdisjoint(required), entity.name
        assert allowed == {spec.name for spec in entity.fields if not spec.required}


def test_nanosecond_fields_are_decimal_strings_in_schema() -> None:
    """``*_ns`` 在 ``to_dict`` 输出字符串；schema 若声明整数会拒绝全部合法数据。"""
    for entity in ENTITIES:
        schema = _committed(entity.filename)
        for name, spec in schema["properties"].items():
            if name.endswith("_ns"):
                assert "string" in spec["type"], f"{entity.name}.{name}"
                assert spec["pattern"] == "^-?(0|[1-9][0-9]*)$", f"{entity.name}.{name}"


def test_export_check_mode_detects_drift(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``--check`` 在产物缺失时返回非零，避免 CI 静默放过漂移。"""
    import importlib.util

    script_path = REPO_ROOT / "scripts" / "export_canonical_schemas.py"
    spec = importlib.util.spec_from_file_location("export_canonical_schemas", script_path)
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    monkeypatch.setattr(exporter, "OUTPUT_DIR", tmp_path / "missing")
    assert exporter.main(["--check"]) == 1

    exporter.main([])
    assert exporter.main(["--check"]) == 0
