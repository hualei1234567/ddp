"""DDP-205 数据库兜底能力探针：target_id 完整性与已校验标定不可变。

背景：
- 契约冻结 v1 §10 选择「calibration_target_refs 投影表 + 复合外键」作为 target_id
  完整性的唯一权威防线；
- 契约冻结 v1 §8.4 要求 DB 触发器拦截非法状态直写；
- 当前 0003 迁移未包含上述表/触发器。本探针用直接 ORM/SQL 写入验证缺口是否真实存在。

数据来源：T2 合成数据；临时 SQLite；不连接真实数据库。
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ddp205-dbguard-") as tmp:
        db_path = Path(tmp) / "probe.db"
        command.upgrade(make_config(db_path), "head")
        engine = engine_for(db_path)
        factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        now = datetime.now(UTC)

        project_a, project_b = uuid4(), uuid4()
        user_a, user_b = uuid4(), uuid4()
        device_b = uuid4()
        profile_a = uuid4()
        with factory() as session:
            session.add_all(
                [
                    Project(id=project_a, code="db-a", name="项目 A"),
                    Project(id=project_b, code="db-b", name="项目 B"),
                    User(id=user_a, display_name="用户 A"),
                    User(id=user_b, display_name="用户 B"),
                ]
            )
            session.flush()
            session.add(
                Device(
                    id=device_b,
                    project_id=project_b,
                    code="device-b",
                    name="项目 B 设备",
                    device_type=DeviceType.COMPUTE.value,
                    status=LifecycleStatus.PROVISIONED.value,
                    capability_profile={},
                    created_by=user_b,
                    updated_by=user_b,
                )
            )
            session.add(
                CalibrationProfile(
                    id=profile_a,
                    project_id=project_a,
                    name="探针配置",
                    scope=CalibrationScope.DEVICE.value,
                    target_type=CalibrationTargetType.DEVICE.value,
                    schema_version="ddp.device.calibration-parameters/1.0.0",
                    required_types=[CalibrationType.INTRINSICS.value],
                    selection_rule={},
                    version=1,
                    created_by=user_a,
                    updated_by=user_a,
                )
            )
            session.commit()

        def add_record(target_id, status: CalibrationStatus) -> None:
            with factory() as session:
                session.add(
                    CalibrationRecord(
                        id=uuid4(),
                        project_id=project_a,
                        profile_id=profile_a,
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
                        performed_by=user_a,
                        validated_by=user_b,
                        validated_at=now,
                        created_by=user_a,
                        updated_by=user_a,
                    )
                )
                session.commit()

        # 1. 不存在的 target_id。
        try:
            add_record(uuid4(), CalibrationStatus.DRAFT)
            record(
                "数据库拒绝不存在 target_id",
                "FAIL",
                "0003 无 target_id 外键/投影表，直接写入不存在目标成功",
            )
        except IntegrityError:
            record("数据库拒绝不存在 target_id", "PASS", "IntegrityError")

        # 2. 跨项目 target_id。
        try:
            add_record(device_b, CalibrationStatus.DRAFT)
            record(
                "数据库拒绝跨项目 target_id",
                "FAIL",
                "0003 无 target_id 复合外键，项目 A 记录引用项目 B 设备成功",
            )
        except IntegrityError:
            record("数据库拒绝跨项目 target_id", "PASS", "IntegrityError")

        # 3. 已校验记录的 checksum/status 是否可被直接 SQL 篡改。
        tampered_id = uuid4()
        with factory() as session:
            session.add(
                CalibrationRecord(
                    id=tampered_id,
                    project_id=project_a,
                    profile_id=profile_a,
                    scope=CalibrationScope.DEVICE.value,
                    target_type=CalibrationTargetType.DEVICE.value,
                    target_id=device_b,
                    calibration_type=CalibrationType.EXTRINSICS.value,
                    method="probe/1.0",
                    parameters={},
                    valid_from=now,
                    status=CalibrationStatus.VALIDATED.value,
                    evidence_asset_ids=[],
                    checksum="sha256:" + "0" * 64,
                    uncertainty={},
                    performed_by=user_a,
                    validated_by=user_b,
                    validated_at=now,
                    created_by=user_a,
                    updated_by=user_a,
                )
            )
            session.commit()
        # SQLAlchemy 的 Uuid 在 SQLite 中存储为 32 位十六进制字符串。
        stored_id = tampered_id.hex
        with factory() as session:
            session.execute(
                sa.text(
                    "UPDATE calibration_records SET checksum = :checksum, "
                    "status = 'ACTIVE' WHERE id = :id"
                ),
                {"checksum": "sha256:evil", "id": stored_id},
            )
            session.commit()
        with factory() as session:
            row = session.execute(
                sa.text(
                    "SELECT checksum, status FROM calibration_records WHERE id = :id"
                ),
                {"id": stored_id},
            ).one()
        if row.checksum == "sha256:evil" and row.status == "ACTIVE":
            record(
                "数据库保护已校验标定不可变",
                "FAIL",
                "0003 无触发器：VALIDATED 记录的 checksum 与 status 可被直接 SQL 改写",
            )
        else:
            record(
                "数据库保护已校验标定不可变",
                "PASS",
                f"direct_update_blocked, row={tuple(row)}",
            )

        engine.dispose()

    print("\n=== 汇总 ===")
    for name, status, _detail in RESULTS:
        print(f"{status}\t{name}")
    failures = [item for item in RESULTS if item[1] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
