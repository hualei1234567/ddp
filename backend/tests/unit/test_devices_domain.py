"""领域测试：机器人/设备/传感器生命周期与标定状态机。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.devices import (
    CalibrationProfile,
    CalibrationRecord,
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    Device,
    DeviceType,
    DomainValidationError,
    InvalidStateTransitionError,
    LifecycleStatus,
    Robot,
    Sensor,
    SensorSyncMode,
    SensorType,
)

from tests.calibration_fixtures import parameters_for


def _robot(status: LifecycleStatus = LifecycleStatus.PROVISIONED) -> Robot:
    return Robot(
        robot_id=uuid4(),
        project_id=uuid4(),
        code="robot-1",
        name="机器人一",
        robot_type="HUMANOID",
        status=status,
    )


def _sensor(**overrides: object) -> Sensor:
    values: dict[str, object] = {
        "sensor_id": uuid4(),
        "project_id": uuid4(),
        "code": "camera-1",
        "sensor_type": SensorType.RGB,
        "sample_rate_hz": 30.0,
        "coordinate_frame": "camera_link",
        "robot_id": uuid4(),
    }
    values.update(overrides)
    return Sensor(**values)  # type: ignore[arg-type]


def _profile(*, profile_version: int = 1) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id=uuid4(),
        project_id=uuid4(),
        name="默认标定配置",
        scope=CalibrationScope.SENSOR,
        target_type=CalibrationTargetType.SENSOR,
        profile_version=profile_version,
        schema_version="ddp.calibration/1.0.0",
        required_types=(CalibrationType.INTRINSICS, CalibrationType.EXTRINSICS),
    )


def _record(
    *,
    status: CalibrationStatus = CalibrationStatus.DRAFT,
    calibration_type: CalibrationType = CalibrationType.INTRINSICS,
    evidence_asset_ids: tuple[UUID, ...] = (),
    performed_by: UUID | None = None,
    validated_by: UUID | None = None,
    validated_at: datetime | None = None,
    parent_frame: str | None = None,
    child_frame: str | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    parameters: dict[str, object] | None = None,
) -> CalibrationRecord:
    return CalibrationRecord(
        calibration_id=uuid4(),
        project_id=uuid4(),
        profile_id=uuid4(),
        scope=CalibrationScope.SENSOR,
        target_type=CalibrationTargetType.SENSOR,
        target_id=uuid4(),
        calibration_type=calibration_type,
        method="charuco",
        parameters=_valid_parameters() if parameters is None else parameters,
        valid_from=valid_from or datetime(2026, 1, 1, tzinfo=UTC),
        evidence_asset_ids=evidence_asset_ids,
        checksum="sha256:abc",
        performed_by=performed_by or uuid4(),
        status=status,
        valid_to=valid_to,
        parent_frame=parent_frame,
        child_frame=child_frame,
        validated_by=validated_by,
        validated_at=validated_at,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LifecycleStatus.PROVISIONED, LifecycleStatus.ACTIVE),
        (LifecycleStatus.PROVISIONED, LifecycleStatus.SUSPENDED),
        (LifecycleStatus.PROVISIONED, LifecycleStatus.RETIRED),
        (LifecycleStatus.ACTIVE, LifecycleStatus.IN_MAINTENANCE),
        (LifecycleStatus.ACTIVE, LifecycleStatus.FAULT),
        (LifecycleStatus.ACTIVE, LifecycleStatus.SUSPENDED),
        (LifecycleStatus.ACTIVE, LifecycleStatus.RETIRED),
        (LifecycleStatus.IN_MAINTENANCE, LifecycleStatus.ACTIVE),
        (LifecycleStatus.IN_MAINTENANCE, LifecycleStatus.FAULT),
        (LifecycleStatus.FAULT, LifecycleStatus.IN_MAINTENANCE),
        (LifecycleStatus.FAULT, LifecycleStatus.SUSPENDED),
        (LifecycleStatus.SUSPENDED, LifecycleStatus.ACTIVE),
        (LifecycleStatus.SUSPENDED, LifecycleStatus.RETIRED),
    ],
)
def test_lifecycle_allows_contract_transitions(
    current: LifecycleStatus,
    target: LifecycleStatus,
) -> None:
    assert _robot(current).transition_to(target).status is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LifecycleStatus.PROVISIONED, LifecycleStatus.IN_MAINTENANCE),
        (LifecycleStatus.PROVISIONED, LifecycleStatus.FAULT),
        (LifecycleStatus.FAULT, LifecycleStatus.ACTIVE),
        (LifecycleStatus.FAULT, LifecycleStatus.PROVISIONED),
        (LifecycleStatus.SUSPENDED, LifecycleStatus.IN_MAINTENANCE),
        (LifecycleStatus.RETIRED, LifecycleStatus.ACTIVE),
        (LifecycleStatus.RETIRED, LifecycleStatus.SUSPENDED),
        (LifecycleStatus.RETIRED, LifecycleStatus.RETIRED),
        (LifecycleStatus.ACTIVE, LifecycleStatus.ACTIVE),
    ],
)
def test_lifecycle_rejects_unknown_transitions(
    current: LifecycleStatus,
    target: LifecycleStatus,
) -> None:
    with pytest.raises(InvalidStateTransitionError):
        _robot(current).transition_to(target)


def test_retired_is_terminal_for_every_target() -> None:
    retired = _robot(LifecycleStatus.RETIRED)
    for target in LifecycleStatus:
        with pytest.raises(InvalidStateTransitionError):
            retired.transition_to(target)


def test_named_actions_use_state_machine() -> None:
    robot = _robot()
    active = robot.activate()
    assert active.status is LifecycleStatus.ACTIVE
    assert active.enter_maintenance().activate().status is LifecycleStatus.ACTIVE
    assert active.report_fault().status is LifecycleStatus.FAULT
    assert active.suspend().status is LifecycleStatus.SUSPENDED
    assert robot.retire().status is LifecycleStatus.RETIRED


def test_transition_returns_new_instance() -> None:
    robot = _robot()
    active = robot.activate()
    assert robot.status is LifecycleStatus.PROVISIONED
    assert active is not robot


def test_entity_rejects_unknown_status_and_enum_values() -> None:
    with pytest.raises(DomainValidationError):
        _robot("BROKEN")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        Device(
            device_id=uuid4(),
            project_id=uuid4(),
            code="device-1",
            name="设备一",
            device_type="UNKNOWN",
        )


def test_sensor_requires_robot_or_device() -> None:
    with pytest.raises(DomainValidationError, match="至少关联"):
        _sensor(robot_id=None)


def test_sensor_accepts_device_only_mount() -> None:
    sensor = _sensor(robot_id=None, device_id=uuid4())
    assert sensor.device_id is not None
    assert sensor.sync_mode is SensorSyncMode.NONE


def test_sensor_resolution_must_be_paired() -> None:
    with pytest.raises(DomainValidationError, match="resolution"):
        _sensor(resolution_w=1920)
    with pytest.raises(DomainValidationError, match="resolution"):
        _sensor(resolution_h=1080)


def test_sensor_rejects_invalid_measurements() -> None:
    with pytest.raises(DomainValidationError):
        _sensor(sample_rate_hz=0)
    with pytest.raises(DomainValidationError):
        _sensor(bit_depth=0)
    with pytest.raises(DomainValidationError):
        _sensor(sensor_type="LIDAR")


def test_device_type_is_validated() -> None:
    device = Device(
        device_id=uuid4(),
        project_id=uuid4(),
        code="compute-1",
        name="计算单元",
        device_type=DeviceType.COMPUTE,
    )
    assert device.device_type is DeviceType.COMPUTE


def test_calibration_profile_accepts_config_version() -> None:
    profile = _profile(profile_version=2)
    assert profile.profile_version == 2
    assert profile.required_types == (
        CalibrationType.INTRINSICS,
        CalibrationType.EXTRINSICS,
    )


def test_calibration_draft_allows_no_evidence_but_submit_requires_it() -> None:
    draft = _record()
    assert draft.evidence_asset_ids == ()
    with pytest.raises(DomainValidationError, match="证据"):
        draft.submit()

    submitted = _record(evidence_asset_ids=(uuid4(),)).submit()
    assert submitted.status is CalibrationStatus.SUBMITTED


def test_calibration_state_machine_allows_contract_path() -> None:
    evidence = (uuid4(),)
    submitted = _record(evidence_asset_ids=evidence).submit()
    validated = submitted.validate(uuid4(), datetime(2026, 1, 2, tzinfo=UTC))
    assert validated.status is CalibrationStatus.VALIDATED
    active = validated.activate()
    assert active.status is CalibrationStatus.ACTIVE
    assert active.supersede().status is CalibrationStatus.SUPERSEDED
    assert active.revoke().status is CalibrationStatus.REVOKED
    assert active.expire().status is CalibrationStatus.EXPIRED

    rejected = submitted.reject()
    assert rejected.status is CalibrationStatus.REJECTED
    assert rejected.reopen().status is CalibrationStatus.DRAFT


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (CalibrationStatus.DRAFT, "activate"),
        (CalibrationStatus.SUBMITTED, "activate"),
        (CalibrationStatus.VALIDATED, "revoke"),
        (CalibrationStatus.SUPERSEDED, "activate"),
        (CalibrationStatus.REVOKED, "activate"),
        (CalibrationStatus.EXPIRED, "activate"),
    ],
)
def test_calibration_rejects_invalid_transitions(
    status: CalibrationStatus,
    action: str,
) -> None:
    record = _record(
        status=status,
        evidence_asset_ids=(uuid4(),),
        validated_by=uuid4() if status is not CalibrationStatus.DRAFT else None,
        validated_at=(
            datetime(2026, 1, 2, tzinfo=UTC) if status is not CalibrationStatus.DRAFT else None
        ),
    )
    with pytest.raises(InvalidStateTransitionError):
        getattr(record, action)()


def test_calibration_activation_requires_effective_validity_window() -> None:
    performer = uuid4()
    validator = uuid4()
    validated = _record(
        status=CalibrationStatus.VALIDATED,
        evidence_asset_ids=(uuid4(),),
        performed_by=performer,
        validated_by=validator,
        validated_at=datetime(2026, 1, 2, tzinfo=UTC),
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2026, 12, 1, tzinfo=UTC),
    )
    assert validated.activate(now=datetime(2026, 6, 1, tzinfo=UTC)).status is (
        CalibrationStatus.ACTIVE
    )


def test_calibration_activation_rejects_expired_window() -> None:
    record = _record(
        status=CalibrationStatus.VALIDATED,
        evidence_asset_ids=(uuid4(),),
        validated_by=uuid4(),
        validated_at=datetime(2026, 1, 2, tzinfo=UTC),
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2026, 2, 1, tzinfo=UTC),
    )
    with pytest.raises(DomainValidationError, match="过期"):
        record.activate(now=datetime(2026, 6, 1, tzinfo=UTC))


def test_calibration_activation_rejects_future_valid_from() -> None:
    record = _record(
        status=CalibrationStatus.VALIDATED,
        evidence_asset_ids=(uuid4(),),
        validated_by=uuid4(),
        validated_at=datetime(2026, 1, 2, tzinfo=UTC),
        valid_from=datetime(2026, 12, 1, tzinfo=UTC),
    )
    with pytest.raises(DomainValidationError, match="尚未生效"):
        record.activate(now=datetime(2026, 6, 1, tzinfo=UTC))


def test_calibration_requires_four_eyes() -> None:
    performer = uuid4()
    record = _record(
        status=CalibrationStatus.SUBMITTED,
        evidence_asset_ids=(uuid4(),),
        performed_by=performer,
    )
    with pytest.raises(DomainValidationError, match="分离"):
        record.validate(performer, datetime(2026, 1, 2, tzinfo=UTC))

    validated = record.validate(uuid4(), datetime(2026, 1, 2, tzinfo=UTC))
    assert validated.validated_by is not None
    assert validated.validated_at is not None


def test_validated_statuses_require_validator_fields() -> None:
    with pytest.raises(DomainValidationError, match="validated_by"):
        _record(
            status=CalibrationStatus.VALIDATED,
            evidence_asset_ids=(uuid4(),),
        )


def test_non_draft_status_requires_evidence() -> None:
    with pytest.raises(DomainValidationError, match="证据"):
        _record(status=CalibrationStatus.SUBMITTED)


def test_calibration_requires_frames_for_extrinsics() -> None:
    with pytest.raises(DomainValidationError, match="parent_frame"):
        _record(calibration_type=CalibrationType.EXTRINSICS)
    with pytest.raises(DomainValidationError, match="同时提供"):
        _record(
            calibration_type=CalibrationType.EXTRINSICS,
            parent_frame="base_link",
        )


def test_calibration_validity_window_must_be_positive() -> None:
    valid_from = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DomainValidationError, match="valid_to"):
        _record(valid_from=valid_from, valid_to=valid_from)


def test_calibration_requires_timezone_aware_times() -> None:
    with pytest.raises(DomainValidationError, match="时区"):
        _record(valid_from=datetime(2026, 1, 1))  # noqa: DTZ001


def _valid_parameters() -> dict[str, object]:
    """G-19 之后参数形状由契约 schema 强制；测试必须用合法参数。"""
    return parameters_for("INTRINSICS")


def test_calibration_rejects_parameters_violating_contract_schema() -> None:
    """G-19：参数形状由契约冻结，缺字段/枚举错误必须被拒且消息可定位。"""
    with pytest.raises(DomainValidationError, match="INTRINSICS 参数不合法"):
        _record(parameters={"fx": 1.0})

    missing = _valid_parameters()
    del missing["image_width"]
    with pytest.raises(DomainValidationError, match="image_width"):
        _record(parameters=missing)

    bad_enum = _valid_parameters()
    bad_enum["camera_model"] = "FISHEYE_NOT_IN_CONTRACT"
    with pytest.raises(DomainValidationError, match="camera_model"):
        _record(parameters=bad_enum)


def test_calibration_rejects_type_mismatched_parameters() -> None:
    """校验按 calibration_type 选定义：IMU 参数放进 INTRINSICS 记录必须被拒。"""
    with pytest.raises(DomainValidationError, match="INTRINSICS 参数不合法"):
        _record(parameters=parameters_for("IMU"))


def test_calibration_accepts_all_contract_calibration_types() -> None:
    """8 种 calibration_type 的合法参数都必须能构造记录（防夹具漂移）。"""
    for calibration_type in CalibrationType:
        # 外参/手眼在领域层额外要求父子坐标系，这里一并给出。
        frames = (
            {"parent_frame": "base_link", "child_frame": "camera_link"}
            if calibration_type in (CalibrationType.EXTRINSICS, CalibrationType.HAND_EYE)
            else {}
        )
        record = _record(
            calibration_type=calibration_type,
            parameters=parameters_for(calibration_type.value),
            **frames,
        )
        assert record.calibration_type is calibration_type
