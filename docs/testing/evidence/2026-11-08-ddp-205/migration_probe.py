"""DDP-205 独立迁移与数据库约束探针。

目的：在仓库现有测试之外，独立验证 0003 设备域迁移的三类行为：
1. 部分唯一索引（仅 status='ACTIVE' 生效）的谓词与运行期行为；
2. 复合外键（project_id, robot_id）跨项目写入是否被数据库拒绝；
3. 带数据降级（0003 -> 0002）是否成功、数据是否丢失。

脚本只在临时 SQLite 文件中操作，不连接任何真实数据库，不修改业务代码。
数据来源：合成数据（T2），只用于验证约束行为，不是业务验收数据。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain.devices.enums import (  # noqa: E402
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    DeviceType,
    LifecycleStatus,
)
from app.infrastructure.db.models.devices import (  # noqa: E402
    CalibrationProfile,
    CalibrationRecord,
    Device,
    Robot,
)
from app.infrastructure.db.models.identity import Project, User  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def make_config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def engine_for(db_path: Path) -> sa.Engine:
    engine = sa.create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def schema_sql(engine: sa.Engine, name: str) -> str:
    with engine.connect() as connection:
        value = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = ?", (name,)
        ).scalar()
    return str(value or "")


def seed_identity(factory: sessionmaker[Session], project_ids, user_ids) -> None:
    with factory() as session:
        session.add_all(
            [Project(id=pid, code=f"project-{i}", name=f"项目 {i}") for i, pid in enumerate(project_ids)]
        )
        session.add_all(
            [User(id=uid, display_name=f"用户 {i}") for i, uid in enumerate(user_ids)]
        )
        session.commit()


def seed_robot(
    factory: sessionmaker[Session],
    *,
    robot_id,
    project_id,
    user_id,
    code: str,
) -> None:
    with factory() as session:
        session.add(
            Robot(
                id=robot_id,
                project_id=project_id,
                code=code,
                name=code,
                robot_type="HUMANOID",
                status=LifecycleStatus.PROVISIONED.value,
                capability_profile={},
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()


def seed_device(
    factory: sessionmaker[Session],
    *,
    device_id,
    project_id,
    user_id,
    code: str,
    robot_id=None,
) -> None:
    with factory() as session:
        session.add(
            Device(
                id=device_id,
                project_id=project_id,
                code=code,
                name=code,
                device_type=DeviceType.COMPUTE.value,
                robot_id=robot_id,
                status=LifecycleStatus.PROVISIONED.value,
                capability_profile={},
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()


def seed_profile(
    factory: sessionmaker[Session],
    *,
    profile_id,
    project_id,
    user_id,
) -> None:
    with factory() as session:
        session.add(
            CalibrationProfile(
                id=profile_id,
                project_id=project_id,
                name="探针配置",
                scope=CalibrationScope.DEVICE.value,
                target_type=CalibrationTargetType.DEVICE.value,
                schema_version="ddp.device.calibration-parameters/1.0.0",
                required_types=[CalibrationType.INTRINSICS.value],
                selection_rule={},
                version=1,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()


def seed_record(
    factory: sessionmaker[Session],
    *,
    project_id,
    profile_id,
    target_id,
    performed_by,
    validator_id,
    status: CalibrationStatus,
) -> None:
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            CalibrationRecord(
                id=uuid4(),
                project_id=project_id,
                profile_id=profile_id,
                scope=CalibrationScope.DEVICE.value,
                target_type=CalibrationTargetType.DEVICE.value,
                target_id=target_id,
                calibration_type=CalibrationType.EXTRINSICS.value,
                method="probe/1.0",
                parameters={},
                valid_from=now,
                status=status.value,
                evidence_asset_ids=[],
                checksum="sha256:" + "0" * 64,
                uncertainty={},
                performed_by=performed_by,
                validated_by=validator_id,
                validated_at=now,
                created_by=performed_by,
                updated_by=performed_by,
            )
        )
        session.commit()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ddp205-migration-") as tmp:
        db_path = Path(tmp) / "probe.db"
        config = make_config(db_path)
        command.upgrade(config, "head")
        engine = engine_for(db_path)
        factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

        # 1. 部分唯一索引的谓词与列。
        index_sql = schema_sql(engine, "uq_calibration_records_active_target")
        expected_columns = (
            "project_id",
            "scope",
            "target_type",
            "target_id",
            "calibration_type",
        )
        columns_ok = all(column in index_sql for column in expected_columns)
        predicate_ok = "status = 'ACTIVE'" in index_sql.replace('"', "'")
        unique_ok = "UNIQUE" in index_sql.upper()
        record(
            "部分唯一索引定义",
            "PASS" if (columns_ok and predicate_ok and unique_ok) else "FAIL",
            f"UNIQUE={unique_ok}, columns_ok={columns_ok}, predicate_ok={predicate_ok}",
        )

        # 2. 复合外键元数据。
        inspector = sa.inspect(engine)
        device_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("devices")}
        robot_fk = device_fks.get("fk_devices_project_robot")
        composite_ok = (
            robot_fk is not None
            and tuple(robot_fk["constrained_columns"]) == ("project_id", "robot_id")
            and tuple(robot_fk["referred_columns"]) == ("project_id", "id")
        )
        record(
            "devices 复合外键元数据",
            "PASS" if composite_ok else "FAIL",
            f"found={robot_fk}",
        )

        project_a, project_b = uuid4(), uuid4()
        user_a, user_b = uuid4(), uuid4()
        seed_identity(factory, [project_a, project_b], [user_a, user_b])

        robot_b, device_b = uuid4(), uuid4()
        seed_robot(
            factory,
            robot_id=robot_b,
            project_id=project_b,
            user_id=user_b,
            code="robot-b",
        )
        seed_device(
            factory,
            device_id=device_b,
            project_id=project_b,
            user_id=user_b,
            code="device-b",
            robot_id=robot_b,
        )
        record("正向复合外键", "PASS", "项目 B 内 device -> robot 写入成功")

        try:
            seed_device(
                factory,
                device_id=uuid4(),
                project_id=project_a,
                user_id=user_a,
                code="device-a-cross",
                robot_id=robot_b,
            )
            record("跨项目复合外键", "FAIL", "跨项目 robot 引用被写入（应拒绝）")
        except IntegrityError:
            record("跨项目复合外键", "PASS", "数据库拒绝跨项目 robot 引用（IntegrityError）")

        # 3. 部分唯一索引运行期行为。
        profile_id = uuid4()
        seed_profile(factory, profile_id=profile_id, project_id=project_a, user_id=user_a)
        seed_record(
            factory,
            project_id=project_a,
            profile_id=profile_id,
            target_id=device_b,
            performed_by=user_a,
            validator_id=user_b,
            status=CalibrationStatus.ACTIVE,
        )
        record("ACTIVE 记录写入", "PASS", "首条 ACTIVE 写入成功")

        try:
            seed_record(
                factory,
                project_id=project_a,
                profile_id=profile_id,
                target_id=device_b,
                performed_by=user_a,
                validator_id=user_b,
                status=CalibrationStatus.ACTIVE,
            )
            record("重复 ACTIVE", "FAIL", "同目标第二条 ACTIVE 写入成功（应拒绝）")
        except IntegrityError:
            record("重复 ACTIVE", "PASS", "同目标第二条 ACTIVE 被部分唯一索引拒绝")

        seed_record(
            factory,
            project_id=project_a,
            profile_id=profile_id,
            target_id=device_b,
            performed_by=user_a,
            validator_id=user_b,
            status=CalibrationStatus.SUPERSEDED,
        )
        record("非 ACTIVE 同目标", "PASS", "SUPERSEDED 同目标可写入，部分索引未误伤")

        # 4. 带数据降级：设备域有数据时降级到 0002。
        with engine.connect() as connection:
            before = {
                table: connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar()
                for table in (
                    "calibration_profiles",
                    "calibration_records",
                    "robots",
                    "devices",
                )
            }
        engine.dispose()
        downgrade_error = ""
        try:
            command.downgrade(config, "0002")
            downgraded = True
        except Exception as exc:  # noqa: BLE001 - 探针需要记录失败原因
            downgraded = False
            downgrade_error = f"{type(exc).__name__}: {exc}"
        after_engine = engine_for(db_path)
        remaining = set(sa.inspect(after_engine).get_table_names())
        device_tables_gone = not (
            {"calibration_profiles", "calibration_records", "robots", "devices"} & remaining
        )
        identity_present = {"users", "projects", "project_members"} <= remaining
        record(
            "带数据降级 0003 -> 0002",
            "PASS" if (downgraded and device_tables_gone and identity_present) else "FAIL",
            f"downgraded={downgraded}, device_tables_gone={device_tables_gone}, "
            f"identity_tables_present={identity_present}, before_counts={before}, "
            f"error={downgrade_error or '无'}",
        )
        record(
            "降级数据保留语义",
            "WARN",
            "0003 降级直接删除设备域表，带数据降级会丢失标定/设备/传感器数据；"
            "生产回滚必须先备份并由人工批准",
        )
        after_engine.dispose()

    print("\n=== 汇总 ===")
    for name, status, _detail in RESULTS:
        print(f"{status}\t{name}")
    failures = [item for item in RESULTS if item[1] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
