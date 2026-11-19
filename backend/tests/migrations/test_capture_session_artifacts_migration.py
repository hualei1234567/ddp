"""迁移测试：0068 采集会话 → RawAsset / Episode 归属链路。

覆盖 SQLite 与真实 PostgreSQL 的 upgrade → downgrade → upgrade 往返，
以及不能靠人工验收的数据库兜底：

- 三张表都新增可空 ``capture_session_id`` 列与 ``(project_id, capture_session_id)`` 索引；
- PostgreSQL 上复合外键强制同项目：跨项目引用被拒绝；
- 会话删除受 RESTRICT 保护（产物不悬空）；
- 回滚后列与索引完全移除，既有列与行为不变。

配置 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再跑一遍同一套往返。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
_TABLES = ("upload_sessions", "raw_assets", "episodes")
_INDEXES = {
    "upload_sessions": "ix_upload_sessions_project_capture_session",
    "raw_assets": "ix_raw_assets_project_capture_session",
    "episodes": "ix_episodes_project_capture_session",
}


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
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _columns(engine: Engine) -> dict[str, set[str]]:
    return {table: {col["name"] for col in inspect(engine).get_columns(table)} for table in _TABLES}


def _index_names(engine: Engine, table: str) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table)}


def test_sqlite_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "capture_session_artifacts.db"
    config = _config(db_path)

    command.upgrade(config, "0067")
    engine = _engine(db_path)
    try:
        columns = _columns(engine)
        for table in _TABLES:
            assert "capture_session_id" not in columns[table]
            assert _INDEXES[table] not in _index_names(engine, table)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        columns = _columns(engine)
        for table in _TABLES:
            assert "capture_session_id" in columns[table]
            assert _INDEXES[table] in _index_names(engine, table)
    finally:
        engine.dispose()

    command.downgrade(config, "0067")
    engine = _engine(db_path)
    try:
        columns = _columns(engine)
        for table in _TABLES:
            assert "capture_session_id" not in columns[table]
            assert _INDEXES[table] not in _index_names(engine, table)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        columns = _columns(engine)
        for table in _TABLES:
            assert "capture_session_id" in columns[table]
            assert _INDEXES[table] in _index_names(engine, table)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_capture_session_artifacts_roundtrip_on_postgresql() -> None:
    schema = f"ddp_capture_artifacts_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "0067")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            for table in _TABLES:
                assert "capture_session_id" not in columns[table]
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            for table in _TABLES:
                assert "capture_session_id" in columns[table]
                assert _INDEXES[table] in _index_names(engine, table)

            project_a, project_b, user_id = uuid4(), uuid4(), uuid4()
            task_id, session_id = uuid4(), uuid4()
            asset_id = uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
                    {"id": project_a, "code": schema[-20:], "name": "PG 项目 A"},
                )
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
                    {"id": project_b, "code": f"{schema[-18:]}b", "name": "PG 项目 B"},
                )
                connection.execute(
                    text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
                    {"id": user_id, "name": "PG 操作员"},
                )
                connection.execute(
                    text(
                        "INSERT INTO collection_tasks "
                        "(id, project_id, code, name, status, priority, "
                        "created_by, updated_by) VALUES "
                        "(:id, :project, 'TASK-1', '任务', 'DRAFT', 'P1', :user, :user)"
                    ),
                    {"id": task_id, "project": project_a, "user": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO capture_sessions "
                        "(id, project_id, code, task_id, status, created_by, updated_by) "
                        "VALUES (:id, :project, 'S-1', :task, 'SCHEDULED', :user, :user)"
                    ),
                    {
                        "id": session_id,
                        "project": project_a,
                        "task": task_id,
                        "user": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO raw_assets "
                        "(id, project_id, code, source_type, original_filename, object_key, "
                        "size_bytes, content_type, format, sha256, status, ingested_at, "
                        "storage_class, uploaded_by, version, created_by, updated_by) "
                        "VALUES (:id, :project, 'RAW-1', 'UPLOAD', 'a.mcap', :key, "
                        "1024, 'application/x-mcap', 'MCAP', :sha, 'VALIDATING', now(), "
                        "'STANDARD', :user, 1, :user, :user)"
                    ),
                    {
                        "id": asset_id,
                        "project": project_a,
                        "key": f"raw/{project_a}/2026/11/19/{asset_id}/content.mcap",
                        "sha": f"sha256:{'a' * 64}",
                        "user": user_id,
                    },
                )

            # 同项目引用合法。
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE raw_assets SET capture_session_id = :s WHERE id = :id"),
                    {"s": session_id, "id": asset_id},
                )

            # 跨项目引用被复合外键拒绝。
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO episodes "
                            "(id, project_id, episode_lineage_id, episode_version, code, "
                            "capture_session_id, status, start_time_ns, end_time_ns, "
                            "duration_ns, canonical_schema_version, pipeline_id, "
                            "pipeline_version, segmentation_rule_version, created_by, updated_by) "
                            "VALUES (:id, :project, :id, 1, 'EP-X', :session, 'PENDING', "
                            "1, 2, 1, 'v1', 'p', '1', 'r', :user, :user)"
                        ),
                        {
                            "id": uuid4(),
                            "project": project_b,
                            "session": session_id,
                            "user": user_id,
                        },
                    )

            # 会话删除受 RESTRICT 保护（另有禁删触发器，任一先命中即可）。
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM capture_sessions WHERE id = :id"),
                        {"id": session_id},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "0067")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            for table in _TABLES:
                assert "capture_session_id" not in columns[table]
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            for table in _TABLES:
                assert "capture_session_id" in columns[table]
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
