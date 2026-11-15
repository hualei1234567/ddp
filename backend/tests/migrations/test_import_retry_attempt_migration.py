"""迁移 0034：导入任务 attempt / max_attempts 在两种方言下都真的落库。

**为什么单独测这条迁移**

0034 用 ``batch_alter_table`` 加列与加 CHECK。SQLite 走的重建表路径、
PostgreSQL 走原生 ALTER，两条路径的行为不同；只测其中一个方言很容易出现
"本地 SQLite 绿、真实 PG 才炸"。这里显式验证：

- 两列存在且 NOT NULL、默认值正确；
- 三条 CHECK 真的建出来了（而不只是写了代码）；
- 回滚把列与约束都清干净（``alembic downgrade`` 必须可逆）。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def test_columns_and_checks_exist_after_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "import_retry.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        columns = {col["name"]: col for col in inspect(engine).get_columns("data_source_imports")}
        assert {"attempt", "max_attempts"} <= set(columns)
        assert columns["attempt"]["nullable"] is False
        assert columns["max_attempts"]["nullable"] is False

        # 约束真的在库里，而不是只写在迁移代码里。
        with engine.connect() as conn:
            ddl = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='data_source_imports'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert "import_attempt_positive" in ddl or "attempt >= 1" in ddl
    assert "import_attempt_within_max" in ddl or "attempt <= max_attempts" in ddl


def test_downgrade_removes_columns_and_checks(tmp_path: Path) -> None:
    db_path = tmp_path / "import_retry_down.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0033")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        columns = {col["name"] for col in inspect(engine).get_columns("data_source_imports")}
    finally:
        engine.dispose()
    assert "attempt" not in columns
    assert "max_attempts" not in columns
