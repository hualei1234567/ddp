"""迁移 0062：``data_source_reviews.evidence`` 的上/下/再上往返。

**为什么单独测这条迁移**

审核证据从 ``audit_logs.details`` 迁到审核表独立列，涉及三个不能靠人工
验收的属性：

- 升级后列存在且**可空**：存量审核没有独立证据，NULL 表示"未记录"，
  不能 NOT NULL 默认 ``{}`` 伪造出"无证据"；
- 升级前已存在的审核行升级后列为 NULL，行为仍是"回退审计留痕"；
- 回滚删列不丢审核结论本身，且可再次升级。

SQLite 覆盖方言差异最大的路径（ADD COLUMN / batch 重建）；配置
``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再走一遍同一套往返。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "data_source_reviews"
COLUMN = "evidence"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _config_for_url(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 插值：PG 的 ``?options=-csearch_path%3D...`` 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _config(db_path: Path) -> Config:
    return _config_for_url(f"sqlite+pysqlite:///{db_path}")


def _db_id(value: UUID, dialect_name: str) -> object:
    """SQLite 的 ``Uuid`` 落成 32 位无连字符文本，PG 落成原生 uuid。"""
    return value.hex if dialect_name == "sqlite" else value


def _columns(engine: Engine) -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in inspect(engine).get_columns(TABLE)}


def _seed_legacy_review(connection: sa.Connection) -> UUID:
    """在 0061（尚无 evidence 列）的库里写入一条历史审核记录。"""
    dialect_name = connection.dialect.name
    project_id, user_id, reviewer_id = uuid4(), uuid4(), uuid4()
    source_id, import_id, review_id = uuid4(), uuid4(), uuid4()
    ids = {
        "project_id": _db_id(project_id, dialect_name),
        "user_id": _db_id(user_id, dialect_name),
        "reviewer_id": _db_id(reviewer_id, dialect_name),
        "source_id": _db_id(source_id, dialect_name),
        "import_id": _db_id(import_id, dialect_name),
        "review_id": _db_id(review_id, dialect_name),
    }
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
        {"id": ids["project_id"], "code": f"p-{project_id.hex[:8]}", "name": "项目"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
        {"id": ids["user_id"], "name": "提交人"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
        {"id": ids["reviewer_id"], "name": "审核人"},
    )
    connection.execute(
        text(
            "INSERT INTO data_sources ("
            "id, project_id, created_at, updated_at, created_by, updated_by,"
            " code, name, kind, repo_id, revision, license, access_condition, is_gated"
            ") VALUES ("
            ":id, :project_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user_id, :user_id,"
            " :code, '历史数据源', 'HUGGINGFACE', 'lerobot/aloha_sim', :revision,"
            " 'Apache-2.0', 'PUBLIC', false"
            ")"
        ),
        {
            "id": ids["source_id"],
            "project_id": ids["project_id"],
            "user_id": ids["user_id"],
            "code": f"src-{source_id.hex[:8]}",
            "revision": "a" * 40,
        },
    )
    connection.execute(
        text(
            "INSERT INTO data_source_imports ("
            "id, project_id, created_at, updated_at, created_by, updated_by,"
            " data_source_id, status, scope, tool_name, tool_version"
            ") VALUES ("
            ":id, :project_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user_id, :user_id,"
            " :source_id, 'SUCCEEDED', 'FULL', 'dataset-tool', '1.0.0'"
            ")"
        ),
        {
            "id": ids["import_id"],
            "project_id": ids["project_id"],
            "user_id": ids["user_id"],
            "source_id": ids["source_id"],
        },
    )
    connection.execute(
        text(
            "INSERT INTO data_source_reviews ("
            "id, import_id, status, decision, reason, reviewer_id, submitted_by,"
            " decided_at, project_id, created_at, updated_at, created_by, updated_by,"
            " version"
            ") VALUES ("
            ":id, :import_id, 'APPROVED', 'ACCEPT', '历史通过', :reviewer_id, :user_id,"
            " CURRENT_TIMESTAMP, :project_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
            " :user_id, :user_id, 2"
            ")"
        ),
        {
            "id": ids["review_id"],
            "import_id": ids["import_id"],
            "reviewer_id": ids["reviewer_id"],
            "user_id": ids["user_id"],
            "project_id": ids["project_id"],
        },
    )
    return review_id


def _evidence_table() -> sa.Table:
    return sa.table(
        TABLE,
        sa.column("id", sa.Uuid()),
        sa.column(COLUMN, _JSON_TYPE),
    )


def _read_evidence(engine: Engine, review_id: UUID) -> object:
    table = _evidence_table()
    with engine.begin() as connection:
        return connection.execute(
            select(table.c[COLUMN]).where(table.c.id == review_id)
        ).scalar_one()


def _write_evidence(engine: Engine, review_id: UUID, payload: dict[str, object]) -> None:
    table = _evidence_table()
    with engine.begin() as connection:
        connection.execute(
            table.update().where(table.c.id == review_id).values(**{COLUMN: payload})
        )


def test_sqlite_roundtrip_keeps_legacy_rows_null(tmp_path: Path) -> None:
    db_path = tmp_path / "review_evidence.db"
    config = _config(db_path)

    command.upgrade(config, "0061")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert COLUMN not in _columns(engine)
        with engine.begin() as connection:
            review_id = _seed_legacy_review(connection)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        columns = _columns(engine)
        assert COLUMN in columns
        assert columns[COLUMN]["nullable"] is True
        # 0026 改写为显式 DDL 后，约束名必须与 create_all 路径一致
        # （CHECK 名称由命名约定展开，只传短名，避免二次加前缀）。
        assert {
            "ck_data_source_reviews_review_status_allowed",
            "ck_data_source_reviews_review_decision_allowed",
            "ck_data_source_reviews_review_segregation_of_duties",
            "ck_data_source_reviews_version_positive",
        } <= {item["name"] for item in inspect(engine).get_check_constraints(TABLE)}
        # 存量不伪造：历史审核的独立证据列必须是 NULL。
        assert _read_evidence(engine, review_id) is None

        payload = {"timestamp_seconds": 12.5, "note": "新列可写"}
        _write_evidence(engine, review_id, payload)
        assert _read_evidence(engine, review_id) == payload
    finally:
        engine.dispose()

    command.downgrade(config, "0061")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert COLUMN not in _columns(engine)
        # 回滚只删证据列，审核结论本身保留。
        with engine.begin() as connection:
            status = connection.execute(
                text("SELECT status FROM data_source_reviews WHERE id = :id"),
                {"id": review_id.hex},
            ).scalar_one()
        assert status == "APPROVED"
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert COLUMN in _columns(engine)
        # 回滚已丢弃回滚前写入的列内证据；重新升级得到 NULL，不凭空恢复。
        assert _read_evidence(engine, review_id) is None
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_postgresql_roundtrip_keeps_legacy_rows_null() -> None:
    """真实 PostgreSQL：UUID/JSONB 落库形态与回滚语义只能在 PG 上验收。"""
    schema = f"ddp_review_evidence_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "0061")
        engine = create_engine(url)
        try:
            assert COLUMN not in _columns(engine)
            with engine.begin() as connection:
                review_id = _seed_legacy_review(connection)
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            assert COLUMN in columns
            assert columns[COLUMN]["nullable"] is True
            assert str(columns[COLUMN]["type"]).upper() == "JSONB"
            assert _read_evidence(engine, review_id) is None

            payload = {"timestamp_seconds": 12.5, "note": "PG 新列"}
            _write_evidence(engine, review_id, payload)
            assert _read_evidence(engine, review_id) == payload
        finally:
            engine.dispose()

        command.downgrade(config, "0061")
        engine = create_engine(url)
        try:
            assert COLUMN not in _columns(engine)
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert COLUMN in _columns(engine)
            assert _read_evidence(engine, review_id) is None
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
