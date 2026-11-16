"""迁移测试：0042 建立 ``locations`` 表并给三处 ``location_id`` 补复合外键。

覆盖四件事（都是本次修复的核心价值，缺一条就等于没修）：

1. **表建成 + 约束齐全**：复合唯一键、CHECK、索引、项目隔离；
2. **外键真的会拒绝**：跨项目引用与幽灵引用必须 ``IntegrityError``，
   ``NULL`` 必须放行。只断言"外键存在"不够——SQLite 默认不开
   ``PRAGMA foreign_keys``，外键写进 DDL 也不会生效，必须用真实写入证伪；
3. **存量脏数据被清理且留痕**：造悬空引用 → 升级 → 断言被清空、
   且被清理的行 ID 出现在迁移日志里；
4. **双向可迁移**：``downgrade`` 后表与外键消失、采集守卫触发器仍在，
   再次 ``upgrade`` 成功。

另有一条守卫：迁移不得用 ``Model.__table__.create()`` 建表（与
``test_migrations_are_immutable_snapshots`` 同一口径，这里对 0042 单独钉死）。

PostgreSQL：配置 ``DDP_TEST_POSTGRES_URL`` 时用独立 schema 跑同一套升级/回滚
与外键行为；未配置则跳过并标注 BLOCKED。
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_FILE = BACKEND_DIR / "migrations" / "versions" / "0042_locations_and_foreign_keys.py"
LOCATION_FOREIGN_KEYS = {
    "fk_robots_project_location": ("robots", "location_id"),
    "fk_devices_project_location": ("devices", "location_id"),
    "fk_collection_tasks_project_location": ("collection_tasks", "location_id"),
}
GUARD_TRIGGERS = {
    "trg_collection_tasks_initial_status",
    "trg_collection_tasks_transition",
    "trg_collection_tasks_no_delete",
    "trg_collection_batches_initial_status",
    "trg_collection_batches_transition",
    "trg_collection_batches_no_delete",
}

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _config_for_url(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 做插值：PG 的 ``?options=-csearch_path%3D...``
    # 里那个 ``%`` 必须转义成 ``%%``，否则 set_main_option 直接抛 ValueError。
    # 与 ``test_annotation_quality_runs_migration.py`` 的既有做法一致。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _raw_engine(db_path: Path) -> Engine:
    """不带 PRAGMA 的引擎：只用于读结构，不用于证明外键行为。"""
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _fk_engine(db_path: Path) -> Engine:
    """与应用同源的引擎：开启 ``PRAGMA foreign_keys=ON``，用于证明外键真实生效。"""
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _seed_project_user(connection: object, project_id: str, other: str | None, user: str) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
        (project_id, f"p-{project_id[:6]}", "项目一"),
    )
    if other is not None:
        connection.exec_driver_sql(  # type: ignore[attr-defined]
            "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
            (other, f"p-{other[:6]}", "项目二"),
        )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (user, "用户一"),
    )


def _insert_location(connection: object, location_id: str, project_id: str, user: str) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO locations (id, project_id, code, name, kind, address, notes, "
        "version, created_by, updated_by) VALUES (?, ?, ?, ?, 'SITE', NULL, NULL, 1, ?, ?)",
        (location_id, project_id, f"LOC-{location_id[:6]}", "北京基地", user, user),
    )


def _insert_robot(connection: object, project_id: str, user: str, location: str | None) -> str:
    robot_id = uuid4().hex
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO robots (id, code, name, robot_type, status, capability_profile, "
            "project_id, location_id, version, created_at, updated_at, created_by, updated_by) "
            "VALUES (:id, :code, '机器人', 'HUMANOID', 'ACTIVE', '{}', :project, :location, 1, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user, :user)"
        ),
        {
            "id": robot_id,
            "code": f"R-{robot_id[:6]}",
            "project": project_id,
            "location": location,
            "user": user,
        },
    )
    return robot_id


def test_migration_uses_explicit_ddl_not_live_models() -> None:
    """守卫：0042 不得用 ``Model.__table__.create()`` 或 ``metadata.create_all()``。

    用活模型建表会把历史迁移绑死在**当前**模型定义上：模型后续加列时，
    全新库 upgrade 会 ``duplicate column name``（迁移 0025 的真实故障）。
    """
    tree = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "create_table" in calls
    assert "drop_table" in calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "create_all",
                "create",
            }, "迁移不得用活 ORM 模型或 metadata 建表"


def test_fresh_database_upgrades_to_head_with_locations(tmp_path: Path) -> None:
    """全新库一次性 upgrade head：``locations`` 建成，三表带上复合外键。"""
    db_path = tmp_path / "fresh.db"
    command.upgrade(_config(db_path), "head")

    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        assert "locations" in inspector.get_table_names()

        for name, (table, column) in LOCATION_FOREIGN_KEYS.items():
            by_name = {item["name"]: item for item in inspector.get_foreign_keys(table)}
            assert name in by_name, f"{table} 缺少 {name}"
            fk = by_name[name]
            # 复合外键必须是 (project_id, location_id) 两列，单列外键挡不住跨项目引用。
            assert fk["constrained_columns"] == ["project_id", column]
            assert fk["referred_columns"] == ["project_id", "id"]
            assert fk["referred_table"] == "locations"
    finally:
        engine.dispose()


def test_locations_table_carries_project_scoped_constraints(tmp_path: Path) -> None:
    """``locations`` 的复合唯一键、CHECK 与项目隔离必须齐全。"""
    db_path = tmp_path / "constraints.db"
    command.upgrade(_config(db_path), "head")
    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        uniques = {
            tuple(item["column_names"]) for item in inspector.get_unique_constraints("locations")
        }
        assert {("project_id", "id"), ("project_id", "code")} <= uniques
        checks = {item["name"] for item in inspector.get_check_constraints("locations")}
        assert {"ck_locations_kind_allowed", "ck_locations_version_positive"} <= checks
        fk_targets = {item["referred_table"] for item in inspector.get_foreign_keys("locations")}
        assert {"projects", "users"} <= fk_targets
        columns = {item["name"]: item for item in inspector.get_columns("locations")}
        # 列长与领域层常量对齐（NOTES_MAX_LENGTH = 2000 / address = 500）。
        assert columns["notes"]["type"].length == 2000
        assert columns["address"]["type"].length == 500
        # 审计字段形态与素材表一致：version 非空默认 1，四人字段非空。
        assert columns["version"]["nullable"] is False
        for name in ("created_at", "updated_at", "created_by", "updated_by"):
            assert columns[name]["nullable"] is False
    finally:
        engine.dispose()


def test_cross_project_reference_is_rejected_by_database(tmp_path: Path) -> None:
    """核心负向用例：A 项目的机器人引用 B 项目的场地必须被数据库拒绝。"""
    db_path = tmp_path / "cross_project.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_a, project_b, user_id = uuid4().hex, uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_a, project_b, user_id)
            location_b = uuid4().hex
            _insert_location(connection, location_b, project_b, user_id)

        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            with engine.begin() as connection:
                _insert_robot(connection, project_a, user_id, location_b)

        # 同项目引用必须成功：证明拒绝的原因是"跨项目"，不是"任何引用都写不进"。
        with engine.begin() as connection:
            location_a = uuid4().hex
            _insert_location(connection, location_a, project_a, user_id)
            _insert_robot(connection, project_a, user_id, location_a)
    finally:
        engine.dispose()


def test_ghost_reference_is_rejected_and_null_is_allowed(tmp_path: Path) -> None:
    """不存在的 UUID 必须被拒绝；NULL 必须仍然允许（"尚未指定基地"是合法状态）。"""
    db_path = tmp_path / "ghost.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)

        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            with engine.begin() as connection:
                _insert_robot(connection, project_id, user_id, uuid4().hex)

        # 三张表的 NULL 都必须放行。
        with engine.begin() as connection:
            _insert_robot(connection, project_id, user_id, None)
            connection.execute(
                text(
                    "INSERT INTO devices (id, code, name, device_type, status, "
                    "capability_profile, project_id, location_id, version, created_at, "
                    "updated_at, created_by, updated_by) VALUES (:id, 'D-NULL', '设备', "
                    "'COMPUTE', 'ACTIVE', '{}', :project, NULL, 1, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, :user, :user)"
                ),
                {"id": uuid4().hex, "project": project_id, "user": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, location_id, created_by, updated_by) VALUES "
                    "(:id, :project, 'T-NULL', '无场地任务', 'DRAFT', 'P1', '{}', NULL, "
                    ":user, :user)"
                ),
                {"id": uuid4().hex, "project": project_id, "user": user_id},
            )
    finally:
        engine.dispose()


def test_ghost_reference_is_rejected_on_all_three_tables(tmp_path: Path) -> None:
    """三处引用都必须真的被约束住——只修一处等于没修。"""
    db_path = tmp_path / "all_three.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)

        statements = {
            "robots": (
                "INSERT INTO robots (id, code, name, robot_type, status, capability_profile, "
                "project_id, location_id, version, created_at, updated_at, created_by, "
                "updated_by) VALUES (:id, :code, '机器人', 'HUMANOID', 'ACTIVE', '{}', "
                ":project, :ghost, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user, :user)"
            ),
            "devices": (
                "INSERT INTO devices (id, code, name, device_type, status, capability_profile, "
                "project_id, location_id, version, created_at, updated_at, created_by, "
                "updated_by) VALUES (:id, :code, '设备', 'COMPUTE', 'ACTIVE', '{}', :project, "
                ":ghost, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user, :user)"
            ),
            "collection_tasks": (
                "INSERT INTO collection_tasks (id, project_id, code, name, status, priority, "
                "quality_summary, location_id, created_by, updated_by) VALUES (:id, :project, "
                ":code, '任务', 'DRAFT', 'P1', '{}', :ghost, :user, :user)"
            ),
        }
        for table, statement in statements.items():
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                with engine.begin() as connection:
                    connection.execute(
                        text(statement),
                        {
                            "id": uuid4().hex,
                            "code": f"X-{table}",
                            "project": project_id,
                            "ghost": uuid4().hex,
                            "user": user_id,
                        },
                    )
    finally:
        engine.dispose()


def test_dangling_references_are_cleared_with_audit_trail(tmp_path: Path) -> None:
    """核心回归：存量行已有悬空/跨项目 ``location_id`` 时，升级必须成功并清空。

    这是本 bug 在真实库上的必然后果——三列从 0003 / 0007 起就能写任意 UUID。
    给这样的表加外键，PostgreSQL 会在 ``ADD CONSTRAINT`` 时校验存量行并直接失败。

    同时断言"清理要留痕"：被清空的行 ID 与**被丢弃的原值**都必须出现在迁移
    日志里，否则就是静默吞数据。
    """
    db_path = tmp_path / "dangling.db"
    config = _config(db_path)
    command.upgrade(config, "0040")

    project_a, project_b, user_id = uuid4().hex, uuid4().hex, uuid4().hex
    ghost = uuid4().hex
    robot_id, task_id = uuid4().hex, uuid4().hex
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_a, project_b, user_id)
            # 一个指向根本不存在的场地的机器人 + 一个任务，另一个设备保持 NULL。
            _insert_robot(connection, project_a, user_id, ghost)
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, location_id, created_by, updated_by) VALUES "
                    "(:id, :project, 'T-DIRTY', '脏数据任务', 'DRAFT', 'P1', '{}', :ghost, "
                    ":user, :user)"
                ),
                {"id": task_id, "project": project_a, "ghost": ghost, "user": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO devices (id, code, name, device_type, status, "
                    "capability_profile, project_id, location_id, version, created_at, "
                    "updated_at, created_by, updated_by) VALUES (:id, 'D-CLEAN', '设备', "
                    "'COMPUTE', 'ACTIVE', '{}', :project, NULL, 1, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, :user, :user)"
                ),
                {"id": uuid4().hex, "project": project_a, "user": user_id},
            )
            robot_id = connection.execute(
                text("SELECT id FROM robots WHERE location_id = :ghost"), {"ghost": ghost}
            ).scalar()
    finally:
        engine.dispose()

    # 不用 caplog：alembic 的 env.py 会调 ``fileConfig()`` 重置 root 上的 handler。
    # 直接把采集器挂在目标 logger 上，与被测代码解耦。
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target_logger = logging.getLogger("ddp.migration.0042")
    collector = _Collector(level=logging.WARNING)
    target_logger.addHandler(collector)
    try:
        command.upgrade(config, "head")
    finally:
        target_logger.removeHandler(collector)

    messages = [record.getMessage() for record in records]
    assert any(
        robot_id in message for message in messages
    ), "被清理的机器人行 ID 必须出现在迁移日志里，否则就是静默吞数据"
    assert any(task_id in message for message in messages)
    assert any(
        ghost in message for message in messages
    ), "被丢弃的 location_id 原值必须记录：它是唯一的追溯线索"

    engine = _fk_engine(db_path)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT location_id FROM robots WHERE id = :id"), {"id": robot_id}
                ).scalar()
                is None
            )
            assert (
                connection.execute(
                    text("SELECT location_id FROM collection_tasks WHERE id = :id"),
                    {"id": task_id},
                ).scalar()
                is None
            )
    finally:
        engine.dispose()


def test_clean_rows_are_not_touched_by_cleanup(tmp_path: Path) -> None:
    """清理不能误伤：``location_id`` 为 NULL 的行必须原样保留（含其它列）。"""
    db_path = tmp_path / "clean.db"
    config = _config(db_path)
    command.upgrade(config, "0040")

    project_id, user_id = uuid4().hex, uuid4().hex
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)
            _insert_robot(connection, project_id, user_id, None)
            connection.execute(text("UPDATE robots SET status='IN_MAINTENANCE'"))
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = _fk_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT code, status, version, location_id FROM robots")
            ).one()
        assert row[1] == "IN_MAINTENANCE"
        assert row[2] == 1
        assert row[3] is None
    finally:
        engine.dispose()


def test_downgrade_removes_table_and_foreign_keys_then_reupgrade(tmp_path: Path) -> None:
    """回滚必须干净：``locations`` 消失、三条外键消失、采集守卫触发器仍在。"""
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    command.downgrade(config, "0040")
    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        assert "locations" not in inspector.get_table_names()
        for name, (table, _column) in LOCATION_FOREIGN_KEYS.items():
            remaining = {item["name"] for item in inspector.get_foreign_keys(table)}
            assert name not in remaining
        # 回滚不能顺手删掉 0007 装的守卫触发器。
        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' "
                        "AND tbl_name IN ('collection_tasks','collection_batches')"
                    )
                )
            }
        assert GUARD_TRIGGERS <= triggers
    finally:
        engine.dispose()

    # 再升级：证明 downgrade 没有留下"半重建"的表状态。
    command.upgrade(config, "head")
    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        assert "locations" in inspector.get_table_names()
        for name, (table, _column) in LOCATION_FOREIGN_KEYS.items():
            assert name in {item["name"] for item in inspector.get_foreign_keys(table)}
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_and_constraints_on_postgresql() -> None:
    """真实 PostgreSQL：升级/回滚、外键三种行为、级联删除项目仍可用。

    SQLite 的 CHECK / 复合外键语义足够接近，但它不能替代验收目标库：
    ``Uuid`` 的落库形态、DDL 事务性与约束命名只在 PG 上才真实。
    用独立 schema 隔离，跑完清理，避免污染共享测试库。
    """
    schema = f"ddp_locations_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            assert "locations" in inspector.get_table_names()
            for name, (table, column) in LOCATION_FOREIGN_KEYS.items():
                by_name = {item["name"]: item for item in inspector.get_foreign_keys(table)}
                assert by_name[name]["constrained_columns"] == ["project_id", column]
                assert by_name[name]["referred_columns"] == ["project_id", "id"]

            project_a, project_b, user_id = uuid4(), uuid4(), uuid4()
            location_a, location_b = uuid4(), uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id,'pa','项目一')"),
                    {"id": project_a},
                )
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id,'pb','项目二')"),
                    {"id": project_b},
                )
                connection.execute(
                    text("INSERT INTO users (id, display_name) VALUES (:id,'用户')"),
                    {"id": user_id},
                )
                for location_id, project_id, code in (
                    (location_a, project_a, "LOC-A"),
                    (location_b, project_b, "LOC-B"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO locations (id, project_id, code, name, kind, "
                            "version, created_by, updated_by) VALUES (:id, :project, :code, "
                            "'基地', 'SITE', 1, :user, :user)"
                        ),
                        {
                            "id": location_id,
                            "project": project_id,
                            "code": code,
                            "user": user_id,
                        },
                    )

                # 同项目引用成功。
                connection.execute(
                    text(
                        "INSERT INTO robots (id, code, name, robot_type, status, "
                        "capability_profile, project_id, location_id, version, created_by, "
                        "updated_by) VALUES (:id, 'R-OK', '机器人', 'HUMANOID', 'ACTIVE', "
                        "'{}', :project, :location, 1, :user, :user)"
                    ),
                    {
                        "id": uuid4(),
                        "project": project_a,
                        "location": location_a,
                        "user": user_id,
                    },
                )

            # 跨项目与幽灵引用被拒（各自包 savepoint：PG 违反约束后事务进入 aborted）。
            for bad_project, bad_location in ((project_a, location_b), (project_a, uuid4())):
                with pytest.raises(IntegrityError):
                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                "INSERT INTO devices (id, code, name, device_type, status, "
                                "capability_profile, project_id, location_id, version, "
                                "created_by, updated_by) VALUES (:id, :code, '设备', 'COMPUTE', "
                                "'ACTIVE', '{}', :project, :location, 1, :user, :user)"
                            ),
                            {
                                "id": uuid4(),
                                "code": f"D-{uuid4().hex[:6]}",
                                "project": bad_project,
                                "location": bad_location,
                                "user": user_id,
                            },
                        )

            # 级联删除项目必须仍可用：NO ACTION 在此用例里不能把级联搞挂。
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_a})
                remaining = connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM robots WHERE project_id = :id), "
                        "(SELECT count(*) FROM locations WHERE project_id = :id)"
                    ),
                    {"id": project_a},
                ).one()
            assert tuple(remaining) == (0, 0)
        finally:
            engine.dispose()

        command.downgrade(config, "0040")
        engine = create_engine(url)
        try:
            assert "locations" not in set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
