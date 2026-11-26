"""迁移 0084：``episodes.text_digest`` 列 + pg_trgm 索引。

覆盖：
- 升级建出可空 ``text_digest`` 与 ``(project_id, text_digest)`` B-tree 索引；
- 存量行的 ``language_instruction`` 被一次性回填（规范化口径与领域层一致）；
- 降级删列删索引、再升级可恢复（往返）；
- 配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上验证
  ``pg_trgm`` 扩展安装、GIN 索引建立与 ``similarity()`` 真实可用
  （MEM-INC-013：扩展与 GIN 索引只存在于 PG 分支）。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "episodes"
COLUMN = "text_digest"
BTREE_INDEX = "ix_episodes_project_text_digest"
TRGM_INDEX = "ix_episodes_language_instruction_trgm"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")

_BASE_INSTRUCTION = "pick up the red cup from the kitchen table"


def _config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 做插值：连接串里的 ``%`` 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+pysqlite:///{db_path}"


def _index_names(engine: Engine) -> set[str]:
    return {item["name"] for item in inspect(engine).get_indexes(TABLE)}


def _digest(instruction: str) -> str:
    normalized = " ".join(instruction.split())
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _seed_project_and_user(connection: object) -> None:
    connection.execute(  # type: ignore[attr-defined]
        text("INSERT INTO projects (id, code, name) VALUES ('p-0084', 'p-0084', '迁移 0084 项目')")
    )
    connection.execute(  # type: ignore[attr-defined]
        text("INSERT INTO users (id, display_name) VALUES ('p-0084', '迁移 0084 用户')")
    )


def _insert_episode(connection: object, episode_id: str, instruction: str | None) -> None:
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO episodes ("
            "id, project_id, episode_lineage_id, episode_version, code, status,"
            " quality_status, annotation_status, scene_labels, language_instruction,"
            " start_time_ns, end_time_ns, duration_ns, time_domain, time_base,"
            " canonical_schema_version, pipeline_id, pipeline_version,"
            " segmentation_rule_version, frame_count, calibration_snapshot,"
            " preview_refs, attempt, max_attempts, version, created_at, updated_at,"
            " created_by, updated_by"
            ") VALUES ("
            ":id, 'p-0084', :id, 1, :code, 'PENDING', 'NOT_CHECKED',"
            " 'NOT_STARTED', '[]', :instruction, 0, 1000, 1000, 'UTC', '{}',"
            " 'ddp.canonical/1.0.0-draft', 'data_source_import', '1.0.0',"
            " 'episode-boundary/1.0.0', 0, '[]', '[]', 1, 3, 1,"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'p-0084', 'p-0084'"
            ")"
        ),
        {"id": episode_id, "code": f"EP-{episode_id}", "instruction": instruction},
    )


def test_upgrade_adds_nullable_digest_column_and_index(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "dedup_up.db")
    command.upgrade(_config(url), "head")
    engine = create_engine(url)
    try:
        columns = {col["name"]: col for col in inspect(engine).get_columns(TABLE)}
        assert COLUMN in columns
        assert columns[COLUMN]["nullable"] is True
        assert "VARCHAR(71)" in str(columns[COLUMN]["type"]).upper()
        assert BTREE_INDEX in _index_names(engine)
        # SQLite 没有 pg_trgm：GIN 索引不得出现（方言分支正确跳过）。
        assert TRGM_INDEX not in _index_names(engine)
    finally:
        engine.dispose()


def test_upgrade_backfills_existing_rows(tmp_path: Path) -> None:
    """升级前的存量行在升级后拿到规范化指纹（空白结构被压平）。"""
    url = _sqlite_url(tmp_path / "dedup_backfill.db")
    config = _config(url)
    command.upgrade(config, "0082")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            _seed_project_and_user(conn)
            _insert_episode(conn, "e-0084-1", "  pick   up  the cup ")
            _insert_episode(conn, "e-0084-2", None)
        command.upgrade(config, "head")
        with engine.connect() as conn:
            filled = conn.execute(
                text("SELECT text_digest FROM episodes WHERE id = 'e-0084-1'")
            ).scalar_one()
            empty = conn.execute(
                text("SELECT text_digest FROM episodes WHERE id = 'e-0084-2'")
            ).scalar_one()
    finally:
        engine.dispose()
    assert filled == _digest("  pick   up  the cup ")
    assert empty is None


def test_downgrade_removes_column_and_index_then_upgrade_restores(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "dedup_down.db")
    config = _config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0082")
    engine = create_engine(url)
    try:
        columns = {col["name"] for col in inspect(engine).get_columns(TABLE)}
        assert COLUMN not in columns
        assert BTREE_INDEX not in _index_names(engine)
        assert TRGM_INDEX not in _index_names(engine)
    finally:
        engine.dispose()
    # 往返：回滚后重新升级必须恢复列与索引。
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        columns = {col["name"] for col in inspect(engine).get_columns(TABLE)}
        assert COLUMN in columns
        assert BTREE_INDEX in _index_names(engine)
    finally:
        engine.dispose()


def test_postgres_trigram_extension_and_index_when_configured() -> None:
    """真实 PG：pg_trgm 扩展 + GIN 索引 + similarity() 可用；降级只删索引。"""
    if not _POSTGRES_URL:
        pytest.skip("未配置 DDP_TEST_POSTGRES_URL，跳过 PostgreSQL 往返")
    config = _config(_POSTGRES_URL)
    command.upgrade(config, "head")
    engine = create_engine(_POSTGRES_URL)
    try:
        with engine.connect() as conn:
            extension = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            ).scalar_one_or_none()
            assert extension == 1
            gin_indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE indexname = :name"),
                    {"name": TRGM_INDEX},
                )
            }
            assert TRGM_INDEX in gin_indexes
            identical = conn.execute(
                text("SELECT similarity(:a, :b)"), {"a": _BASE_INSTRUCTION, "b": _BASE_INSTRUCTION}
            ).scalar_one()
            assert identical == pytest.approx(1.0)
            unrelated = conn.execute(
                text("SELECT similarity(:a, :b)"),
                {"a": _BASE_INSTRUCTION, "b": "navigate to the charging station"},
            ).scalar_one()
            assert unrelated < 0.5
    finally:
        engine.dispose()
    command.downgrade(config, "0082")
    engine = create_engine(_POSTGRES_URL)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
                    {"name": TRGM_INDEX},
                ).scalar_one_or_none()
                is None
            )
            # 扩展是数据库级共享能力：降级刻意不删（见迁移 docstring）。
            assert (
                conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
                ).scalar_one_or_none()
                == 1
            )
    finally:
        engine.dispose()
    command.upgrade(config, "head")
