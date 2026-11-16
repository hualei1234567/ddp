"""迁移 0043：evaluations.baseline_dataset_version_id 的升级/回滚。

本文件要证明的不是"列加上了"，而是三件在真实环境里会要命的事：

1. **全新库与存量库两条路径都通过**。0021 用活的 ORM 表建 ``evaluations``，
   给 ORM 加列后全新库会自动带出该列——若 0043 无条件 ``add_column``，
   全新库升级直接 ``duplicate column``；
2. **回填真的做了**。存量库里必然有「有基线」的评测（该列从 0021 起就能写），
   不回填就会在加配对 CHECK 时因约束违反让升级失败；
3. **重建表不会静默删掉守卫触发器**。SQLite 上加 CHECK 只能重建表，
   而 ``DROP TABLE`` 会连带删除表上所有触发器。若不拆装守卫，状态机、
   不可变字段、禁删三道兜底会同时失效，而 API 测试全绿——它们不检查触发器。
"""

from __future__ import annotations

# ruff: noqa: S608 - 被测对象是迁移的 DDL/DML；表名与列名全部来自本模块声明的
# 常量（TABLE / COLUMN / CHECK），参数一律走绑定变量，不接收外部输入。
import logging
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "evaluations"
COLUMN = "baseline_dataset_version_id"
CHECK = "ck_evaluations_baseline_pair"
_THRESHOLDS_JSON = '{"success_rate": {"min": 0.9}}'
EVALUATION_TRIGGERS = {
    "trg_evaluations_initial_status",
    "trg_evaluations_transition",
    "trg_evaluations_no_delete",
    "trg_evaluations_immutable",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _raw_engine(db_path: Path) -> Engine:
    """不带 PRAGMA 的引擎：只用于读结构。"""
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _fk_engine(db_path: Path) -> Engine:
    """与应用同源的引擎：开启 ``PRAGMA foreign_keys=ON``，用于写入验证。"""
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _columns(db_path: Path) -> list[str]:
    engine = _raw_engine(db_path)
    try:
        return [item["name"] for item in inspect(engine).get_columns(TABLE)]
    finally:
        engine.dispose()


def _checks(db_path: Path) -> set[str]:
    engine = _raw_engine(db_path)
    try:
        return {item["name"] for item in inspect(engine).get_check_constraints(TABLE)}
    finally:
        engine.dispose()


def _triggers(db_path: Path) -> set[str]:
    engine = _raw_engine(db_path)
    try:
        with engine.connect() as connection:
            return {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master " "WHERE type='trigger' AND tbl_name=:table"
                    ),
                    {"table": TABLE},
                )
            }
    finally:
        engine.dispose()


def _seed_evaluation_rows(db_path: Path, *, with_baseline: bool, status: str = "QUEUED") -> dict:
    """直接落库造评测行；返回关键 ID，便于断言回填结果。

    不走应用层：存量库里的行是**旧代码**写进去的，用新代码造数据就测不到
    "迁移如何对待历史行"。``status`` 可设为 ``SUCCEEDED`` 以验证回填顺序
    （不可变守卫会阻止改动已完成评测的基线列）。
    """
    ids = {
        "project": uuid4().hex,
        "user": uuid4().hex,
        "model": uuid4().hex,
        "version": uuid4().hex,
        "dataset_version": uuid4().hex,
        "baseline_dataset_version": uuid4().hex,
        "baseline": uuid4().hex,
        "row": uuid4().hex,
    }
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_model_chain(connection, ids)
            if with_baseline:
                # 基线行本身必须存在，否则回填会把它判成悬空引用（见另一用例）。
                connection.exec_driver_sql(
                    "INSERT INTO evaluations (id, project_id, model_version_id, "
                    "dataset_version_id, status, metrics, thresholds, verdict, version, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (
                        ids["baseline"],
                        ids["project"],
                        ids["version"],
                        ids["baseline_dataset_version"],
                        "QUEUED",
                        "{}",
                        _THRESHOLDS_JSON,
                        "UNKNOWN",
                        1,
                    ),
                )
            # 必须按"初始状态"插入，再用 UPDATE 迁移到目标状态：
            # ``trg_evaluations_initial_status`` 只允许 QUEUED 作为插入状态。
            # 新列在"列已存在"的库上必须同步写入：配对 CHECK 要求基线 ID 与
            # 基线数据集版本成对出现。旧结构库上该列不存在，故按实际列择一。
            has_new_column = COLUMN in {
                item["name"] for item in inspect(connection).get_columns(TABLE)
            }
            baseline_id = ids["baseline"] if with_baseline else None
            if has_new_column:
                _insert_with_baseline(
                    connection,
                    (
                        ids["row"],
                        ids["project"],
                        ids["version"],
                        ids["dataset_version"],
                        baseline_id,
                        ids["baseline_dataset_version"] if with_baseline else None,
                        "QUEUED",
                        "{}",
                        _THRESHOLDS_JSON,
                        "UNKNOWN",
                        1,
                    ),
                )
            else:
                connection.exec_driver_sql(
                    "INSERT INTO evaluations (id, project_id, model_version_id, "
                    "dataset_version_id, baseline_evaluation_id, status, metrics, "
                    "thresholds, verdict, version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (
                        ids["row"],
                        ids["project"],
                        ids["version"],
                        ids["dataset_version"],
                        baseline_id,
                        "QUEUED",
                        "{}",
                        _THRESHOLDS_JSON,
                        "UNKNOWN",
                        1,
                    ),
                )
            if status != "QUEUED":
                connection.exec_driver_sql(
                    "UPDATE evaluations SET status='RUNNING' WHERE id=?", (ids["row"],)
                )
                connection.exec_driver_sql(
                    "UPDATE evaluations SET status=?, metrics='{\"success_rate\": 0.95}', "
                    "verdict='PASS' WHERE id=?",
                    (status, ids["row"]),
                )
    finally:
        engine.dispose()
    return ids


def _insert_with_baseline(connection: object, params: tuple) -> None:
    """写一条带新列（基线 ID + 基线数据集版本）的评测。"""
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        f"INSERT INTO {TABLE} (id, project_id, model_version_id, dataset_version_id, "
        "baseline_evaluation_id, "
        f"{COLUMN}, status, metrics, thresholds, verdict, version, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        params,
    )


def _seed_project_model_chain(connection: object, ids: dict) -> None:
    """项目 / 用户 / 模型 / 模型版本四行；评测行依赖它们的外键。"""
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
        (ids["project"], f"p-{ids['project'][:6]}", "项目一"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (ids["user"], "用户一"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO models (id, project_id, code, name, task_type, framework, "
        "owner_id, status, tags, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (
            ids["model"],
            ids["project"],
            "m-1",
            "模型",
            "IMITATION",
            "pytorch",
            ids["user"],
            "DRAFT",
            "[]",
            1,
        ),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO model_versions (id, project_id, model_id, version_no, status, "
        "artifact, runtime, artifact_sha256, dataset_version_ids, metrics_summary, "
        "version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (
            ids["version"],
            ids["project"],
            ids["model"],
            1,
            "DRAFT",
            "{}",
            "{}",
            "sha256:" + "a" * 64,
            "[]",
            "{}",
            1,
        ),
    )


def _downgrade_table_to_pre_0043_shape(db_path: Path) -> None:
    """把 evaluations 还原成 0021 当时的形状：没有新列，也没有配对 CHECK。

    当前 ORM 已含新列，因此 ``0021`` 在全新库上会自动建出它们；要模拟
    "升级前就已存在的库"，必须把两者都去掉。两个顺序陷阱（都实测过）：

    1. 删列前必须先删 CHECK：``ALTER TABLE ... DROP COLUMN`` 会校验表上的
       CHECK 正文，残留的配对 CHECK 引用了该列 → 报
       ``error in table evaluations after drop column: no such column``；
    2. 删列前还要先拆守卫：不可变触发器正文同样引用了该列，报同一个错。
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.infrastructure.db.modelops_guards import (
        drop_modelops_guards,
        install_modelops_guards,
    )

    engine = _raw_engine(db_path)
    try:
        with engine.begin() as connection:
            inspect_connection = inspect(connection)
            assert COLUMN in {
                item["name"] for item in inspect_connection.get_columns(TABLE)
            }, "当前 ORM 已含新列，先确认前置条件"
            drop_modelops_guards(connection)
            if CHECK in {item["name"] for item in inspect_connection.get_check_constraints(TABLE)}:
                operations = Operations(MigrationContext.configure(connection))
                with operations.batch_alter_table(TABLE, recreate="auto") as batch:
                    batch.drop_constraint(operations.f(CHECK), type_="check")
            connection.exec_driver_sql(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
            install_modelops_guards(connection)
    finally:
        engine.dispose()
    assert COLUMN not in _columns(db_path), "模拟旧结构失败，用例前提不成立"
    assert CHECK not in _checks(db_path), "模拟旧结构失败，CHECK 未移除"


def test_fresh_database_upgrades_with_column_check_and_guards(tmp_path: Path) -> None:
    """全新库：0021 已按 ORM 建出列与 CHECK，0043 必须是空操作。"""
    db_path = tmp_path / "fresh.db"
    command.upgrade(_config(db_path), "head")

    assert COLUMN in _columns(db_path)
    assert CHECK in _checks(db_path)
    # 守卫触发器必须齐备——重建表若漏装回，这里立刻红。
    assert EVALUATION_TRIGGERS <= _triggers(db_path)


def test_existing_database_gains_column_and_backfills_baseline(tmp_path: Path) -> None:
    """存量库（列尚不存在）升级：加列 + 回填基线数据集版本。"""
    db_path = tmp_path / "legacy.db"
    config = _config(db_path)
    command.upgrade(config, "0040")
    _downgrade_table_to_pre_0043_shape(db_path)

    ids = _seed_evaluation_rows(db_path, with_baseline=True)
    command.upgrade(config, "head")

    assert COLUMN in _columns(db_path)
    assert CHECK in _checks(db_path)
    engine = _raw_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(f"SELECT baseline_evaluation_id, {COLUMN} FROM {TABLE} WHERE id=:id"),
                {"id": ids["row"]},
            ).one()
    finally:
        engine.dispose()
    # 回填值必须等于基线行自己的 dataset_version_id。
    assert row[0] == ids["baseline"]
    assert row[1] == ids["baseline_dataset_version"]


def test_backfill_works_for_already_succeeded_evaluations(tmp_path: Path) -> None:
    """已完成（SUCCEEDED）的存量行也要能回填。

    不可变守卫禁止改动已完成评测的不可变列，而 ``baseline_evaluation_id``
    本次被纳入该清单——若回填在守卫在线时执行，升级会直接报
    ``evaluations_immutable``。用例锁住"先拆守卫再回填"这个顺序。
    """
    db_path = tmp_path / "succeeded.db"
    config = _config(db_path)
    command.upgrade(config, "0040")
    _downgrade_table_to_pre_0043_shape(db_path)

    ids = _seed_evaluation_rows(db_path, with_baseline=True, status="SUCCEEDED")
    command.upgrade(config, "head")

    engine = _raw_engine(db_path)
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text(f"SELECT {COLUMN} FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
            ).scalar()
    finally:
        engine.dispose()
    assert value == ids["baseline_dataset_version"]


def test_dangling_baseline_is_cleared_with_audit_trail(tmp_path: Path) -> None:
    """基线行不存在（幽灵引用）→ 置空并留痕，不让升级卡死。

    这列从 0021 起就允许写任意 UUID，真实库里完全可能有指向已不存在评测的
    基线。给这种行加"成对"CHECK 会失败；保留 ID 而新列为 NULL 也违反 CHECK。
    因此清空并输出到 ``ddp.migration.0043`` 日志，可审计、可追认。
    """
    db_path = tmp_path / "dangling.db"
    config = _config(db_path)
    command.upgrade(config, "0040")
    _downgrade_table_to_pre_0043_shape(db_path)

    ids = _seed_evaluation_rows(db_path, with_baseline=False)
    # 手工写一个指向不存在评测的基线（旧代码允许这么写）。
    ghost = uuid4().hex
    engine = _raw_engine(db_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"UPDATE {TABLE} SET baseline_evaluation_id=:ghost WHERE id=:id",
                {"ghost": ghost, "id": ids["row"]},
            )
    finally:
        engine.dispose()

    # 不用 caplog：alembic 的 env.py 会调 ``fileConfig()`` 重置 root handler。
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target = logging.getLogger("ddp.migration.0043")
    collector = _Collector(level=logging.WARNING)
    target.addHandler(collector)
    try:
        command.upgrade(config, "head")
    finally:
        target.removeHandler(collector)

    assert any(
        ids["row"] in record.getMessage() for record in records
    ), "被清理的行 ID 必须出现在迁移日志里，否则就是静默改数据"
    engine = _raw_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(f"SELECT baseline_evaluation_id, {COLUMN} FROM {TABLE} WHERE id=:id"),
                {"id": ids["row"]},
            ).one()
    finally:
        engine.dispose()
    assert tuple(row) == (None, None)


def test_downgrade_roundtrip_restores_shape_and_keeps_guards(tmp_path: Path) -> None:
    """回滚：列与 CHECK 消失，四个守卫触发器仍在，再次升级成功。"""
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    ids = _seed_evaluation_rows(db_path, with_baseline=True)

    command.downgrade(config, "0040")
    assert COLUMN not in _columns(db_path)
    assert CHECK not in _checks(db_path)
    assert EVALUATION_TRIGGERS <= _triggers(db_path), "回滚不得留下无守卫的表"

    # 守卫正文不得再引用已删列，否则第一次 UPDATE 就会炸。
    engine = _raw_engine(db_path)
    try:
        with engine.connect() as connection:
            sql = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='trigger' AND name='trg_evaluations_immutable'"
                )
            ).scalar()
    finally:
        engine.dispose()
    assert sql is not None
    assert COLUMN not in sql

    command.upgrade(config, "head")
    assert COLUMN in _columns(db_path)
    assert CHECK in _checks(db_path)
    assert EVALUATION_TRIGGERS <= _triggers(db_path)
    # 回滚再升级后存量行仍在（重建表不得丢数据）。
    engine = _raw_engine(db_path)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text(f"SELECT COUNT(*) FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
            ).scalar()
    finally:
        engine.dispose()
    assert count == 1
