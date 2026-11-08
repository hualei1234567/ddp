"""数据库兜底负向测试：目标投影完整性与已校验标定不可变。

这些用例绕过应用仓储，直接通过 ORM/SQL 写入，验证 SQLite 的
``create_all`` 路径（与 Alembic 0004 共用同一份 guard DDL）确实拦截非法写入。
PostgreSQL 的对应证据见 ``test_postgres_calibration_guards.py``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.devices import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    LifecycleStatus,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import (
    CalibrationRecord as CalibrationRecordModel,
)
from app.infrastructure.db.models.devices import (
    CalibrationTargetRef as CalibrationTargetRefModel,
)
from app.infrastructure.repositories.devices import (
    SqlCalibrationRecordRepository,
    SqlRobotRepository,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.calibration_support import (
    add_profile,
    draft_record,
    seed_validated,
)
from tests.infrastructure.calibration_support import (
    profile as make_profile,
)
from tests.infrastructure.conftest import DeviceIds


def _record_model(
    *,
    project_id,
    profile_id,
    target_type: CalibrationTargetType,
    target_id,
    performed_by,
    status: CalibrationStatus = CalibrationStatus.DRAFT,
) -> CalibrationRecordModel:
    return CalibrationRecordModel(
        id=uuid4(),
        project_id=project_id,
        profile_id=profile_id,
        scope=CalibrationScope.SENSOR.value,
        target_type=target_type.value,
        target_id=target_id,
        calibration_type=CalibrationType.INTRINSICS.value,
        method="direct-insert",
        parameters={},
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        status=status.value,
        evidence_asset_ids=[],
        checksum="sha256:direct",
        uncertainty={},
        performed_by=performed_by,
        created_by=performed_by,
        updated_by=performed_by,
    )


def test_repositories_maintain_target_projection_rows(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    with factory() as session:
        robot = session.get(
            CalibrationTargetRefModel,
            (ids.project_a, "ROBOT", ids.robot_a),
        )
        sensor = session.get(
            CalibrationTargetRefModel,
            (ids.project_a, "SENSOR", ids.sensor_a),
        )
        assert robot is not None
        assert robot.lifecycle_status == LifecycleStatus.PROVISIONED.value
        assert robot.is_retired is False
        assert sensor is not None
        assert sensor.lifecycle_status == LifecycleStatus.PROVISIONED.value


def test_target_projection_tracks_retirement(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        repository = SqlRobotRepository(session)
        robot = repository.get(scope_a, ids.robot_a)
        assert robot is not None
        repository.save(
            scope_a,
            robot.retire(),
            expected_version=robot.version,
        )
        session.commit()

    with factory() as session:
        ref = session.get(
            CalibrationTargetRefModel,
            (ids.project_a, "ROBOT", ids.robot_a),
        )
        assert ref is not None
        assert ref.lifecycle_status == LifecycleStatus.RETIRED.value
        assert ref.is_retired is True


def test_calibration_add_materialises_system_target(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(
        ids.project_a,
        scope=CalibrationScope.SYSTEM,
        target_type=CalibrationTargetType.SYSTEM,
    )
    add_profile(factory, scope_a, profile)
    record = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.project_a,
        performer=ids.user_a,
        scope=CalibrationScope.SYSTEM,
        target_type=CalibrationTargetType.SYSTEM,
    )

    with factory() as session:
        SqlCalibrationRecordRepository(session).add(scope_a, record)
        session.commit()

    with factory() as session:
        ref = session.get(
            CalibrationTargetRefModel,
            (ids.project_a, "SYSTEM", ids.project_a),
        )
        assert ref is not None
        assert ref.lifecycle_status == LifecycleStatus.ACTIVE.value
        assert ref.is_retired is False


def test_database_rejects_nonexistent_target(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    with factory() as session:
        session.add(
            _record_model(
                project_id=ids.project_a,
                profile_id=profile.profile_id,
                target_type=CalibrationTargetType.SENSOR,
                target_id=uuid4(),
                performed_by=ids.user_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_database_rejects_cross_project_target(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    with factory() as session:
        session.add(
            _record_model(
                project_id=ids.project_a,
                profile_id=profile.profile_id,
                target_type=CalibrationTargetType.SENSOR,
                target_id=ids.sensor_b,
                performed_by=ids.user_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_database_rejects_validated_checksum_tampering(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    validated = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == validated.calibration_id)
                .values(checksum="sha256:evil")
            )
        session.rollback()

    with factory() as session:
        row = session.scalar(
            select(CalibrationRecordModel).where(
                CalibrationRecordModel.id == validated.calibration_id
            )
        )
        assert row is not None
        assert row.checksum == "sha256:abc"
        assert row.status == CalibrationStatus.VALIDATED.value


@pytest.mark.parametrize("forged_status", ["DRAFT", "SUBMITTED", "REJECTED"])
def test_database_rejects_invalid_status_direct_write(
    device_db: tuple[sessionmaker[Session], DeviceIds],
    forged_status: str,
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    validated = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == validated.calibration_id)
                .values(status=forged_status)
            )
        session.rollback()

    with factory() as session:
        row = session.get(CalibrationRecordModel, validated.calibration_id)
        assert row is not None
        assert row.status == CalibrationStatus.VALIDATED.value


def test_database_allows_contract_state_transitions(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    validated = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        session.execute(
            update(CalibrationRecordModel)
            .where(CalibrationRecordModel.id == validated.calibration_id)
            .values(status=CalibrationStatus.ACTIVE.value)
        )
        session.commit()

    with factory() as session:
        session.execute(
            update(CalibrationRecordModel)
            .where(CalibrationRecordModel.id == validated.calibration_id)
            .values(status=CalibrationStatus.SUPERSEDED.value)
        )
        session.commit()

    with factory() as session:
        row = session.get(CalibrationRecordModel, validated.calibration_id)
        assert row is not None
        assert row.status == CalibrationStatus.SUPERSEDED.value

    # 终态不可回退。
    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == validated.calibration_id)
                .values(status=CalibrationStatus.ACTIVE.value)
            )
        session.rollback()
