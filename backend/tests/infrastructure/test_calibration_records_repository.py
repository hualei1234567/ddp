"""标定仓储安全测试：目标项目约束、溯源不可变、状态命令与激活原子性。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.devices import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    DomainValidationError,
    ResourceImmutableError,
    ResourceNotFoundError,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import Sensor as SensorModel
from app.infrastructure.repositories.devices import (
    SqlCalibrationProfileRepository,
    SqlCalibrationRecordRepository,
)
from sqlalchemy.orm import Session, sessionmaker

from tests.calibration_fixtures import parameters_for
from tests.infrastructure.calibration_support import (
    NOW,
    VALIDATED_AT,
    add_profile,
    draft_record,
    seed_active,
    seed_submitted,
    seed_validated,
)
from tests.infrastructure.calibration_support import (
    profile as make_profile,
)
from tests.infrastructure.conftest import DeviceIds


def test_calibration_profile_accepts_config_version_and_is_project_scoped(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)
    profile = make_profile(ids.project_a, profile_version=2)
    add_profile(factory, scope_a, profile)

    with factory() as session:
        repository = SqlCalibrationProfileRepository(session)
        stored = repository.get(scope_a, profile.profile_id)
        assert stored is not None
        assert stored.profile_version == 2
        assert repository.get(scope_b, profile.profile_id) is None


def test_add_rejects_cross_project_target(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    record = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_b,
        performer=ids.user_a,
    )
    with factory() as session:
        with pytest.raises(ResourceNotFoundError, match="标定目标"):
            SqlCalibrationRecordRepository(session).add(scope_a, record)
        session.rollback()


def test_add_rejects_nonexistent_target(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    record = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=uuid4(),
        performer=ids.user_a,
    )
    with factory() as session:
        with pytest.raises(ResourceNotFoundError, match="标定目标"):
            SqlCalibrationRecordRepository(session).add(scope_a, record)
        session.rollback()


def test_add_rejects_cross_project_profile(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)
    foreign_profile = make_profile(ids.project_b)
    add_profile(factory, scope_b, foreign_profile)

    record = draft_record(
        ids.project_a,
        foreign_profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )
    with factory() as session:
        with pytest.raises(ResourceNotFoundError, match="标定配置"):
            SqlCalibrationRecordRepository(session).add(scope_a, record)
        session.rollback()


def test_add_rejects_profile_target_type_mismatch(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    record = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.robot_a,
        performer=ids.user_a,
        scope=CalibrationScope.ROBOT,
        target_type=CalibrationTargetType.ROBOT,
    )
    with factory() as session:
        with pytest.raises(DomainValidationError, match="target_type"):
            SqlCalibrationRecordRepository(session).add(scope_a, record)
        session.rollback()


def test_system_target_must_equal_project_id(
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

    invalid = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=uuid4(),
        performer=ids.user_a,
        scope=CalibrationScope.SYSTEM,
        target_type=CalibrationTargetType.SYSTEM,
    )
    with factory() as session:
        with pytest.raises(DomainValidationError, match="target_id"):
            SqlCalibrationRecordRepository(session).add(scope_a, invalid)
        session.rollback()

    valid = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.project_a,
        performer=ids.user_a,
        scope=CalibrationScope.SYSTEM,
        target_type=CalibrationTargetType.SYSTEM,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(scope_a, valid)
        session.commit()


def test_add_only_accepts_draft(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    draft = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )
    cases = (
        replace(draft, status=CalibrationStatus.SUBMITTED),
        replace(draft, supersedes_id=uuid4()),
        replace(draft, validated_by=ids.user_b, validated_at=VALIDATED_AT),
    )
    for record in cases:
        with factory() as session:
            with pytest.raises(DomainValidationError):
                SqlCalibrationRecordRepository(session).add(scope_a, record)
            session.rollback()


def test_add_requires_performer_to_be_actor(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)

    record = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_b,
    )
    with factory() as session:
        with pytest.raises(DomainValidationError, match="performed_by"):
            SqlCalibrationRecordRepository(session).add(scope_a, record)
        session.rollback()


def test_save_cannot_change_status(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    draft = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(scope_a, draft)
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, draft.calibration_id)
        assert stored is not None
        forged = replace(stored, status=CalibrationStatus.SUBMITTED)
        with pytest.raises(ResourceImmutableError, match="状态"):
            repository.save(scope_a, forged, expected_version=stored.version)
        session.rollback()


def test_draft_save_updates_content_without_writing_status(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    draft = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(scope_a, draft)
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, draft.calibration_id)
        assert stored is not None
        updated = repository.save(
            scope_a,
            replace(stored, parameters=_changed_parameters()),
            expected_version=stored.version,
        )
        session.commit()

    assert updated.status is CalibrationStatus.DRAFT
    assert updated.version == 2

    with factory() as session:
        stored = SqlCalibrationRecordRepository(session).get(
            scope_a,
            draft.calibration_id,
        )
        assert stored is not None
        assert stored.status is CalibrationStatus.DRAFT
        assert dict(stored.parameters) == _changed_parameters()
        assert stored.checksum == "sha256:abc"


def test_state_command_only_updates_status_and_audit_fields(
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
        rejected = repository.reject(
            scope_a,
            stored,
            expected_version=stored.version,
        )
        session.commit()

    assert rejected.status is CalibrationStatus.REJECTED
    assert rejected.version == submitted.version + 1
    assert rejected.checksum == submitted.checksum
    assert rejected.parameters == submitted.parameters
    assert rejected.target_id == submitted.target_id
    assert rejected.evidence_asset_ids == submitted.evidence_asset_ids
    assert rejected.performed_by == submitted.performed_by


def test_save_rejects_trace_tampering_after_submit(
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

    tampered = (
        replace(submitted, checksum="sha256:evil"),
        replace(submitted, parameters=_changed_parameters()),
        replace(submitted, target_id=uuid4()),
        replace(submitted, evidence_asset_ids=(uuid4(),)),
    )
    for record in tampered:
        with factory() as session:
            repository = SqlCalibrationRecordRepository(session)
            stored = repository.get(scope_a, submitted.calibration_id)
            assert stored is not None
            with pytest.raises(ResourceImmutableError, match="溯源"):
                repository.save(
                    scope_a,
                    replace(
                        stored,
                        **{
                            key: getattr(record, key)
                            for key in (
                                "checksum",
                                "parameters",
                                "target_id",
                                "evidence_asset_ids",
                            )
                        },
                    ),
                    expected_version=stored.version,
                )
            session.rollback()

    with factory() as session:
        stored = SqlCalibrationRecordRepository(session).get(
            scope_a,
            submitted.calibration_id,
        )
        assert stored is not None
        assert stored.checksum == "sha256:abc"
        assert stored.target_id == ids.sensor_a
        assert stored.status is CalibrationStatus.SUBMITTED


def test_save_rejects_trace_tampering_after_validate(
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
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, validated.calibration_id)
        assert stored is not None
        with pytest.raises(ResourceImmutableError, match="溯源"):
            repository.save(
                scope_a,
                replace(stored, checksum="sha256:evil"),
                expected_version=stored.version,
            )
        session.rollback()

    with factory() as session:
        stored = SqlCalibrationRecordRepository(session).get(
            scope_a,
            validated.calibration_id,
        )
        assert stored is not None
        assert stored.checksum == "sha256:abc"
        assert stored.status is CalibrationStatus.VALIDATED


def test_save_allows_valid_to_after_validate(
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
    new_valid_to = datetime(2027, 1, 1, tzinfo=UTC)

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, validated.calibration_id)
        assert stored is not None
        updated = repository.save(
            scope_a,
            replace(stored, valid_to=new_valid_to),
            expected_version=stored.version,
        )
        session.commit()

    assert updated.status is CalibrationStatus.VALIDATED
    assert updated.valid_to == new_valid_to
    assert updated.version == validated.version + 1

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, validated.calibration_id)
        assert stored is not None
        active = repository.activate(
            scope_a,
            stored,
            expected_version=stored.version,
            now=NOW,
        )
        session.commit()
    assert active.status is CalibrationStatus.ACTIVE


def test_save_cannot_forge_performer_in_draft(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    draft = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(scope_a, draft)
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, draft.calibration_id)
        assert stored is not None
        with pytest.raises(ResourceImmutableError, match="performed_by"):
            repository.save(
                scope_a,
                replace(stored, performed_by=ids.user_viewer),
                expected_version=stored.version,
            )
        session.rollback()


def test_submit_rejects_target_removed_after_draft(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    draft = draft_record(
        ids.project_a,
        profile.profile_id,
        target_id=ids.sensor_a,
        performer=ids.user_a,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(scope_a, draft)
        session.commit()

    # 模拟目标在草稿阶段被删除：提交前必须重新校验目标仍属于本项目。
    with factory() as session:
        target = session.get(SensorModel, ids.sensor_a)
        assert target is not None
        session.delete(target)
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, draft.calibration_id)
        assert stored is not None
        with pytest.raises(ResourceNotFoundError, match="标定目标"):
            repository.submit(scope_a, stored, expected_version=stored.version)
        session.rollback()


def test_validate_rejects_non_member_and_viewer(
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

    for validator in (uuid4(), ids.user_viewer):
        with factory() as session:
            repository = SqlCalibrationRecordRepository(session)
            stored = repository.get(scope_a, submitted.calibration_id)
            assert stored is not None
            with pytest.raises(DomainValidationError, match="validated_by"):
                repository.validate(
                    scope_a,
                    stored,
                    validator,
                    VALIDATED_AT,
                    expected_version=stored.version,
                )
            session.rollback()


def test_editor_can_validate_another_performers_record(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    performer_scope = ProjectScope(ids.project_a, actor_id=ids.user_b)
    profile = make_profile(ids.project_a)
    add_profile(factory, performer_scope, profile)
    validated = seed_validated(
        factory,
        performer_scope,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_b,
        validator=ids.user_a,
    )

    assert validated.status is CalibrationStatus.VALIDATED
    assert validated.validated_by == ids.user_a
    assert validated.validated_at == VALIDATED_AT


def test_activate_ignores_tampered_trace_fields(
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
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, validated.calibration_id)
        assert stored is not None
        tampered = replace(
            stored,
            checksum="sha256:evil",
            target_id=uuid4(),
            evidence_asset_ids=(uuid4(),),
        )
        activated = repository.activate(
            scope_a,
            tampered,
            expected_version=stored.version,
            now=NOW,
        )
        session.commit()

    assert activated.status is CalibrationStatus.ACTIVE
    assert activated.checksum == "sha256:abc"
    assert activated.target_id == ids.sensor_a
    assert activated.evidence_asset_ids == validated.evidence_asset_ids

    with factory() as session:
        persisted = SqlCalibrationRecordRepository(session).get(
            scope_a,
            validated.calibration_id,
        )
        assert persisted is not None
        assert persisted.status is CalibrationStatus.ACTIVE
        assert persisted.checksum == "sha256:abc"
        assert persisted.target_id == ids.sensor_a


def test_activation_supersedes_and_writes_supersedes_id(
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
    assert first.status is CalibrationStatus.ACTIVE
    assert first.version == 4

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
        activated = repository.activate(
            scope_a,
            stored,
            expected_version=stored.version,
            now=NOW,
        )
        session.commit()

    assert activated.status is CalibrationStatus.ACTIVE
    assert activated.supersedes_id == first.calibration_id

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        current = repository.get(scope_a, second.calibration_id)
        assert old is not None
        assert current is not None
        assert old.status is CalibrationStatus.SUPERSEDED
        assert old.version == 5
        assert current.status is CalibrationStatus.ACTIVE
        assert current.supersedes_id == first.calibration_id


def test_activation_is_project_scoped(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)
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
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_b, validated.calibration_id)
        assert stored is None
        with pytest.raises(ResourceNotFoundError):
            repository.activate(
                scope_b,
                replace(validated, project_id=ids.project_b),
                expected_version=validated.version,
                now=NOW,
            )
        session.rollback()


def test_activation_rejects_expired_record_without_touching_active(
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
    expired = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2026, 2, 1, tzinfo=UTC),
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, expired.calibration_id)
        assert stored is not None
        with pytest.raises(DomainValidationError, match="过期"):
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
        pending = repository.get(scope_a, expired.calibration_id)
        assert old is not None
        assert pending is not None
        assert old.status is CalibrationStatus.ACTIVE
        assert pending.status is CalibrationStatus.VALIDATED


def _valid_parameters() -> dict[str, object]:
    """G-19 之后参数形状由契约 schema 强制；测试必须用合法参数。"""
    return parameters_for("INTRINSICS")


def _changed_parameters() -> dict[str, object]:
    """与 ``_valid_parameters()`` 内容确实不同，用于验证"实质变更"路径。"""
    changed = _valid_parameters()
    changed["fx"] = 1234.5
    return changed
