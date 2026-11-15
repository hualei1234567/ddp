"""迁移 0004：目标投影表、SQLite 目标守卫与不可变触发器。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.domain.devices import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    LifecycleStatus,
)
from app.infrastructure.db.models.devices import CalibrationProfile, CalibrationTargetRef
from app.infrastructure.db.models.devices import CalibrationRecord as CalibrationRecordModel
from app.infrastructure.db.models.identity import Project, User
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TRIGGERS = {
    "trg_calibration_records_target_guard_insert",
    "trg_calibration_records_target_guard_update",
    "trg_calibration_records_immutable_update",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _trigger_names(db_path: Path) -> set[str]:
    engine = _engine(db_path)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _seed_project(
    session: Session,
    *,
    project_id: UUID,
    user_id: UUID,
) -> CalibrationProfile:
    session.add(Project(id=project_id, code=f"p-{project_id.hex[:8]}", name="项目"))
    session.add(User(id=user_id, display_name="用户"))
    session.flush()
    profile = CalibrationProfile(
        id=uuid4(),
        project_id=project_id,
        name="迁移测试配置",
        scope=CalibrationScope.SENSOR.value,
        target_type=CalibrationTargetType.SENSOR.value,
        schema_version="ddp.calibration/1.0.0",
        required_types=[CalibrationType.INTRINSICS.value],
        selection_rule={},
        version=1,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(profile)
    session.flush()
    return profile


def _add_ref(
    session: Session,
    *,
    project_id: UUID,
    target_type: CalibrationTargetType,
    target_id: UUID,
) -> None:
    session.add(
        CalibrationTargetRef(
            project_id=project_id,
            target_type=target_type.value,
            target_id=target_id,
            lifecycle_status=LifecycleStatus.PROVISIONED.value,
            is_retired=False,
        )
    )
    session.flush()


def _validated_record(
    *,
    project_id: UUID,
    profile_id: UUID,
    target_id: UUID,
    performed_by: UUID,
    validated_by: UUID,
) -> CalibrationRecordModel:
    return CalibrationRecordModel(
        id=uuid4(),
        project_id=project_id,
        profile_id=profile_id,
        scope=CalibrationScope.SENSOR.value,
        target_type=CalibrationTargetType.SENSOR.value,
        target_id=target_id,
        calibration_type=CalibrationType.INTRINSICS.value,
        method="migration-test",
        parameters={},
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        status=CalibrationStatus.VALIDATED.value,
        evidence_asset_ids=[str(uuid4())],
        checksum="sha256:" + "a" * 64,
        uncertainty={},
        performed_by=performed_by,
        validated_by=validated_by,
        validated_at=datetime(2026, 1, 2, tzinfo=UTC),
        created_by=performed_by,
        updated_by=performed_by,
    )


def _add_post_0003_columns(db_path: Path) -> None:
    """0003 之后、后续迁移新增的列。

    本测试用**当前 ORM 模型**往 0003 的 schema 插数据。ORM 会按最新表结构
    生成 INSERT，因此需要先把后续迁移新增的列补上，否则 INSERT 会因
    未知列失败。这里只补本测试用到的列，不是完整迁移。
    """
    engine = _engine(db_path)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE calibration_profiles ADD COLUMN target_id CHAR(32)"))
            conn.execute(
                text(
                    "ALTER TABLE calibration_records ADD COLUMN calibration_version INTEGER "
                    "NOT NULL DEFAULT 1"
                )
            )
            conn.execute(text("ALTER TABLE calibration_records ADD COLUMN profile_version INTEGER"))
            conn.execute(
                text(
                    "ALTER TABLE calibration_records ADD COLUMN profile_schema_version "
                    "VARCHAR(64)"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE calibration_records ADD COLUMN profile_snapshot JSON "
                    "NOT NULL DEFAULT '{}'"
                )
            )
    finally:
        engine.dispose()


def test_migration_fails_loudly_on_orphan_records(tmp_path: Path) -> None:
    """0003 上已存在无法解析的目标时，0004 必须失败而不是生成幽灵投影。"""
    db_path = tmp_path / "orphan.db"
    config = _config(db_path)
    command.upgrade(config, "0003")
    _add_post_0003_columns(db_path)

    project_id, performer, validator = uuid4(), uuid4(), uuid4()
    engine = _engine(db_path)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    try:
        with factory() as session:
            profile = _seed_project(session, project_id=project_id, user_id=performer)
            session.add(User(id=validator, display_name="校验人"))
            session.flush()
            session.add(
                _validated_record(
                    project_id=project_id,
                    profile_id=profile.id,
                    target_id=uuid4(),
                    performed_by=performer,
                    validated_by=validator,
                )
            )
            session.commit()
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="投影表"):
        command.upgrade(config, "head")


def test_migration_creates_projection_table_and_guards(tmp_path: Path) -> None:
    db_path = tmp_path / "guards.db"
    command.upgrade(_config(db_path), "head")

    engine = _engine(db_path)
    try:
        inspector = inspect(engine)
        assert "calibration_target_refs" in inspector.get_table_names()
        assert {"project_id", "target_type", "target_id"} <= {
            column["name"] for column in inspector.get_columns("calibration_target_refs")
        }
        assert TRIGGERS <= _trigger_names(db_path)
    finally:
        engine.dispose()

    command.downgrade(_config(db_path), "0003")
    engine = _engine(db_path)
    try:
        assert "calibration_target_refs" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert not TRIGGERS & _trigger_names(db_path)

    # 可重复升级，保证迁移可重入。
    command.upgrade(_config(db_path), "head")
    assert TRIGGERS <= _trigger_names(db_path)


def test_migration_rejects_unknown_and_cross_project_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "targets.db"
    command.upgrade(_config(db_path), "head")

    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    sensor_b = uuid4()
    engine = _engine(db_path)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    try:
        with factory() as session:
            profile = _seed_project(session, project_id=project_a, user_id=user_a)
            session.add(Project(id=project_b, code="project-b", name="项目 B"))
            session.add(User(id=user_b, display_name="用户 B"))
            session.flush()
            _add_ref(
                session,
                project_id=project_b,
                target_type=CalibrationTargetType.SENSOR,
                target_id=sensor_b,
            )
            session.commit()
            profile_id = profile.id

        with factory() as session:
            session.add(
                _validated_record(
                    project_id=project_a,
                    profile_id=profile_id,
                    target_id=uuid4(),
                    performed_by=user_a,
                    validated_by=user_b,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        with factory() as session:
            session.add(
                _validated_record(
                    project_id=project_a,
                    profile_id=profile_id,
                    target_id=sensor_b,
                    performed_by=user_a,
                    validated_by=user_b,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        # 正向对照：投影行存在时可写入。
        sensor_a = uuid4()
        with factory() as session:
            _add_ref(
                session,
                project_id=project_a,
                target_type=CalibrationTargetType.SENSOR,
                target_id=sensor_a,
            )
            record = _validated_record(
                project_id=project_a,
                profile_id=profile_id,
                target_id=sensor_a,
                performed_by=user_a,
                validated_by=user_b,
            )
            session.add(record)
            session.commit()
            record_id = record.id
        assert record_id is not None
    finally:
        engine.dispose()


def test_migration_blocks_validated_direct_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "immutable.db"
    command.upgrade(_config(db_path), "head")

    project_id, performer, validator, sensor_id = uuid4(), uuid4(), uuid4(), uuid4()
    engine = _engine(db_path)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    try:
        with factory() as session:
            profile = _seed_project(session, project_id=project_id, user_id=performer)
            session.add(User(id=validator, display_name="校验人"))
            session.flush()
            _add_ref(
                session,
                project_id=project_id,
                target_type=CalibrationTargetType.SENSOR,
                target_id=sensor_id,
            )
            record = _validated_record(
                project_id=project_id,
                profile_id=profile.id,
                target_id=sensor_id,
                performed_by=performer,
                validated_by=validator,
            )
            session.add(record)
            session.commit()
            record_id = record.id

        with factory() as session:
            with pytest.raises(IntegrityError):
                session.execute(
                    update(CalibrationRecordModel)
                    .where(CalibrationRecordModel.id == record_id)
                    .values(checksum="sha256:evil")
                )
            session.rollback()

        with factory() as session:
            with pytest.raises(IntegrityError):
                session.execute(
                    update(CalibrationRecordModel)
                    .where(CalibrationRecordModel.id == record_id)
                    .values(status=CalibrationStatus.DRAFT.value)
                )
            session.rollback()

        # 合法迁移仍可执行：VALIDATED → ACTIVE → SUPERSEDED。
        with factory() as session:
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == record_id)
                .values(status=CalibrationStatus.ACTIVE.value)
            )
            session.commit()

        with factory() as session:
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == record_id)
                .values(status=CalibrationStatus.SUPERSEDED.value)
            )
            session.commit()

        with factory() as session:
            with pytest.raises(IntegrityError):
                session.execute(
                    update(CalibrationRecordModel)
                    .where(CalibrationRecordModel.id == record_id)
                    .values(status=CalibrationStatus.ACTIVE.value)
                )
            session.rollback()

        with factory() as session:
            row = session.get(CalibrationRecordModel, record_id)
            assert row is not None
            assert row.checksum == "sha256:" + "a" * 64
            assert row.status == CalibrationStatus.SUPERSEDED.value
    finally:
        engine.dispose()
