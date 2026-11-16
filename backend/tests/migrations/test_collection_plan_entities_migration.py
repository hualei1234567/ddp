"""迁移测试：0040 建立四张素材表并给任务补复合外键。

覆盖三件事（都是本次修复的核心价值，缺一条就等于没修）：

1. **四表建成 + 约束齐全**：复合唯一键、CHECK、索引、项目隔离；
2. **外键真的会拒绝**：跨项目引用与幽灵引用必须 ``IntegrityError``；
   只断言"外键存在"是不够的——SQLite 默认不开 ``PRAGMA foreign_keys``，
   外键列写进了 DDL 也不会生效，这条测试必须用真实写入来证伪；
3. **双向可迁移**：``downgrade`` 后四表与外键消失、守卫触发器仍在，
   再次 ``upgrade`` 成功（证明迁移可重复执行，不是一次性脚本）。

另外用 ``Model.__table__.create`` 之外的路径建库：本文件全程走 Alembic
``op.create_table``，因此全新库 upgrade head 与既有库升级必须都通过。
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
ASSET_TABLES = {"collection_plans", "collection_sops", "scenarios", "action_specs"}
TASK_ASSET_FOREIGN_KEYS = {
    "fk_collection_tasks_project_plan",
    "fk_collection_tasks_project_sop",
    "fk_collection_tasks_project_scenario",
    "fk_collection_tasks_project_action_spec",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
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


def test_fresh_database_upgrades_to_head_with_asset_tables(tmp_path: Path) -> None:
    """全新库一次性 upgrade head：四表建成，任务表带上四条复合外键。"""
    db_path = tmp_path / "fresh.db"
    command.upgrade(_config(db_path), "head")

    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        assert ASSET_TABLES <= set(inspector.get_table_names())

        task_fks = {item["name"] for item in inspector.get_foreign_keys("collection_tasks")}
        assert TASK_ASSET_FOREIGN_KEYS <= task_fks

        # 复合外键必须是 (project_id, xxx_id) 两列，单列外键挡不住跨项目引用。
        by_name = {item["name"]: item for item in inspector.get_foreign_keys("collection_tasks")}
        for name, column in (
            ("fk_collection_tasks_project_plan", "plan_id"),
            ("fk_collection_tasks_project_sop", "sop_id"),
            ("fk_collection_tasks_project_scenario", "scenario_id"),
            ("fk_collection_tasks_project_action_spec", "action_spec_id"),
        ):
            fk = by_name[name]
            assert fk["constrained_columns"] == ["project_id", column]
            assert fk["referred_columns"] == ["project_id", "id"]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("table", "expected_uniques", "expected_checks"),
    [
        (
            "collection_plans",
            {("project_id", "id"), ("project_id", "code", "plan_version")},
            {"ck_collection_plans_status_allowed", "ck_collection_plans_version_positive"},
        ),
        (
            "collection_sops",
            {("project_id", "id"), ("project_id", "code", "sop_version")},
            {"ck_collection_sops_status_allowed"},
        ),
        (
            "scenarios",
            {("project_id", "id"), ("project_id", "code")},
            {"ck_scenarios_status_allowed"},
        ),
        (
            "action_specs",
            {("project_id", "id"), ("project_id", "code", "spec_version")},
            {"ck_action_specs_status_allowed"},
        ),
    ],
)
def test_asset_tables_carry_project_scoped_constraints(
    tmp_path: Path,
    table: str,
    expected_uniques: set[tuple[str, ...]],
    expected_checks: set[str],
) -> None:
    db_path = tmp_path / f"{table}.db"
    command.upgrade(_config(db_path), "head")
    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
        assert expected_uniques <= uniques
        checks = {item["name"] for item in inspector.get_check_constraints(table)}
        assert expected_checks <= checks
        # 项目隔离：project_id 必须是 projects 的外键，且 created_by/updated_by 落在 users。
        fk_targets = {item["referred_table"] for item in inspector.get_foreign_keys(table)}
        assert "projects" in fk_targets
        assert "users" in fk_targets
    finally:
        engine.dispose()


def test_cross_project_reference_is_rejected_by_database(tmp_path: Path) -> None:
    """核心负向用例：A 项目的任务引用 B 项目的方案必须被数据库拒绝。

    在**同一条插入语句**里构造：任务 ``project_id=A``、``plan_id=属于 B 的 plan``。
    若外键是单列（只比 plan_id）或缺失，这条会静默成功——即"幽灵引用"。
    """
    db_path = tmp_path / "cross_project.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_a, project_b, user_id = uuid4().hex, uuid4().hex, uuid4().hex
    plan_b = uuid4().hex
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_a, project_b, user_id)
            connection.execute(
                text(
                    "INSERT INTO collection_plans (id, project_id, code, name, "
                    "plan_version, status, description, owner_id, version, "
                    "created_by, updated_by) VALUES "
                    "(:id, :project, 'PLAN-B', 'B 项目方案', 1, 'DRAFT', NULL, NULL, 1, "
                    ":user, :user)"
                ),
                {"id": plan_b, "project": project_b, "user": user_id},
            )

        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                        "priority, quality_summary, plan_id, created_by, updated_by) VALUES "
                        "(:id, :project, 'T-X', '跨项目任务', 'DRAFT', 'P1', '{}', :plan, "
                        ":user, :user)"
                    ),
                    {
                        "id": uuid4().hex,
                        "project": project_a,
                        "plan": plan_b,
                        "user": user_id,
                    },
                )

        # 同项目引用必须成功，证明拒绝的原因是"跨项目"而不是"任何引用都写不进"。
        with engine.begin() as connection:
            plan_a = uuid4().hex
            connection.execute(
                text(
                    "INSERT INTO collection_plans (id, project_id, code, name, "
                    "plan_version, status, description, owner_id, version, "
                    "created_by, updated_by) VALUES "
                    "(:id, :project, 'PLAN-A', 'A 项目方案', 1, 'DRAFT', NULL, NULL, 1, "
                    ":user, :user)"
                ),
                {"id": plan_a, "project": project_a, "user": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, plan_id, created_by, updated_by) VALUES "
                    "(:id, :project, 'T-OK', '同项目任务', 'DRAFT', 'P1', '{}', :plan, "
                    ":user, :user)"
                ),
                {"id": uuid4().hex, "project": project_a, "plan": plan_a, "user": user_id},
            )
    finally:
        engine.dispose()


def test_ghost_reference_is_rejected_by_database(tmp_path: Path) -> None:
    """不存在的 UUID（幽灵引用）必须被拒绝；NULL 引用必须仍然允许。"""
    db_path = tmp_path / "ghost.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)

        for column in ("plan_id", "sop_id", "scenario_id", "action_spec_id"):
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                            "priority, quality_summary, " + column + ", created_by, updated_by) "
                            "VALUES (:id, :project, :code, '幽灵引用', 'DRAFT', 'P1', '{}', "
                            ":ghost, :user, :user)"
                        ),
                        {
                            "id": uuid4().hex,
                            "project": project_id,
                            "code": f"T-{column}",
                            "ghost": uuid4().hex,
                            "user": user_id,
                        },
                    )

        # 六列保持可空：存量任务没有这些引用，NULL 必须能写入。
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, created_by, updated_by) VALUES "
                    "(:id, :project, 'T-NULL', '无引用任务', 'DRAFT', 'P1', '{}', :user, :user)"
                ),
                {"id": uuid4().hex, "project": project_id, "user": user_id},
            )
    finally:
        engine.dispose()


def test_downgrade_removes_tables_and_foreign_keys_then_reupgrade(tmp_path: Path) -> None:
    """回滚必须干净：四表消失、四条外键消失、采集守卫触发器仍在。"""
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    command.downgrade(config, "0032")
    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        assert not (ASSET_TABLES & set(inspector.get_table_names()))
        remaining = {item["name"] for item in inspector.get_foreign_keys("collection_tasks")}
        assert not (TASK_ASSET_FOREIGN_KEYS & remaining)
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
        assert "trg_collection_tasks_transition" in triggers
        assert "trg_collection_batches_transition" in triggers
    finally:
        engine.dispose()

    # 再升级：证明 downgrade 没有留下"半重建"的表状态。
    command.upgrade(config, "head")
    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        assert ASSET_TABLES <= set(inspector.get_table_names())
        task_fks = {item["name"] for item in inspector.get_foreign_keys("collection_tasks")}
        assert TASK_ASSET_FOREIGN_KEYS <= task_fks
    finally:
        engine.dispose()


def test_task_asset_foreign_keys_survive_with_existing_rows(tmp_path: Path) -> None:
    """带存量任务的库升级：数据不丢、状态不被重置、外键照常生效。

    batch 重建（SQLite 上加外键的唯一办法）会"建新表 + 搬数据"。
    若迁移写错列映射，最容易出现的后果就是**存量行丢失或 status 被写回默认值**，
    而这类缺陷在空库测试里完全看不出来。
    """
    db_path = tmp_path / "upgrade_with_rows.db"
    config = _config(db_path)
    command.upgrade(config, "0032")

    user_id, project_id, task_id = uuid4().hex, uuid4().hex, uuid4().hex
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, created_by, updated_by) VALUES "
                    "(:id, :project, 'T-LEGACY', '存量任务', 'DRAFT', 'P1', '{}', :user, :user)"
                ),
                {"id": task_id, "project": project_id, "user": user_id},
            )
            # 走合法迁移到非 DRAFT，验证重建后状态与乐观锁列都原样保留。
            connection.execute(
                text("UPDATE collection_tasks SET status='PENDING_REVIEW' WHERE id=:id"),
                {"id": task_id},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = _fk_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT project_id, code, status, version, plan_id "
                    "FROM collection_tasks WHERE id=:id"
                ),
                {"id": task_id},
            ).one()
        assert row[0] == project_id
        assert row[1] == "T-LEGACY"
        assert row[2] == "PENDING_REVIEW"
        assert row[3] == 1
        assert row[4] is None
    finally:
        engine.dispose()


def test_dangling_references_in_legacy_rows_are_cleared_with_audit_trail(
    tmp_path: Path,
) -> None:
    """核心回归：存量任务已有幽灵引用时，升级必须成功并把它们清空。

    这是本 bug 在真实库上的必然后果——``plan_id`` 等列从 0007 起就能写任意 UUID。
    给这样的表加外键，PostgreSQL 会在 ``ADD CONSTRAINT`` 时校验存量行并直接失败
    （已在真实 PG 上复现）。若迁移不先清理，升级就会被一条历史脏数据卡死。

    同时断言"清理要留痕"：每条被清空的任务 ID 必须出现在迁移日志里，
    否则就是静默吞数据。
    """
    db_path = tmp_path / "dangling.db"
    config = _config(db_path)
    command.upgrade(config, "0032")

    project_id, user_id, task_id = uuid4().hex, uuid4().hex, uuid4().hex
    ghost_plan, ghost_sop = uuid4().hex, uuid4().hex
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)
            # 模拟历史遗留：四个引用里两个是任意 UUID，版本号也一并写上。
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, plan_id, sop_id, sop_version, "
                    "created_by, updated_by) VALUES "
                    "(:id, :project, 'T-DIRTY', '脏数据任务', 'DRAFT', 'P1', '{}', "
                    ":plan, :sop, 'v1', :user, :user)"
                ),
                {
                    "id": task_id,
                    "project": project_id,
                    "plan": ghost_plan,
                    "sop": ghost_sop,
                    "user": user_id,
                },
            )
    finally:
        engine.dispose()

    # 不用 caplog：alembic 的 env.py 会调 ``fileConfig()`` 重置 root 上的 handler
    # （见 migrations/env.py 头注释），captured records 会因此丢失。
    # 直接把采集器挂在目标 logger 上，与被测代码解耦且不受 fileConfig 影响。
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target_logger = logging.getLogger("ddp.migration.0040")
    collector = _Collector(level=logging.WARNING)
    target_logger.addHandler(collector)
    try:
        command.upgrade(config, "head")
    finally:
        target_logger.removeHandler(collector)

    assert any(
        task_id in record.getMessage() for record in records
    ), "被清理的任务 ID 必须出现在迁移日志里，否则就是静默吞数据"

    engine = _fk_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT plan_id, sop_id, sop_version, scenario_id, action_spec_id "
                    "FROM collection_tasks WHERE id=:id"
                ),
                {"id": task_id},
            ).one()
        # 引用与"半截版本号"都必须清空：留下 sop_version 而 sop_id 为 NULL，
        # 会让下游误以为任务引用过某个 SOP 版本。
        assert row[0] is None
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None
    finally:
        engine.dispose()


def test_half_reference_version_snapshot_is_cleared_on_upgrade(tmp_path: Path) -> None:
    """回归用例：只有版本号、没有 ID 的"半截引用"必须一并清理。

    早期实现的清理条件只看四个引用列。``sop_id`` 为空但 ``sop_version='v1'``
    的任务不会命中，于是升级后仍然保存着"冻结了某版本、却不知道是哪份素材"的
    自相矛盾状态——应用层创建路径已拒绝这种组合（见 ``asset_references``），
    存量脏数据也必须按同一语义清掉。
    """
    db_path = tmp_path / "half_reference.db"
    config = _config(db_path)
    command.upgrade(config, "0032")

    project_id, user_id, task_id = uuid4().hex, uuid4().hex, uuid4().hex
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_user(connection, project_id, None, user_id)
            connection.execute(
                text(
                    "INSERT INTO collection_tasks (id, project_id, code, name, status, "
                    "priority, quality_summary, sop_version, action_spec_version, "
                    "created_by, updated_by) VALUES "
                    "(:id, :project, 'T-HALF', '半截引用任务', 'DRAFT', 'P1', '{}', "
                    "'v1', 'v2', :user, :user)"
                ),
                {"id": task_id, "project": project_id, "user": user_id},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = _fk_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT sop_id, sop_version, action_spec_id, action_spec_version "
                    "FROM collection_tasks WHERE id=:id"
                ),
                {"id": task_id},
            ).one()
        assert tuple(row) == (None, None, None, None)
    finally:
        engine.dispose()


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
