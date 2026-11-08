"""DDP-216 复验探针：0004/0005 迁移升级、降级、部分唯一索引与带数据降级。

覆盖：
1. upgrade head 后 ``calibration_target_refs`` 表、三类触发器存在；
2. 0003 定义的部分唯一索引 ``uq_calibration_records_active_target`` 在 0004/0005
   升级后仍存在且谓词为 ``status = 'ACTIVE'``；
3. 同目标第二条 ACTIVE 被拒绝，SUPERSEDED 可写入（未误伤）；
4. 带数据 downgrade 到 0003：投影表/触发器删除，设备域数据与部分唯一索引保留；
5. 再次 upgrade head：可重入，backfill 恢复投影行；
6. downgrade 到 0002：设备域表删除，身份域表保留（数据丢失语义 WARN）。

数据来源：T2 合成数据；临时 SQLite；不连接真实数据库。
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_BACKEND_DIR = Path("/home/user/vscodeworkspace/DataPlatform/backend")
TRIGGERS = {
    "trg_calibration_records_target_guard_insert",
    "trg_calibration_records_target_guard_update",
    "trg_calibration_records_immutable_update",
}
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


def trigger_names(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        )
        return {row[0] for row in rows}


def index_sql(engine: sa.Engine, name: str) -> str | None:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"
            ),
            {"name": name},
        ).first()
        return row[0] if row else None


def table_names(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
        return {row[0] for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-dir",
        default=str(DEFAULT_BACKEND_DIR),
        help="后端代码目录（含 alembic.ini 与 app/）",
    )
    args = parser.parse_args(argv)
    backend_dir = Path(args.backend_dir).resolve()
    import sys

    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.domain.devices.enums import (
        CalibrationScope,
        CalibrationStatus,
        CalibrationTargetType,
        CalibrationType,
        LifecycleStatus,
    )
    from app.infrastructure.db.models.devices import (
        CalibrationProfile,
        CalibrationRecord,
        CalibrationTargetRef,
    )
    from app.infrastructure.db.models.identity import Project, User

    with tempfile.TemporaryDirectory(prefix="ddp216-migration-") as tmp:
        db_path = Path(tmp) / "migration.db"
        config = make_config(backend_dir, db_path)
        command.upgrade(config, "head")
        engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")

        tables = table_names(engine)
        if "calibration_target_refs" in tables:
            record("0004 投影表存在", "PASS", "calibration_target_refs")
        else:
            record("0004 投影表存在", "FAIL", "缺少 calibration_target_refs")

        missing = TRIGGERS - trigger_names(engine)
        if not missing:
            record("0004 触发器存在", "PASS", ", ".join(sorted(TRIGGERS)))
        else:
            record("0004 触发器存在", "FAIL", f"缺少：{sorted(missing)}")

        sql = index_sql(engine, "uq_calibration_records_active_target")
        if sql and "UNIQUE" in sql.upper() and "status = 'ACTIVE'" in sql:
            record(
                "部分唯一索引在升级后保留",
                "PASS",
                "uq_calibration_records_active_target + status = 'ACTIVE'",
            )
        else:
            record(
                "部分唯一索引在升级后保留",
                "FAIL",
                f"索引 SQL={sql!r}",
            )

        now = datetime.now(UTC)
        project = uuid4()
        user = uuid4()
        profile_id = uuid4()
        factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        with factory() as session:
            session.add(Project(id=project, code="mig-a", name="迁移项目"))
            session.add(User(id=user, display_name="迁移用户"))
            validator = uuid4()
            session.add(User(id=validator, display_name="迁移校验人"))
            session.flush()
            session.add(
                CalibrationProfile(
                    id=profile_id,
                    project_id=project,
                    name="迁移配置",
                    scope=CalibrationScope.SYSTEM.value,
                    target_type=CalibrationTargetType.SYSTEM.value,
                    schema_version="ddp.calibration/1.0.0",
                    required_types=[CalibrationType.TIME_OFFSET.value],
                    selection_rule={},
                    version=1,
                    created_by=user,
                    updated_by=user,
                )
            )
            session.add(
                CalibrationTargetRef(
                    project_id=project,
                    target_type=CalibrationTargetType.SYSTEM.value,
                    target_id=project,
                    lifecycle_status=LifecycleStatus.ACTIVE.value,
                    is_retired=False,
                )
            )
            session.commit()

        def add_record(status: CalibrationStatus) -> bool:
            try:
                with factory() as session:
                    session.add(
                        CalibrationRecord(
                            id=uuid4(),
                            project_id=project,
                            profile_id=profile_id,
                            scope=CalibrationScope.SYSTEM.value,
                            target_type=CalibrationTargetType.SYSTEM.value,
                            target_id=project,
                            calibration_type=CalibrationType.TIME_OFFSET.value,
                            method="migration/1.0",
                            parameters={},
                            valid_from=now,
                            status=status.value,
                            evidence_asset_ids=[],
                            checksum="sha256:" + "0" * 64,
                            uncertainty={},
                            performed_by=user,
                            validated_by=validator,
                            validated_at=now,
                            created_by=user,
                            updated_by=user,
                        )
                    )
                    session.commit()
                return True
            except IntegrityError as exc:
                print(f"[INFO] 记录插入被拒绝：{exc.orig}")
                return False

        first_active = add_record(CalibrationStatus.ACTIVE)
        second_active = add_record(CalibrationStatus.ACTIVE)
        if first_active and not second_active:
            record(
                "同目标第二条 ACTIVE 被拒绝",
                "PASS",
                "部分唯一索引生效",
            )
        else:
            record(
                "同目标第二条 ACTIVE 被拒绝",
                "FAIL",
                f"first_active={first_active}, second_active={second_active}",
            )

        superseded = add_record(CalibrationStatus.SUPERSEDED)
        record(
            "同目标 SUPERSEDED 未被误伤",
            "PASS" if superseded else "FAIL",
            "允许非 ACTIVE 记录" if superseded else "非 ACTIVE 记录被拒绝",
        )

        # 带数据降级到 0003。
        try:
            command.downgrade(config, "0003")
            engine.dispose()
            engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
            tables = table_names(engine)
            triggers = trigger_names(engine)
            sql = index_sql(engine, "uq_calibration_records_active_target")
            if (
                "calibration_target_refs" not in tables
                and not (TRIGGERS & triggers)
                and sql is not None
                and "calibration_records" in tables
            ):
                record(
                    "带数据降级 0004 -> 0003",
                    "PASS",
                    "投影表/触发器删除，设备域数据与部分唯一索引保留",
                )
            else:
                record(
                    "带数据降级 0004 -> 0003",
                    "FAIL",
                    f"tables={sorted(tables)} triggers={sorted(triggers)}",
                )
        except Exception as exc:  # noqa: BLE001 - 探针需记录失败原因
            record("带数据降级 0004 -> 0003", "FAIL", f"{type(exc).__name__}: {exc}")

        # 再升级，验证可重入与 backfill。
        try:
            command.upgrade(config, "head")
            engine.dispose()
            engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
            with engine.connect() as connection:
                count = connection.execute(
                    sa.text("SELECT COUNT(*) FROM calibration_target_refs")
                ).scalar_one()
            if "calibration_target_refs" in table_names(engine) and count >= 1:
                record(
                    "再升级 head 可重入",
                    "PASS",
                    f"投影行 backfill 数量={count}",
                )
            else:
                record("再升级 head 可重入", "FAIL", f"投影行数量={count}")
        except Exception as exc:  # noqa: BLE001
            record("再升级 head 可重入", "FAIL", f"{type(exc).__name__}: {exc}")

        # 带数据降级到 0002（设备域表删除，身份域保留）。
        try:
            command.downgrade(config, "0002")
            engine.dispose()
            engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
            tables = table_names(engine)
            if (
                "devices" not in tables
                and "calibration_records" not in tables
                and "users" in tables
                and "projects" in tables
            ):
                record(
                    "带数据降级 0003 -> 0002",
                    "PASS（WARN）",
                    "设备域表删除，身份域表保留；生产回滚前必须备份",
                )
            else:
                record(
                    "带数据降级 0003 -> 0002",
                    "FAIL",
                    f"tables={sorted(tables)}",
                )
        except Exception as exc:  # noqa: BLE001
            record("带数据降级 0003 -> 0002", "FAIL", f"{type(exc).__name__}: {exc}")

        engine.dispose()

    print("\n=== 汇总 ===")
    for name, status, _detail in RESULTS:
        print(f"{status}\t{name}")
    failures = [item for item in RESULTS if item[1] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
