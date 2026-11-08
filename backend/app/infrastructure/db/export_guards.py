"""ExportJob 数据库兜底触发器。

SQLite 用于测试，PostgreSQL 为验收目标；迁移与 ``create_all`` 安装同一份 DDL。
覆盖：初始 QUEUED、合法状态迁移、输入字段不可变、终态产物摘要不可改写、禁删。
"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.datasets.state_machines import (
    EXPORT_ARTIFACT_COLUMNS,
    EXPORT_IMMUTABLE_COLUMNS,
    export_transition_pairs,
)

_TERMINAL_OUTPUT_STATES = "'SUCCEEDED', 'PARTIAL', 'CANCELLED'"
_EXPORT_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(export_transition_pairs())
)
_JSON_COLUMNS = frozenset({"config"})


def _changed(column: str) -> str:
    return (
        f"(CAST(OLD.{column} AS TEXT) <> CAST(NEW.{column} AS TEXT)"
        f" OR (OLD.{column} IS NULL AND NEW.{column} IS NOT NULL)"
        f" OR (OLD.{column} IS NOT NULL AND NEW.{column} IS NULL))"
    )


def _pg_changed(column: str) -> str:
    if column in _JSON_COLUMNS:
        return f"OLD.{column}::text IS DISTINCT FROM NEW.{column}::text"
    return f"OLD.{column} IS DISTINCT FROM NEW.{column}"


def _pg_changed_expression() -> str:
    return " OR ".join(_pg_changed(column) for column in EXPORT_IMMUTABLE_COLUMNS)


def _pg_artifact_changed_expression() -> str:
    return " OR ".join(_pg_changed(column) for column in EXPORT_ARTIFACT_COLUMNS)


_IMMUTABLE = " OR ".join(_changed(column) for column in EXPORT_IMMUTABLE_COLUMNS)
_ARTIFACT = " OR ".join(_changed(column) for column in EXPORT_ARTIFACT_COLUMNS)

_SQLITE_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_export_jobs_initial_status
    BEFORE INSERT ON export_jobs
    FOR EACH ROW
    WHEN NEW.status <> 'QUEUED' OR NEW.attempt <> 0
    BEGIN
        SELECT RAISE(ABORT, 'export_job_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_export_jobs_transition
    BEFORE UPDATE ON export_jobs
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_EXPORT_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'export_job_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_export_jobs_input_immutable
    BEFORE UPDATE ON export_jobs
    FOR EACH ROW
    WHEN ({_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'export_job_input_immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_export_jobs_artifacts_immutable
    BEFORE UPDATE ON export_jobs
    FOR EACH ROW
    WHEN OLD.status IN ({_TERMINAL_OUTPUT_STATES}) AND ({_ARTIFACT})
    BEGIN
        SELECT RAISE(ABORT, 'export_job_artifacts_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_export_jobs_no_delete
    BEFORE DELETE ON export_jobs
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'export_job_delete_forbidden');
    END
    """,
)

_PG_FUNCTION = f"""
CREATE OR REPLACE FUNCTION ddp_export_job_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    immutable_changed boolean;
    artifact_changed boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'QUEUED' OR NEW.attempt <> 0 THEN
            RAISE EXCEPTION 'export_job_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_EXPORT_PAIRS}) THEN
        RAISE EXCEPTION 'export_job_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    SELECT {_pg_changed_expression()} INTO immutable_changed;
    IF immutable_changed THEN
        RAISE EXCEPTION 'export_job_input_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ({_TERMINAL_OUTPUT_STATES}) THEN
        SELECT {_pg_artifact_changed_expression()} INTO artifact_changed;
        IF artifact_changed THEN
            RAISE EXCEPTION 'export_job_artifacts_immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

_DELETE_FUNCTION = """
CREATE OR REPLACE FUNCTION ddp_export_job_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'export_job_delete_forbidden' USING ERRCODE = '23514';
END;
$$;
"""

_TRIGGERS = (
    ("trg_export_jobs_initial_status", "export_jobs", "INSERT", "ddp_export_job_guard"),
    ("trg_export_jobs_transition", "export_jobs", "UPDATE", "ddp_export_job_guard"),
    ("trg_export_jobs_input_immutable", "export_jobs", "UPDATE", "ddp_export_job_guard"),
    ("trg_export_jobs_artifacts_immutable", "export_jobs", "UPDATE", "ddp_export_job_guard"),
    ("trg_export_jobs_no_delete", "export_jobs", "DELETE", "ddp_export_job_delete_guard"),
)


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_export_guards(connection: Connection) -> None:
    if not _has_table(connection, "export_jobs"):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_PG_FUNCTION)
        connection.exec_driver_sql(_DELETE_FUNCTION)
        for trigger, table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in _TRIGGERS:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_export_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in ("ddp_export_job_guard()", "ddp_export_job_delete_guard()"):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
