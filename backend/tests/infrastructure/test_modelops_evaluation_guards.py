"""evaluations 表的库层守卫：基线与数据集版本成对、成功后不可篡改。

这些是**库层兜底**，与 ``app/domain/modelops/comparability.py`` 的应用层判定
同一语义、两处实现（一处拒绝非法输入，一处防绕过）。放在 infrastructure 而
不是 migrations 的原因：它们验证的是守卫与 CHECK 的行为，换一个迁移实现也应
当成立；迁移本身（幂等、回填、回滚）另见
``tests/migrations/test_evaluation_baseline_dataset_version_migration.py``。

为什么不能只靠应用层：本仓库刚发生过 ``requirement_match`` 漏登记进不可变清单
的事故（迁移 0038），此后任何人都能直接 UPDATE 把 FAILED 改成 PASSED 绕开
全部阻断逻辑。库层守卫是这类绕过的最后一道防线。
"""

from __future__ import annotations

# ruff: noqa: S608 - 表名与列名全部来自本模块常量，参数走绑定变量。
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "evaluations"
COLUMN = "baseline_dataset_version_id"
CHECK = "ck_evaluations_baseline_pair"
_THRESHOLDS_JSON = '{"success_rate": {"min": 0.9}}'


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _fk_engine(db_path: Path) -> Engine:
    """与应用同源的引擎：开启 ``PRAGMA foreign_keys=ON``。"""
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
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


def _seed(db_path: Path) -> dict:
    ids = {
        "project": uuid4().hex,
        "user": uuid4().hex,
        "model": uuid4().hex,
        "version": uuid4().hex,
        "dataset_version": uuid4().hex,
        "baseline_dataset_version": uuid4().hex,
    }
    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _seed_project_model_chain(connection, ids)
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


@pytest.mark.parametrize(
    ("baseline_id", "dataset_version_id", "expected"),
    [
        ("filled", None, False),
        (None, "filled", False),
        ("filled", "filled", True),
        (None, None, True),
    ],
)
def test_pair_check_rejects_half_references(
    tmp_path: Path, baseline_id: str | None, dataset_version_id: str | None, expected: bool
) -> None:
    """配对 CHECK 必须拒绝"只有一半"的引用。

    SQLite 默认不开 ``PRAGMA foreign_keys``，但 CHECK 一直生效——这是本用例
    能用真实写入证伪的原因（不依赖外键开关）。
    """
    db_path = tmp_path / f"pair-{uuid4().hex[:6]}.db"
    command.upgrade(_config(db_path), "head")
    ids = _seed(db_path)

    engine = _fk_engine(db_path)
    try:
        params = (
            uuid4().hex,
            ids["project"],
            ids["version"],
            ids["dataset_version"],
            uuid4().hex if baseline_id else None,
            uuid4().hex if dataset_version_id else None,
            "QUEUED",
            "{}",
            _THRESHOLDS_JSON,
            "UNKNOWN",
            1,
        )
        if expected:
            with engine.begin() as connection:
                _insert_with_baseline(connection, params)
        else:
            with pytest.raises(IntegrityError, match=CHECK):
                with engine.begin() as connection:
                    _insert_with_baseline(connection, params)
    finally:
        engine.dispose()


def test_immutable_guard_blocks_changing_baseline_after_success(tmp_path: Path) -> None:
    """评测成功后再改基线（或基线数据集版本）必须被数据库拒绝。

    这是把两列纳入 ``_IMMUTABLE_WHEN`` 的直接验收：改基线等于篡改
    "这条评测是跟谁比出来的"这一结论口径。
    """
    db_path = tmp_path / "immutable.db"
    command.upgrade(_config(db_path), "head")
    ids = _seed(db_path)
    row_id = uuid4().hex

    engine = _fk_engine(db_path)
    try:
        with engine.begin() as connection:
            _insert_with_baseline(
                connection,
                (
                    row_id,
                    ids["project"],
                    ids["version"],
                    ids["dataset_version"],
                    uuid4().hex,
                    uuid4().hex,
                    "QUEUED",
                    "{}",
                    _THRESHOLDS_JSON,
                    "UNKNOWN",
                    1,
                ),
            )
            connection.exec_driver_sql(
                "UPDATE evaluations SET status='RUNNING' WHERE id=:id", {"id": row_id}
            )
            connection.exec_driver_sql(
                "UPDATE evaluations SET status='SUCCEEDED', "
                "metrics='{\"success_rate\": 0.95}', verdict='PASS' WHERE id=:id",
                {"id": row_id},
            )
        for column, value in (
            ("baseline_evaluation_id", uuid4().hex),
            (COLUMN, uuid4().hex),
        ):
            with pytest.raises(IntegrityError, match="evaluations_immutable"):
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        f"UPDATE {TABLE} SET {column}=:value WHERE id=:id",
                        {"value": value, "id": row_id},
                    )
    finally:
        engine.dispose()


def test_guards_exist_after_migration(tmp_path: Path) -> None:
    """迁移完成四个触发器必须齐备（重建表容易静默丢触发器）。"""
    db_path = tmp_path / "triggers.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            names = {
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
    assert {
        "trg_evaluations_initial_status",
        "trg_evaluations_transition",
        "trg_evaluations_no_delete",
        "trg_evaluations_immutable",
    } <= names
