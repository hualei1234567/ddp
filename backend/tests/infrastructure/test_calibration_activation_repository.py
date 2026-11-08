"""标定激活安全测试：有效期、版本、事务回滚、并发冲突与跨项目隔离。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.devices import (
    CalibrationRecord,
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import CalibrationRecord as CalibrationRecordModel
from app.infrastructure.repositories.devices import SqlCalibrationRecordRepository
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.calibration_support import (
    NOW,
    VALID_FROM,
    VALIDATED_AT,
    add_profile,
    seed_active,
    seed_submitted,
    seed_validated,
)
from tests.infrastructure.calibration_support import (
    profile as make_profile,
)
from tests.infrastructure.conftest import DeviceIds


def test_activation_rejects_future_valid_from(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    future = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
        valid_from=datetime(2026, 12, 1, tzinfo=UTC),
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, future.calibration_id)
        assert stored is not None
        with pytest.raises(DomainValidationError, match="尚未生效"):
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version,
                now=NOW,
            )
        session.rollback()


def test_activation_rejects_non_validated_state(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    submitted = seed_submitted(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, submitted.calibration_id)
        assert stored is not None
        with pytest.raises(InvalidStateTransitionError):
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version,
                now=NOW,
            )
        session.rollback()


def test_activation_rejects_stale_version(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    first = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        with pytest.raises(VersionConflictError):
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version - 1,
                now=NOW,
            )
        session.rollback()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        pending = repository.get(scope_a, second.calibration_id)
        assert old is not None
        assert pending is not None
        assert old.status is CalibrationStatus.ACTIVE
        assert pending.status is CalibrationStatus.VALIDATED


def test_activation_failure_rolls_back_supersede(
    device_db: tuple[sessionmaker[Session], DeviceIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    first = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    def _fail_after_supersede(
        self: SqlCalibrationRecordRepository,
        scope: ProjectScope,
        activated: CalibrationRecord,
        old_active_id: UUID | None,
        expected_version: int,
    ) -> None:
        raise VersionConflictError("标定", expected_version)

    monkeypatch.setattr(
        SqlCalibrationRecordRepository,
        "_activate_current",
        _fail_after_supersede,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        with pytest.raises(VersionConflictError):
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version,
                now=NOW,
            )
        session.rollback()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        pending = repository.get(scope_a, second.calibration_id)
        assert old is not None
        assert pending is not None
        assert old.status is CalibrationStatus.ACTIVE
        assert old.version == first.version
        assert pending.status is CalibrationStatus.VALIDATED


def test_activation_maps_unique_conflict_to_duplicate_resource(
    device_db: tuple[sessionmaker[Session], DeviceIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    first = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    def _raise_unique_conflict(
        self: SqlCalibrationRecordRepository,
        scope: ProjectScope,
        activated: CalibrationRecord,
        old_active_id: UUID | None,
        expected_version: int,
    ) -> None:
        raise IntegrityError(
            "UPDATE calibration_records",
            {},
            Exception("UNIQUE constraint failed: uq_calibration_records_active_target"),
        )

    monkeypatch.setattr(
        SqlCalibrationRecordRepository,
        "_activate_current",
        _raise_unique_conflict,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        with pytest.raises(DuplicateResourceError):
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version,
                now=NOW,
            )
        session.rollback()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        pending = repository.get(scope_a, second.calibration_id)
        assert old is not None
        assert pending is not None
        assert old.status is CalibrationStatus.ACTIVE
        assert pending.status is CalibrationStatus.VALIDATED


def test_concurrent_stale_activation_gets_version_conflict(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    first = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    # 会话 A 先读取 VALIDATED 快照（模拟并发请求的旧视图）。
    with factory() as session:
        stale = SqlCalibrationRecordRepository(session).get(scope_a, second.calibration_id)
        assert stale is not None
        stale_version = stale.version

    # 会话 B 抢先完成激活。
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        repository.activate(scope_a, stored, expected_version=stored.version, now=NOW)
        session.commit()

    # 会话 A 用旧版本提交，必须得到版本冲突而不是重复激活。
    with factory() as session:
        with pytest.raises(VersionConflictError):
            SqlCalibrationRecordRepository(session).activate(
                scope_a,
                stale,
                expected_version=stale_version,
                now=NOW,
            )
        session.rollback()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        current = repository.get(scope_a, second.calibration_id)
        assert old is not None
        assert current is not None
        assert old.status is CalibrationStatus.SUPERSEDED
        assert current.status is CalibrationStatus.ACTIVE
        assert current.supersedes_id == first.calibration_id


def test_activation_does_not_supersede_other_project(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)
    profile_a = make_profile(ids.project_a)
    profile_b = make_profile(ids.project_b)
    add_profile(factory, scope_a, profile_a)
    add_profile(factory, scope_b, profile_b)

    foreign_active = seed_active(
        factory,
        scope_b,
        profile_b,
        target_id=ids.sensor_b,
        performer=ids.user_b,
        validator=ids.user_a,
    )
    first = seed_active(
        factory,
        scope_a,
        profile_a,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile_a,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        repository.activate(scope_a, stored, expected_version=stored.version, now=NOW)
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        foreign = repository.get(scope_b, foreign_active.calibration_id)
        old = repository.get(scope_a, first.calibration_id)
        assert foreign is not None
        assert old is not None
        assert foreign.status is CalibrationStatus.ACTIVE
        assert foreign.version == foreign_active.version
        assert old.status is CalibrationStatus.SUPERSEDED


def test_database_allows_only_one_active_for_same_target(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        session.add(
            CalibrationRecordModel(
                id=uuid4(),
                project_id=ids.project_a,
                profile_id=profile.profile_id,
                scope=CalibrationScope.SENSOR.value,
                target_type=CalibrationTargetType.SENSOR.value,
                target_id=ids.sensor_a,
                calibration_type=CalibrationType.INTRINSICS.value,
                method="direct-insert",
                parameters={},
                valid_from=VALID_FROM,
                status=CalibrationStatus.ACTIVE.value,
                evidence_asset_ids=[str(uuid4())],
                checksum="sha256:direct",
                uncertainty={},
                performed_by=ids.user_a,
                validated_by=ids.user_b,
                validated_at=VALIDATED_AT,
                created_by=ids.user_a,
                updated_by=ids.user_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
