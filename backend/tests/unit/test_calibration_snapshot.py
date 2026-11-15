"""ADR-0020：G-04 版本分离 / G-05 Profile 快照 / G-06 target_id。

核心断言：**Profile 事后被改，记录的快照不变**——这是"标定可复现"的底线。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.errors import DomainValidationError

_NOW = datetime(2026, 11, 15, tzinfo=UTC)

# G-19 之后参数形状由契约 schema 强制，测试必须给合法参数。
_VALID_INTRINSICS = {
    "camera_model": "PINHOLE",
    "image_width": 1280,
    "image_height": 720,
    "fx": 900.0,
    "fy": 900.0,
    "cx": 640.0,
    "cy": 360.0,
    "distortion_model": "plumb_bob",
    "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
    "unit": "px",
}


def _profile(**overrides: object) -> CalibrationProfile:
    base: dict[str, object] = {
        "profile_id": uuid4(),
        "project_id": uuid4(),
        "name": "出厂标定模板",
        "scope": "SYSTEM",
        "target_type": "SENSOR",
        "profile_version": 3,
        "schema_version": "ddp.calibration/1.0.0",
        "required_types": ("INTRINSICS", "EXTRINSICS"),
        "selection_rule": {"prefer": "latest_validated"},
    }
    base.update(overrides)
    return CalibrationProfile(**base)  # type: ignore[arg-type]


def _record(**overrides: object) -> CalibrationRecord:
    base: dict[str, object] = {
        "calibration_id": uuid4(),
        "project_id": uuid4(),
        "profile_id": uuid4(),
        "scope": "SENSOR",
        "target_type": "SENSOR",
        "target_id": uuid4(),
        "calibration_type": "INTRINSICS",
        "method": "棋盘格",
        "parameters": dict(_VALID_INTRINSICS),
        "valid_from": _NOW,
        "evidence_asset_ids": (),
        "checksum": "sha256:" + "a" * 64,
        "performed_by": uuid4(),
    }
    base.update(overrides)
    return CalibrationRecord(**base)  # type: ignore[arg-type]


# --- G-04：业务版本与并发锁分离 ---------------------------------------------


def test_calibration_version_and_lock_version_are_independent() -> None:
    """状态迁移会推 lock_version，但不该改变业务修订号。"""
    record = _record(version=7, calibration_version=3)

    assert record.version == 7
    assert record.calibration_version == 3


def test_calibration_version_defaults_to_1() -> None:
    assert _record().calibration_version == 1


def test_calibration_version_must_be_positive() -> None:
    with pytest.raises(DomainValidationError, match="calibration_version"):
        _record(calibration_version=0)


# --- G-05：Profile 快照 -----------------------------------------------------


def test_record_stores_profile_snapshot_fields() -> None:
    record = _record(
        profile_version=3,
        profile_schema_version="ddp.calibration/1.0.0",
        profile_snapshot={"required_types": ["INTRINSICS"], "selection_rule": {}},
    )

    assert record.profile_version == 3
    assert record.profile_schema_version == "ddp.calibration/1.0.0"
    assert record.profile_snapshot["required_types"] == ["INTRINSICS"]


def test_profile_mutation_does_not_affect_recorded_snapshot() -> None:
    """这是 ADR-0020 的核心保证：事后改 Profile 不影响历史记录的复现依据。"""
    profile = _profile(required_types=("INTRINSICS",))
    record = _record(
        profile_version=profile.profile_version,
        profile_schema_version=profile.schema_version,
        profile_snapshot={
            "required_types": [str(item) for item in profile.required_types],
            "selection_rule": dict(profile.selection_rule),
        },
    )

    updated = replace(profile, profile_version=4, required_types=("INTRINSICS", "EXTRINSICS"))

    # 记录侧完全没有被影响。
    assert record.profile_version == 3
    assert record.profile_snapshot["required_types"] == ["INTRINSICS"]
    # Profile 自己变了。
    assert updated.profile_version == 4
    assert len(updated.required_types) == 2


def test_profile_version_must_be_positive_when_present() -> None:
    with pytest.raises(DomainValidationError, match="profile_version"):
        _record(profile_version=0)


def test_profile_snapshot_top_level_is_immutable() -> None:
    """顶层键不可增删改。

    注意：``frozen_mapping`` 只做浅冻结，嵌套 dict 仍可变。这是既有工具的
    已知限制，不在本 ADR 范围内改；测试如实断言"顶层不可变"，
    不假装深不可变。
    """
    record = _record(profile_snapshot={"selection_rule": {"prefer": "latest"}})

    with pytest.raises(TypeError):
        record.profile_snapshot["other"] = 1  # type: ignore[index]


# --- G-06：Profile target_id ------------------------------------------------


def test_system_scope_cannot_bind_target() -> None:
    """SYSTEM 是项目级模板；绑具体目标会让"模板"语义失效。"""
    with pytest.raises(DomainValidationError, match="SYSTEM"):
        _profile(scope="SYSTEM", target_id=uuid4())


def test_sensor_scope_can_bind_target() -> None:
    profile = _profile(scope="SENSOR", target_id=uuid4())

    assert profile.target_id is not None


def test_profile_without_target_is_allowed() -> None:
    assert _profile().target_id is None
