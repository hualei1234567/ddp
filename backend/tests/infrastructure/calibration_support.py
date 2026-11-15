"""标定仓储测试的共享构造器与种子流程。

只服务于 ``tests/infrastructure/test_calibration_*``，统一通过真实仓储命令
（add→submit→validate→activate）构造状态，避免测试绕过状态机直写。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.devices import (
    CalibrationProfile,
    CalibrationRecord,
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.devices import (
    SqlCalibrationProfileRepository,
    SqlCalibrationRecordRepository,
)
from sqlalchemy.orm import Session, sessionmaker

from tests.calibration_fixtures import parameters_for

VALID_FROM = datetime(2026, 1, 1, tzinfo=UTC)
VALIDATED_AT = datetime(2026, 1, 2, tzinfo=UTC)
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def profile(
    project_id: UUID,
    *,
    version: int = 1,
    scope: CalibrationScope = CalibrationScope.SENSOR,
    target_type: CalibrationTargetType = CalibrationTargetType.SENSOR,
) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id=uuid4(),
        project_id=project_id,
        name="默认标定配置",
        scope=scope,
        target_type=target_type,
        version=version,
        schema_version="ddp.calibration/1.0.0",
        required_types=(CalibrationType.INTRINSICS,),
    )


def draft_record(
    project_id: UUID,
    profile_id: UUID,
    *,
    target_id: UUID,
    performer: UUID,
    scope: CalibrationScope = CalibrationScope.SENSOR,
    target_type: CalibrationTargetType = CalibrationTargetType.SENSOR,
    calibration_type: CalibrationType = CalibrationType.INTRINSICS,
    evidence: tuple[UUID, ...] | None = None,
    valid_from: datetime = VALID_FROM,
    valid_to: datetime | None = None,
) -> CalibrationRecord:
    return CalibrationRecord(
        calibration_id=uuid4(),
        project_id=project_id,
        profile_id=profile_id,
        scope=scope,
        target_type=target_type,
        target_id=target_id,
        calibration_type=calibration_type,
        method="charuco",
        parameters=_valid_parameters(),
        valid_from=valid_from,
        evidence_asset_ids=(uuid4(),) if evidence is None else evidence,
        checksum="sha256:abc",
        performed_by=performer,
        status=CalibrationStatus.DRAFT,
        valid_to=valid_to,
    )


def add_profile(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    profile_obj: CalibrationProfile,
) -> None:
    with factory() as session:
        SqlCalibrationProfileRepository(session).add(scope, profile_obj)
        session.commit()


def seed_submitted(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    profile_obj: CalibrationProfile,
    *,
    target_id: UUID,
    performer: UUID,
    valid_from: datetime = VALID_FROM,
    valid_to: datetime | None = None,
) -> CalibrationRecord:
    draft = draft_record(
        scope.project_id,
        profile_obj.profile_id,
        target_id=target_id,
        performer=performer,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        repository.add(scope, draft)
        submitted = repository.submit(scope, draft, expected_version=1)
        session.commit()
        return submitted


def seed_validated(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    profile_obj: CalibrationProfile,
    *,
    target_id: UUID,
    performer: UUID,
    validator: UUID,
    valid_from: datetime = VALID_FROM,
    valid_to: datetime | None = None,
) -> CalibrationRecord:
    submitted = seed_submitted(
        factory,
        scope,
        profile_obj,
        target_id=target_id,
        performer=performer,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope, submitted.calibration_id)
        assert stored is not None
        validated = repository.validate(
            scope,
            stored,
            validator,
            VALIDATED_AT,
            expected_version=stored.version,
        )
        session.commit()
        return validated


def seed_active(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    profile_obj: CalibrationProfile,
    *,
    target_id: UUID,
    performer: UUID,
    validator: UUID,
    valid_from: datetime = VALID_FROM,
    valid_to: datetime | None = None,
) -> CalibrationRecord:
    validated = seed_validated(
        factory,
        scope,
        profile_obj,
        target_id=target_id,
        performer=performer,
        validator=validator,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope, validated.calibration_id)
        assert stored is not None
        active = repository.activate(
            scope,
            stored,
            expected_version=stored.version,
            now=NOW,
        )
        session.commit()
        return active


def _valid_parameters() -> dict[str, object]:
    """G-19 之后参数形状由契约 schema 强制；测试必须用合法参数。"""
    return parameters_for("INTRINSICS")
