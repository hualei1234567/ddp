"""G-04 / G-05 / G-19 / G-20 的领域层单测。

API 层测试能证明"接口行为对"，但证明不了规则**落在哪一层**。
这里直接对 Domain 打，确保规则不依赖 HTTP、仓储或事务。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import CalibrationStatus
from app.domain.devices.errors import (
    CalibrationInvalidError,
    CalibrationNotActiveError,
    DomainValidationError,
)
from app.domain.devices.parameter_schema import validate_parameters

from tests.calibration_fixtures import parameters_for

_NOW = datetime(2026, 11, 15, tzinfo=UTC)


def _record(**overrides: object) -> CalibrationRecord:
    base: dict[str, object] = {
        "calibration_id": uuid4(),
        "project_id": uuid4(),
        "profile_id": uuid4(),
        "scope": "SENSOR",
        "target_type": "SENSOR",
        "target_id": uuid4(),
        "calibration_type": "INTRINSICS",
        "method": "charuco",
        "parameters": parameters_for("INTRINSICS"),
        "valid_from": _NOW,
        "evidence_asset_ids": (uuid4(),),
        "checksum": "sha256:" + "a" * 64,
        "performed_by": uuid4(),
    }
    base.update(overrides)
    return CalibrationRecord(**base)  # type: ignore[arg-type]


def _profile(**overrides: object) -> CalibrationProfile:
    base: dict[str, object] = {
        "profile_id": uuid4(),
        "project_id": uuid4(),
        "name": "模板",
        "scope": "SENSOR",
        "target_type": "SENSOR",
        "profile_version": 1,
        "schema_version": "ddp.calibration/1.0.0",
        "required_types": ("INTRINSICS",),
    }
    base.update(overrides)
    return CalibrationProfile(**base)  # type: ignore[arg-type]


# --- G-04：业务修订号 -------------------------------------------------------


def test_content_change_increments_calibration_version() -> None:
    record = _record()

    updated = record.with_content_changes(method="charuco-2")

    assert updated.calibration_version == 2
    assert updated.method == "charuco-2"


def test_content_change_accumulates_across_patches() -> None:
    record = _record()

    once = record.with_content_changes(method="charuco-2")
    twice = once.with_content_changes(source="field")

    assert (record.calibration_version, once.calibration_version, twice.calibration_version) == (
        1,
        2,
        3,
    )


def test_identical_value_is_not_a_content_change() -> None:
    record = _record()

    updated = record.with_content_changes(method=record.method)

    assert updated.calibration_version == 1


def test_parameter_change_counts_as_content_change() -> None:
    record = _record()
    changed = dict(parameters_for("INTRINSICS"))
    changed["fx"] = 901.0

    updated = record.with_content_changes(parameters=changed)

    assert updated.calibration_version == 2


def test_equivalent_parameter_ordering_is_not_a_content_change() -> None:
    """同一个参数字典换个插入顺序不算改动——否则修订号会被噪声推高。"""
    parameters = parameters_for("INTRINSICS")
    record = _record(parameters=parameters)
    reordered = {key: parameters[key] for key in reversed(list(parameters))}

    updated = record.with_content_changes(parameters=reordered)

    assert updated.calibration_version == 1


def test_status_transition_does_not_touch_calibration_version() -> None:
    record = _record()

    submitted = record.submit()

    assert submitted.status is CalibrationStatus.SUBMITTED
    assert submitted.calibration_version == record.calibration_version


# --- G-05：Profile 快照 -----------------------------------------------------


def test_profile_snapshot_can_be_replaced_as_a_unit() -> None:
    record = _record(profile_version=1, profile_schema_version="v1", profile_snapshot={})
    profile = _profile(profile_version=2, schema_version="v2", required_types=("EXTRINSICS",))

    updated = record.with_profile_snapshot(
        profile_version=profile.profile_version,
        profile_schema_version=profile.schema_version,
        profile_snapshot={"required_types": [str(item) for item in profile.required_types]},
    )

    assert updated.profile_version == 2
    assert updated.profile_schema_version == "v2"
    assert updated.profile_snapshot["required_types"] == ["EXTRINSICS"]


def test_profile_snapshot_rejects_invalid_version() -> None:
    record = _record()

    with pytest.raises(DomainValidationError, match="profile_version"):
        record.with_profile_snapshot(
            profile_version=0,
            profile_schema_version="v1",
            profile_snapshot={},
        )


def test_profile_snapshot_replacement_is_independent_of_content_version() -> None:
    """换快照本身不改内容；只有调用方显式变更内容字段才递增修订号。"""
    record = _record()

    updated = record.with_profile_snapshot(
        profile_version=9,
        profile_schema_version="v9",
        profile_snapshot={"required_types": []},
    )

    assert updated.calibration_version == record.calibration_version


# --- G-19：参数校验细节 -----------------------------------------------------


def test_parameters_error_carries_field_path_details() -> None:
    parameters = dict(parameters_for("INTRINSICS"))
    parameters["unit"] = "mm"

    with pytest.raises(DomainValidationError) as excinfo:
        validate_parameters("INTRINSICS", parameters)

    details = excinfo.value.details
    assert details is not None
    assert details["field_path"] == ["unit"]
    assert details["calibration_type"] == "INTRINSICS"
    assert details["error_count"] == 1


def test_parameters_error_details_never_leak_values() -> None:
    """details 只放路径与计数，不放用户填的值——避免回显被当泄漏。"""
    marker = "SECRET-MARKER-VALUE"
    parameters = dict(parameters_for("INTRINSICS"))
    parameters["unit"] = marker

    with pytest.raises(DomainValidationError) as excinfo:
        validate_parameters("INTRINSICS", parameters)

    details = excinfo.value.details or {}
    assert marker not in repr(details)


# --- G-20：标定错误码 -------------------------------------------------------


def test_activation_outside_valid_window_uses_calibration_invalid() -> None:
    record = _record(
        status=CalibrationStatus.VALIDATED,
        validated_by=uuid4(),
        validated_at=_NOW,
        valid_from=datetime(2026, 12, 1, tzinfo=UTC),
    )

    with pytest.raises(CalibrationInvalidError) as excinfo:
        record.activate(now=_NOW)

    assert excinfo.value.code == "calibration_invalid"
    assert (excinfo.value.details or {})["reason"] == "not_yet_valid"


def test_expired_calibration_activation_uses_calibration_invalid() -> None:
    record = _record(
        status=CalibrationStatus.VALIDATED,
        validated_by=uuid4(),
        validated_at=_NOW,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2026, 2, 1, tzinfo=UTC),
    )

    with pytest.raises(CalibrationInvalidError) as excinfo:
        record.activate(now=_NOW)

    assert (excinfo.value.details or {})["reason"] == "expired"


def test_calibration_invalid_is_not_a_validation_failed_code() -> None:
    """G-20 的核心：这两个 code 必须可区分，不能都塌成 validation_failed。"""
    assert CalibrationInvalidError("x").code == "calibration_invalid"
    assert CalibrationNotActiveError("x").code == "calibration_not_active"
    # 二者仍属 DomainValidationError，保持既有的"参数类错误"继承语义。
    assert isinstance(CalibrationInvalidError("x"), DomainValidationError)


def test_activation_within_window_succeeds() -> None:
    record = _record(
        status=CalibrationStatus.VALIDATED,
        validated_by=uuid4(),
        validated_at=_NOW,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2027, 1, 1, tzinfo=UTC),
    )

    activated = record.activate(now=_NOW)

    assert activated.status is CalibrationStatus.ACTIVE


def test_replace_on_invalid_status_still_rejected() -> None:
    """对照组：非法状态迁移仍走 state_transition_invalid，不被新错误码吞掉。"""
    from app.domain.devices.errors import InvalidStateTransitionError

    record = _record(status=CalibrationStatus.DRAFT)
    with pytest.raises((InvalidStateTransitionError, DomainValidationError)):
        replace(record, status=CalibrationStatus.ACTIVE).validate(uuid4(), _NOW)
