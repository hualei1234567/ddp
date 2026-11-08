"""DDP-216 复验探针：数据库兜底（target_id 完整性 + 已校验标定不可变）。

相对 DDP-205 的旧探针：
- 迁移 head 已从 0003 前进到 0005（0004 投影表/触发器 + 0005 PG JSON 比较修复）；
- 旧探针第 3 项使用跨项目 target 插入 VALIDATED 记录，在 0004 后会被目标守卫
  提前拒绝，无法走到“不可变”断言；本版改用项目内合法目标；
- 支持 ``--backend-dir``，以便在 QA 工作树内运行 main 的后端代码，不修改业务代码。

数据来源：T2 合成数据；临时 SQLite；不连接真实数据库。
"""

from __future__ import annotations

import argparse
import os
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

DEFAULT_BACKEND_DIR = Path(
    "/home/user/vscodeworkspace/DataPlatform/backend"
)

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def make_config(backend_dir: Path, db_path: Path) -> Config:
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("prepend_sys_path", str(backend_dir))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-dir",
        default=str(DEFAULT_BACKEND_DIR),
        help="后端代码目录（含 alembic.ini 与 app/）",
    )
    args = parser.parse_args(argv)
    backend_dir = Path(args.backend_dir).resolve()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.domain.devices.enums import (
        CalibrationScope,
        CalibrationStatus,
        CalibrationTargetType,
        CalibrationType,
        DeviceType,
        LifecycleStatus,
    )
    from app.infrastructure.db.models.devices import (
        CalibrationProfile,
        CalibrationRecord,
        CalibrationTargetRef,
        Device,
    )
    from app.infrastructure.db.models.identity import Project, User

    with tempfile.TemporaryDirectory(prefix="ddp216-dbguard-") as tmp:
        db_path = Path(tmp) / "probe.db"
        command.upgrade(make_config(backend_dir, db_path), "head")
        engine = engine_for(db_path)
        factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        now = datetime.now(UTC)

        project_a, project_b = uuid4(), uuid4()
        user_a, user_b = uuid4(), uuid4()
        sensor_a, sensor_b = uuid4(), uuid4()
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
            # 项目 A 的合法目标；项目 B 的跨项目目标也写入投影表，
            # 以证明拒绝来自“项目不匹配”而不只是“目标不存在”。
            session.add_all(
                [
                    CalibrationTargetRef(
                        project_id=project_a,
                        target_type=CalibrationTargetType.SENSOR.value,
                        target_id=sensor_a,
                        lifecycle_status=LifecycleStatus.PROVISIONED.value,
                        is_retired=False,
                    ),
                    CalibrationTargetRef(
                        project_id=project_b,
                        target_type=CalibrationTargetType.SENSOR.value,
                        target_id=sensor_b,
                        lifecycle_status=LifecycleStatus.PROVISIONED.value,
                        is_retired=False,
                    ),
                ]
            )
            session.commit()

        def add_record(
            target_id,
            status: CalibrationStatus = CalibrationStatus.DRAFT,
        ) -> None:
            with factory() as session:
                session.add(
                    CalibrationRecord(
                        id=uuid4(),
                        project_id=project_a,
                        profile_id=profile_a,
                        scope=CalibrationScope.SENSOR.value,
                        target_type=CalibrationTargetType.SENSOR.value,
                        target_id=target_id,
                        calibration_type=CalibrationType.INTRINSICS.value,
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
            add_record(uuid4())
            record(
                "数据库拒绝不存在 target_id",
                "FAIL",
                "直接 ORM 写入不存在目标成功",
            )
        except IntegrityError:
            record("数据库拒绝不存在 target_id", "PASS", "IntegrityError")

        # 2. 跨项目 target_id（投影行存在，但属于项目 B）。
        try:
            add_record(sensor_b)
            record(
                "数据库拒绝跨项目 target_id",
                "FAIL",
                "项目 A 记录引用项目 B 目标成功",
            )
        except IntegrityError:
            record("数据库拒绝跨项目 target_id", "PASS", "IntegrityError")

        # 3. 已校验标定的 checksum 不可直写。
        validated_id = uuid4()
        with factory() as session:
            session.add(
                CalibrationRecord(
                    id=validated_id,
                    project_id=project_a,
                    profile_id=profile_a,
                    scope=CalibrationScope.SENSOR.value,
                    target_type=CalibrationTargetType.SENSOR.value,
                    target_id=sensor_a,
                    calibration_type=CalibrationType.INTRINSICS.value,
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
        stored_id = validated_id.hex

        try:
            with factory() as session:
                session.execute(
                    sa.text(
                        "UPDATE calibration_records SET checksum = :checksum "
                        "WHERE id = :id"
                    ),
                    {"checksum": "sha256:evil", "id": stored_id},
                )
                session.commit()
            record(
                "数据库拒绝篡改已校验 checksum",
                "FAIL",
                "VALIDATED 记录 checksum 被直接 SQL 改写",
            )
        except IntegrityError:
            record(
                "数据库拒绝篡改已校验 checksum",
                "PASS",
                "IntegrityError（calibration_record_immutable）",
            )

        # 4. 已校验标定的非法状态回退不可直写。
        try:
            with factory() as session:
                session.execute(
                    sa.text(
                        "UPDATE calibration_records SET status = 'DRAFT' "
                        "WHERE id = :id"
                    ),
                    {"id": stored_id},
                )
                session.commit()
            record(
                "数据库拒绝已校验状态非法回退",
                "FAIL",
                "VALIDATED 记录 status 被直接改为 DRAFT",
            )
        except IntegrityError:
            record(
                "数据库拒绝已校验状态非法回退",
                "PASS",
                "IntegrityError（calibration_record_state_transition_invalid）",
            )

        # 5. 对照：合法 VALIDATED -> ACTIVE 仍可写。
        try:
            with factory() as session:
                session.execute(
                    sa.text(
                        "UPDATE calibration_records SET status = 'ACTIVE' "
                        "WHERE id = :id"
                    ),
                    {"id": stored_id},
                )
                session.commit()
            row = None
            with factory() as session:
                row = session.execute(
                    sa.text(
                        "SELECT checksum, status FROM calibration_records "
                        "WHERE id = :id"
                    ),
                    {"id": stored_id},
                ).one()
            if row.checksum == "sha256:" + "0" * 64 and row.status == "ACTIVE":
                record(
                    "合法状态迁移未被误伤",
                    "PASS",
                    "VALIDATED -> ACTIVE 允许，checksum 保持不变",
                )
            else:
                record(
                    "合法状态迁移未被误伤",
                    "FAIL",
                    f"row={tuple(row)}",
                )
        except IntegrityError as exc:
            record("合法状态迁移未被误伤", "FAIL", f"合法迁移被拒绝：{exc}")

        engine.dispose()

    print("\n=== 汇总 ===")
    for name, status, _detail in RESULTS:
        print(f"{status}\t{name}")
    failures = [item for item in RESULTS if item[1] == "FAIL"]
    postgres_configured = bool(
        os.environ.get("DDP_TEST_POSTGRES_URL")
        or os.environ.get("DDP_DATABASE_URL")
    )
    print(f"\nPostgreSQL 测试库已配置：{'是' if postgres_configured else '否（BLOCKED）'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
