"""上传/RawAsset 数据库兜底：初始状态、合法迁移、内容不可变与禁删。

与采集域一致：迁移 0008 与 ``Base.metadata.create_all`` 安装同一份 DDL，
SQLite 仅用于测试，PostgreSQL 为验收目标。
"""

# ruff: noqa: S608 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.ingestion.state_machines import (
    raw_asset_transition_pairs,
    upload_session_transition_pairs,
)

_RAW_ASSET_IMMUTABLE_COLUMNS = (
    "project_id",
    "object_key",
    "sha256",
    "size_bytes",
    "upload_session_id",
)
_LINK_STATUS_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("PENDING_VERIFICATION", "ACTIVE"),
        ("PENDING_VERIFICATION", "REVOKED"),
        ("ACTIVE", "REVOKED"),
    }
)


def _pairs_sql(pairs: frozenset[tuple[str, str]]) -> str:
    return " OR ".join(
        f"(OLD.status = '{current}' AND NEW.status = '{target}')"
        for current, target in sorted(pairs)
    )


_SESSION_PAIRS = _pairs_sql(upload_session_transition_pairs())
_RAW_ASSET_PAIRS = _pairs_sql(raw_asset_transition_pairs())
_SQLITE_IMMUTABLE_WHEN = " OR ".join(
    f"OLD.{column} IS NOT NEW.{column}" for column in _RAW_ASSET_IMMUTABLE_COLUMNS
)
_PG_IMMUTABLE_WHEN = " OR ".join(
    f"OLD.{column} IS DISTINCT FROM NEW.{column}" for column in _RAW_ASSET_IMMUTABLE_COLUMNS
)


_SQLITE_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_upload_sessions_initial_status
    BEFORE INSERT ON upload_sessions
    FOR EACH ROW
    WHEN NEW.status <> 'CREATED'
    BEGIN
        SELECT RAISE(ABORT, 'upload_session_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_upload_sessions_transition
    BEFORE UPDATE ON upload_sessions
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_SESSION_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'upload_session_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_upload_sessions_no_delete
    BEFORE DELETE ON upload_sessions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'upload_session_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_upload_parts_no_update
    BEFORE UPDATE ON upload_parts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'upload_part_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_upload_parts_no_delete
    BEFORE DELETE ON upload_parts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'upload_part_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_raw_assets_initial_status
    BEFORE INSERT ON raw_assets
    FOR EACH ROW
    WHEN NEW.status <> 'VALIDATING'
    BEGIN
        SELECT RAISE(ABORT, 'raw_asset_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_raw_assets_transition
    BEFORE UPDATE ON raw_assets
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_RAW_ASSET_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'raw_asset_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_raw_assets_content_immutable
    BEFORE UPDATE ON raw_assets
    FOR EACH ROW
    WHEN {_SQLITE_IMMUTABLE_WHEN}
    BEGIN
        SELECT RAISE(ABORT, 'raw_asset_content_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_raw_assets_no_delete
    BEFORE DELETE ON raw_assets
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'raw_asset_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_raw_asset_links_no_delete
    BEFORE DELETE ON raw_asset_links
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'raw_asset_link_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_raw_asset_links_terminal_status
    BEFORE UPDATE ON raw_asset_links
    FOR EACH ROW
    WHEN OLD.link_status <> NEW.link_status
      AND NOT (
          (OLD.link_status = 'PENDING_VERIFICATION' AND NEW.link_status = 'ACTIVE')
          OR (OLD.link_status = 'PENDING_VERIFICATION' AND NEW.link_status = 'REVOKED')
          OR (OLD.link_status = 'ACTIVE' AND NEW.link_status = 'REVOKED')
      )
    BEGIN
        SELECT RAISE(ABORT, 'raw_asset_link_state_transition_invalid');
    END
    """,
)


_SESSION_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_upload_session_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'CREATED' THEN
            RAISE EXCEPTION 'upload_session_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSIF OLD.status IS DISTINCT FROM NEW.status
          AND NOT ({_SESSION_PAIRS}) THEN
        RAISE EXCEPTION 'upload_session_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_UPLOAD_DELETE_FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION ddp_upload_session_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'upload_session_delete_forbidden' USING ERRCODE = '23514';
END;
$$;

CREATE OR REPLACE FUNCTION ddp_upload_part_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'upload_part_immutable' USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'upload_part_delete_forbidden' USING ERRCODE = '23514';
END;
$$;

CREATE OR REPLACE FUNCTION ddp_raw_asset_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'raw_asset_delete_forbidden' USING ERRCODE = '23514';
END;
$$;

CREATE OR REPLACE FUNCTION ddp_raw_asset_link_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'raw_asset_link_delete_forbidden' USING ERRCODE = '23514';
END;
$$;
"""

_RAW_ASSET_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_raw_asset_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'VALIDATING' THEN
            RAISE EXCEPTION 'raw_asset_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status
       AND NOT ({_RAW_ASSET_PAIRS}) THEN
        RAISE EXCEPTION 'raw_asset_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;

    IF {_PG_IMMUTABLE_WHEN} THEN
        RAISE EXCEPTION 'raw_asset_content_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_LINK_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_raw_asset_link_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.link_status IS DISTINCT FROM NEW.link_status
       AND NOT (
           (OLD.link_status = 'PENDING_VERIFICATION' AND NEW.link_status = 'ACTIVE')
           OR (OLD.link_status = 'PENDING_VERIFICATION' AND NEW.link_status = 'REVOKED')
           OR (OLD.link_status = 'ACTIVE' AND NEW.link_status = 'REVOKED')
       ) THEN
        RAISE EXCEPTION 'raw_asset_link_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    ("trg_upload_sessions_initial_status", "upload_sessions", "INSERT"),
    ("trg_upload_sessions_transition", "upload_sessions", "UPDATE"),
    ("trg_upload_sessions_no_delete", "upload_sessions", "DELETE"),
    ("trg_upload_parts_no_update", "upload_parts", "UPDATE"),
    ("trg_upload_parts_no_delete", "upload_parts", "DELETE"),
    ("trg_raw_assets_initial_status", "raw_assets", "INSERT"),
    ("trg_raw_assets_transition", "raw_assets", "UPDATE"),
    ("trg_raw_assets_content_immutable", "raw_assets", "UPDATE"),
    ("trg_raw_assets_no_delete", "raw_assets", "DELETE"),
    ("trg_raw_asset_links_no_delete", "raw_asset_links", "DELETE"),
    ("trg_raw_asset_links_terminal_status", "raw_asset_links", "UPDATE"),
)


def _has_tables(connection: Connection) -> bool:
    inspector = inspect(connection)
    return all(
        inspector.has_table(table)
        for table in ("upload_sessions", "upload_parts", "raw_assets", "raw_asset_links")
    )


def install_ingestion_state_guards(connection: Connection) -> None:
    """安装上传会话/分片/RawAsset/使用关系兜底触发器。"""
    if not _has_tables(connection):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_SESSION_FUNCTION_SQL)
        connection.exec_driver_sql(_UPLOAD_DELETE_FUNCTIONS_SQL)
        connection.exec_driver_sql(_RAW_ASSET_FUNCTION_SQL)
        connection.exec_driver_sql(_LINK_FUNCTION_SQL)
        for trigger, table, _event in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in (
            (
                "trg_upload_sessions_initial_status",
                "upload_sessions",
                "INSERT",
                "ddp_upload_session_guard",
            ),
            (
                "trg_upload_sessions_transition",
                "upload_sessions",
                "UPDATE",
                "ddp_upload_session_guard",
            ),
            (
                "trg_upload_sessions_no_delete",
                "upload_sessions",
                "DELETE",
                "ddp_upload_session_delete_guard",
            ),
            ("trg_upload_parts_no_update", "upload_parts", "UPDATE", "ddp_upload_part_guard"),
            ("trg_upload_parts_no_delete", "upload_parts", "DELETE", "ddp_upload_part_guard"),
            ("trg_raw_assets_initial_status", "raw_assets", "INSERT", "ddp_raw_asset_guard"),
            ("trg_raw_assets_transition", "raw_assets", "UPDATE", "ddp_raw_asset_guard"),
            ("trg_raw_assets_content_immutable", "raw_assets", "UPDATE", "ddp_raw_asset_guard"),
            ("trg_raw_assets_no_delete", "raw_assets", "DELETE", "ddp_raw_asset_delete_guard"),
            (
                "trg_raw_asset_links_no_delete",
                "raw_asset_links",
                "DELETE",
                "ddp_raw_asset_link_delete_guard",
            ),
            (
                "trg_raw_asset_links_terminal_status",
                "raw_asset_links",
                "UPDATE",
                "ddp_raw_asset_link_guard",
            ),
        ):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_ingestion_state_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_upload_session_guard",
            "ddp_upload_session_delete_guard",
            "ddp_upload_part_guard",
            "ddp_raw_asset_guard",
            "ddp_raw_asset_delete_guard",
            "ddp_raw_asset_link_guard",
            "ddp_raw_asset_link_delete_guard",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}()")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
