"""阶段 5 数据库兜底触发器：状态迁移、不可变字段与禁删。

SQLite 用于测试，PostgreSQL 为验收目标；迁移与 ``create_all`` 安装同一份 DDL。
"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

_JSON_COLUMNS = frozenset(
    {
        "artifact",
        "runtime",
        "dataset_version_ids",
        "metrics_summary",
        "metrics",
        "thresholds",
        "report_ref",
        "result_ref",
        "dimensions",
        "evidence_refs",
        "source_feedback_ids",
        "source_evaluation_ids",
        "quality_thresholds",
        "linked_dataset_version_ids",
        "retest_evaluation_ids",
        "config",
        "episode_ids",
    }
)

TRANSITIONS: dict[str, frozenset[tuple[str, str]]] = {
    "models": frozenset(
        {
            ("DRAFT", "ACTIVE"),
            ("ACTIVE", "DEPRECATED"),
            ("ACTIVE", "ARCHIVED"),
            ("DEPRECATED", "ACTIVE"),
            ("DEPRECATED", "ARCHIVED"),
        }
    ),
    "model_versions": frozenset(
        {
            ("DRAFT", "VALIDATING"),
            ("DRAFT", "READY"),
            ("VALIDATING", "READY"),
            ("VALIDATING", "VALIDATION_FAILED"),
            ("VALIDATION_FAILED", "DRAFT"),
            ("READY", "RELEASED"),
            ("RELEASED", "DEPRECATED"),
            ("RELEASED", "ARCHIVED"),
            ("DEPRECATED", "ARCHIVED"),
        }
    ),
    "inference_services": frozenset(
        {
            ("PENDING", "DEPLOYING"),
            ("DEPLOYING", "ACTIVE"),
            ("DEPLOYING", "FAILED"),
            ("FAILED", "PENDING"),
            ("ACTIVE", "STOPPED"),
            ("STOPPED", "DEPLOYING"),
            ("STOPPED", "RETIRED"),
        }
    ),
    "inference_tasks": frozenset(
        {
            ("QUEUED", "RUNNING"),
            ("QUEUED", "SUCCEEDED"),
            ("QUEUED", "FAILED"),
            ("QUEUED", "CANCELLED"),
            ("RUNNING", "SUCCEEDED"),
            ("RUNNING", "FAILED"),
            ("RUNNING", "CANCELLED"),
        }
    ),
    "evaluations": frozenset(
        {
            ("QUEUED", "RUNNING"),
            ("QUEUED", "SUCCEEDED"),
            ("QUEUED", "FAILED"),
            ("QUEUED", "CANCELLED"),
            ("RUNNING", "SUCCEEDED"),
            ("RUNNING", "FAILED"),
            ("RUNNING", "CANCELLED"),
        }
    ),
    "model_feedback": frozenset(
        {
            ("OPEN", "TRIAGED"),
            ("OPEN", "DISMISSED"),
            ("OPEN", "REQUIREMENT_CREATED"),
            ("TRIAGED", "DISMISSED"),
            ("TRIAGED", "REQUIREMENT_CREATED"),
            ("REQUIREMENT_CREATED", "RESOLVED"),
        }
    ),
    "data_requirements": frozenset(
        {
            ("DRAFT", "SUBMITTED"),
            ("DRAFT", "CANCELLED"),
            ("SUBMITTED", "APPROVED"),
            ("SUBMITTED", "REJECTED"),
            ("SUBMITTED", "CANCELLED"),
            ("APPROVED", "PLANNED"),
            ("APPROVED", "SATISFIED"),
            ("APPROVED", "CANCELLED"),
            ("PLANNED", "IN_PROGRESS"),
            ("PLANNED", "CANCELLED"),
            ("IN_PROGRESS", "SATISFIED"),
            ("IN_PROGRESS", "CANCELLED"),
            ("SATISFIED", "CLOSED"),
        }
    ),
}

_INITIAL = {
    "models": "DRAFT",
    "model_versions": "DRAFT",
    "inference_services": "PENDING",
    "inference_tasks": "QUEUED",
    "evaluations": "QUEUED",
    "model_feedback": "OPEN",
    "data_requirements": "DRAFT",
}

_IMMUTABLE_WHEN = {
    "model_versions": (
        "RELEASED",
        (
            "artifact",
            "runtime",
            "artifact_sha256",
            "training_request_id",
            "training_delivery_id",
            "external_ref_id",
            "dataset_version_ids",
        ),
    ),
    "evaluations": (
        "SUCCEEDED",
        ("metrics", "thresholds", "verdict", "dataset_version_id", "model_version_id"),
    ),
    "model_feedback": (
        "REQUIREMENT_CREATED",
        ("model_version_id", "issue_type", "dimensions", "evaluation_id", "inference_task_id"),
    ),
    "data_requirements": (
        "SUBMITTED",
        ("code", "source_type", "dimensions", "source_feedback_ids", "source_evaluation_ids"),
    ),
}

_TABLES = tuple(TRANSITIONS)


def _pairs_sql(pairs: Iterable[tuple[str, str]]) -> str:
    return " OR ".join(
        f"(OLD.status = '{current}' AND NEW.status = '{target}')"
        for current, target in sorted(pairs)
    )


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


def _any_changed_sqlite(columns: tuple[str, ...]) -> str:
    return " OR ".join(_changed(column) for column in columns)


def _any_changed_pg(columns: tuple[str, ...]) -> str:
    return " OR ".join(_pg_changed(column) for column in columns)


def _sqlite_statements() -> tuple[str, ...]:
    statements: list[str] = []
    for table, initial in _INITIAL.items():
        statements.append(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_initial_status
            BEFORE INSERT ON {table}
            FOR EACH ROW
            WHEN NEW.status <> '{initial}'
            BEGIN
                SELECT RAISE(ABORT, '{table}_initial_status_invalid');
            END
            """
        )
        statements.append(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_transition
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            WHEN OLD.status <> NEW.status AND NOT ({_pairs_sql(TRANSITIONS[table])})
            BEGIN
                SELECT RAISE(ABORT, '{table}_state_transition_invalid');
            END
            """
        )
        statements.append(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, '{table}_delete_forbidden');
            END
            """
        )
    for table, (status, columns) in _IMMUTABLE_WHEN.items():
        statements.append(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_immutable
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            WHEN OLD.status = '{status}' AND ({_any_changed_sqlite(columns)})
            BEGIN
                SELECT RAISE(ABORT, '{table}_immutable');
            END
            """
        )
    return tuple(statements)


def _pg_functions() -> tuple[str, ...]:
    functions: list[str] = []
    for table, initial in _INITIAL.items():
        functions.append(
            f"""
CREATE OR REPLACE FUNCTION ddp_{table}_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> '{initial}' THEN
            RAISE EXCEPTION '{table}_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_pairs_sql(TRANSITIONS[table])}) THEN
        RAISE EXCEPTION '{table}_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""
        )
    for table, (status, columns) in _IMMUTABLE_WHEN.items():
        functions.append(
            f"""
CREATE OR REPLACE FUNCTION ddp_{table}_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = '{status}' AND ({_any_changed_pg(columns)}) THEN
        RAISE EXCEPTION '{table}_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""
        )
    functions.append(
        """
CREATE OR REPLACE FUNCTION ddp_modelops_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = TG_TABLE_NAME || '_delete_forbidden',
        ERRCODE = '23514';
END;
$$;
"""
    )
    return tuple(functions)


def _pg_triggers() -> tuple[tuple[str, str, str, str], ...]:
    triggers: list[tuple[str, str, str, str]] = []
    for table in _TABLES:
        triggers.append((f"trg_{table}_initial_status", table, "INSERT", f"ddp_{table}_guard"))
        triggers.append((f"trg_{table}_transition", table, "UPDATE", f"ddp_{table}_guard"))
        triggers.append((f"trg_{table}_no_delete", table, "DELETE", "ddp_modelops_delete_guard"))
    for table in _IMMUTABLE_WHEN:
        triggers.append((f"trg_{table}_immutable", table, "UPDATE", f"ddp_{table}_immutable_guard"))
    return tuple(triggers)


_REQUIRED_TABLES = frozenset(_TABLES)


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_modelops_guards(connection: Connection) -> None:
    if not all(_has_table(connection, name) for name in _REQUIRED_TABLES):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _sqlite_statements():
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in _pg_functions():
            connection.exec_driver_sql(sql)
        for trigger, table, _event, _function in _pg_triggers():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in _pg_triggers():
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_modelops_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _pg_triggers():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _pg_triggers():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            *(f"ddp_{table}_guard()" for table in _TABLES),
            *(f"ddp_{table}_immutable_guard()" for table in _IMMUTABLE_WHEN),
            "ddp_modelops_delete_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
