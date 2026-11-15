"""设备域错误码映射单元测试。"""

from __future__ import annotations

from uuid import uuid4

from app.api.errors import domain_error_to_app, integrity_error_to_app
from app.domain.devices.errors import (
    DeviceDomainError,
    DomainValidationError,
    DuplicateResourceError,
    FourEyesViolationError,
    InvalidStateTransitionError,
    ResourceImmutableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from sqlalchemy.exc import IntegrityError


def test_domain_errors_map_to_stable_codes() -> None:
    cases = [
        (DomainValidationError("非法"), "validation_failed", 422),
        (DuplicateResourceError("重复"), "duplicate_resource", 409),
        (InvalidStateTransitionError("设备", "ACTIVE", "RETIRED"), "state_transition_invalid", 409),
        (VersionConflictError("设备", 3), "version_conflict", 409),
        (ResourceImmutableError("冻结"), "resource_immutable", 409),
        (ResourceNotFoundError("设备", uuid4()), "resource_not_found", 404),
        (
            ResourceNotFoundError(
                "标定目标",
                uuid4(),
                code="calibration_target_invalid",
            ),
            "calibration_target_invalid",
            422,
        ),
        (FourEyesViolationError("执行人不能校验"), "forbidden", 403),
    ]

    for exc, code, status_code in cases:
        mapped = domain_error_to_app(exc)
        assert mapped.code == code
        assert mapped.status_code == status_code


def test_integrity_error_maps_unique_and_generic() -> None:
    unique = IntegrityError(
        "INSERT",
        {},
        Exception("duplicate key value violates unique constraint uq_devices_project_id"),
    )
    foreign_key = IntegrityError(
        "INSERT",
        {},
        Exception("insert or update violates foreign key constraint"),
    )

    assert integrity_error_to_app(unique).code == "duplicate_resource"
    assert integrity_error_to_app(unique).status_code == 409
    assert integrity_error_to_app(foreign_key).code == "conflict"
    assert "foreign key" not in integrity_error_to_app(foreign_key).message


def test_domain_validation_message_is_not_echoed() -> None:
    marker = "INPUT-SECRET-MARKER"
    mapped = domain_error_to_app(DomainValidationError(f"device_type 取值非法：{marker}"))

    assert mapped.code == "validation_failed"
    assert marker not in mapped.message


def test_unknown_domain_code_converges_to_stable_fallback() -> None:
    class _UnknownError(DeviceDomainError):
        code = "unexpected_internal_code"

    mapped = domain_error_to_app(_UnknownError("internal-detail-SECRET"))

    assert mapped.code == "validation_failed"
    assert mapped.status_code == 422
    assert "internal-detail-SECRET" not in mapped.message


def test_integrity_error_does_not_leak_sql_or_parameters() -> None:
    marker = "SQL-SECRET-PARAM"
    error = IntegrityError(
        "INSERT INTO devices (code) VALUES (%s)",
        (marker,),
        Exception(f"CHECK constraint failed: code = '{marker}'"),
    )

    mapped = integrity_error_to_app(error)

    assert mapped.code == "conflict"
    assert mapped.status_code == 409
    assert "INSERT" not in mapped.message
    assert marker not in mapped.message


# --- G-19 / G-20：details 透传白名单与新错误码 -------------------------------


def test_validation_failed_details_are_passed_through() -> None:
    """G-19：字段路径必须到客户端，否则报错只说明"参数非法"。"""
    exc = DomainValidationError(
        "INTRINSICS 参数不合法：unit — 'mm' 不是 'px'",
        details={"calibration_type": "INTRINSICS", "field_path": ["unit"], "error_count": 1},
    )

    mapped = domain_error_to_app(exc)

    assert mapped.code == "validation_failed"
    assert mapped.details == {
        "calibration_type": "INTRINSICS",
        "field_path": ["unit"],
        "error_count": 1,
    }


def test_details_are_not_leaked_for_codes_outside_whitelist() -> None:
    """details 只对白名单 code 透传；其余 code 的内部结构不得回显。"""
    exc = DuplicateResourceError(
        "项目内业务键重复",
        details={"internal_table": "devices", "row_ids": ["secret-id"]},
    )

    mapped = domain_error_to_app(exc)

    assert mapped.code == "duplicate_resource"
    assert mapped.details is None


def test_calibration_not_active_details_are_passed_through() -> None:
    from app.domain.devices.errors import CalibrationNotActiveError

    exc = CalibrationNotActiveError(
        "目标缺少有效标定",
        details={"target_type": "DEVICE", "missing_calibration_types": ["INTRINSICS"]},
    )

    mapped = domain_error_to_app(exc)

    assert mapped.code == "calibration_not_active"
    assert mapped.status_code == 422
    assert mapped.details == {
        "target_type": "DEVICE",
        "missing_calibration_types": ["INTRINSICS"],
    }


def test_calibration_invalid_maps_to_its_own_code() -> None:
    """G-20：calibration_invalid 必须可达，不能塌成 validation_failed。"""
    from app.domain.devices.errors import CalibrationInvalidError

    mapped = domain_error_to_app(
        CalibrationInvalidError("标定已过期，不能激活", details={"reason": "expired"})
    )

    assert mapped.code == "calibration_invalid"
    assert mapped.status_code == 422
    assert mapped.details == {"reason": "expired"}


def test_domain_error_without_details_still_maps_cleanly() -> None:
    """details 是可选参数，不传时不得出现空 dict 污染错误体。"""
    mapped = domain_error_to_app(DomainValidationError("普通校验失败"))

    assert mapped.code == "validation_failed"
    assert mapped.details is None


def test_calibration_checksum_mismatch_has_its_own_code_and_hides_details() -> None:
    """契约 §8.6：篡改检测是专用码；其 details 含记录 ID，不得回显。"""
    from app.domain.devices.errors import CalibrationChecksumMismatchError

    exc = CalibrationChecksumMismatchError(
        "标定 checksum 与内容不一致",
        details={"calibration_id": "secret-id", "field": "checksum"},
    )

    mapped = domain_error_to_app(exc)

    assert mapped.code == "calibration_checksum_mismatch"
    assert mapped.status_code == 422
    assert mapped.details is None


def test_calibration_active_conflict_is_a_409_device_error() -> None:
    """契约 §8.6：无法原子替代时是 409，不是 422。"""
    from app.domain.devices.errors import CalibrationActiveConflictError

    exc = CalibrationActiveConflictError(
        "同一标定目标已有 ACTIVE 记录，且无法原子替代",
        details={"target_type": "SENSOR", "calibration_type": "INTRINSICS"},
    )

    mapped = domain_error_to_app(exc)

    assert mapped.code == "calibration_active_conflict"
    assert mapped.status_code == 409
    # details 里只有类型名，没有记录 ID，但当前策略是"非白名单 code 一律不回显"。
    assert mapped.details is None
