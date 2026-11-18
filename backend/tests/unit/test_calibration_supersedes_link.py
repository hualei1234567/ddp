"""G-16：标定记录修订链的领域权威校验。

规则只允许存在一处：``validate_supersedes_link``。这里锁定四条语义——
存在、同项目、不自引用、不分叉——以及实体的自引用兜底。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.devices.calibration import CalibrationRecord, validate_supersedes_link
from app.domain.devices.errors import DomainValidationError

_NOW = datetime(2026, 11, 18, tzinfo=UTC)

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


def test_valid_supersedes_link_passes() -> None:
    predecessor = _record()
    successor = _record(project_id=predecessor.project_id)

    validate_supersedes_link(
        successor_id=successor.calibration_id,
        project_id=successor.project_id,
        supersedes_id=predecessor.calibration_id,
        predecessor=predecessor,
        existing_successor=None,
    )


def test_missing_predecessor_is_rejected() -> None:
    successor = _record()

    with pytest.raises(DomainValidationError, match="不存在或不属于当前项目"):
        validate_supersedes_link(
            successor_id=successor.calibration_id,
            project_id=successor.project_id,
            supersedes_id=uuid4(),
            predecessor=None,
            existing_successor=None,
        )


def test_cross_project_predecessor_is_rejected() -> None:
    successor = _record()
    predecessor = _record()  # 默认另一个 project_id

    assert predecessor.project_id != successor.project_id
    with pytest.raises(DomainValidationError, match="不存在或不属于当前项目"):
        validate_supersedes_link(
            successor_id=successor.calibration_id,
            project_id=successor.project_id,
            supersedes_id=predecessor.calibration_id,
            predecessor=predecessor,
            existing_successor=None,
        )


def test_self_reference_is_rejected_by_link_validator() -> None:
    record = _record()

    with pytest.raises(DomainValidationError, match="不能指向自身"):
        validate_supersedes_link(
            successor_id=record.calibration_id,
            project_id=record.project_id,
            supersedes_id=record.calibration_id,
            predecessor=record,
            existing_successor=None,
        )


def test_self_reference_is_rejected_by_entity() -> None:
    record = _record()
    with pytest.raises(DomainValidationError, match="不能指向自身"):
        _record(
            calibration_id=record.calibration_id,
            supersedes_id=record.calibration_id,
        )


def test_forked_chain_is_rejected() -> None:
    predecessor = _record()
    other_successor = _record(
        project_id=predecessor.project_id, supersedes_id=predecessor.calibration_id
    )
    successor = _record(project_id=predecessor.project_id)

    with pytest.raises(DomainValidationError, match="已有后继记录"):
        validate_supersedes_link(
            successor_id=successor.calibration_id,
            project_id=successor.project_id,
            supersedes_id=predecessor.calibration_id,
            predecessor=predecessor,
            existing_successor=other_successor,
        )


def test_same_successor_is_not_treated_as_fork() -> None:
    """重复校验同一条后继（幂等重放）不应被误判为分叉。"""
    predecessor = _record()
    successor = _record(project_id=predecessor.project_id, supersedes_id=predecessor.calibration_id)

    validate_supersedes_link(
        successor_id=successor.calibration_id,
        project_id=successor.project_id,
        supersedes_id=predecessor.calibration_id,
        predecessor=predecessor,
        existing_successor=successor,
    )
