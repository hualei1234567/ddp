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
