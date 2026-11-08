"""训练需求/交付/外部引用/授权数据库兜底触发器。

SQLite 用于测试，PostgreSQL 为验收目标；迁移与 ``create_all`` 安装同一份 DDL。
覆盖：初始状态、合法迁移、内容不可变、终态不可回退、禁止物理删除。
回调防重放表允许按窗口清理，不安装禁删触发器。
"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.datasets.training_state_machines import (
    DELIVERY_IMMUTABLE_COLUMNS,
    EXTERNAL_IMMUTABLE_COLUMNS,
    GRANT_IMMUTABLE_COLUMNS,
    REQUEST_IMMUTABLE_COLUMNS,
    REVISION_APPROVAL_COLUMNS,
    REVISION_IMMUTABLE_COLUMNS,
    delivery_terminal_states,
    delivery_transition_pairs,
    external_terminal_states,
    external_transition_pairs,
    grant_terminal_states,
    grant_transition_pairs,
    request_transition_pairs,
    revision_transition_pairs,
)

_JSON_COLUMNS = frozenset({"snapshot", "split_counts", "scope"})


def _pairs_sql(
    pairs: frozenset[tuple[str, str]],
    *,
    column: str = "status",
) -> str:
    return " OR ".join(
        f"(OLD.{column} = '{current}' AND NEW.{column} = '{target}')"
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


def _terminal_sql(states: frozenset[str]) -> str:
    return ", ".join(f"'{state}'" for state in sorted(states))


def _insert_initial_sqlite(table: str, status: str, message: str) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_initial_status
    BEFORE INSERT ON {table}
    FOR EACH ROW
    WHEN NEW.status <> '{status}'
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """


def _transition_sqlite(
    table: str,
    pairs: str,
    message: str,
    *,
    column: str = "status",
) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_transition
    BEFORE UPDATE ON {table}
    FOR EACH ROW
    WHEN OLD.{column} <> NEW.{column} AND NOT ({pairs})
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """


def _immutable_sqlite(table: str, columns: tuple[str, ...], message: str) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_immutable
    BEFORE UPDATE ON {table}
    FOR EACH ROW
    WHEN ({_any_changed_sqlite(columns)})
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """


def _terminal_sqlite(table: str, states: frozenset[str], message: str) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_terminal_immutable
    BEFORE UPDATE ON {table}
    FOR EACH ROW
    WHEN OLD.status IN ({_terminal_sql(states)})
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """


def _no_delete_sqlite(table: str, message: str) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
    BEFORE DELETE ON {table}
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """


_REQUEST_PAIRS = _pairs_sql(request_transition_pairs())
_REVISION_PAIRS = _pairs_sql(revision_transition_pairs())
_DELIVERY_PAIRS = _pairs_sql(delivery_transition_pairs())
_EXTERNAL_PAIRS = _pairs_sql(external_transition_pairs(), column="external_status")
_GRANT_PAIRS = _pairs_sql(grant_transition_pairs())
_REQUEST_TERMINAL_SQL = _terminal_sql(frozenset({"CLOSED", "CANCELLED"}))
_REVISION_TERMINAL_SQL = _terminal_sql(frozenset({"SUPERSEDED"}))
_DELIVERY_TERMINAL_SQL = _terminal_sql(delivery_terminal_states())
_EXTERNAL_TERMINAL_SQL = _terminal_sql(external_terminal_states())
_GRANT_TERMINAL_SQL = _terminal_sql(grant_terminal_states())

_SQLITE_STATEMENTS = (
    _insert_initial_sqlite("training_requests", "DRAFT", "training_request_initial_status_invalid"),
    _transition_sqlite(
        "training_requests", _REQUEST_PAIRS, "training_request_state_transition_invalid"
    ),
    _immutable_sqlite(
        "training_requests",
        REQUEST_IMMUTABLE_COLUMNS,
        "training_request_identity_immutable",
    ),
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_training_requests_terminal_immutable
    BEFORE UPDATE ON training_requests
    FOR EACH ROW
    WHEN OLD.status IN ({_REQUEST_TERMINAL_SQL})
    BEGIN
        SELECT RAISE(ABORT, 'training_request_terminal_immutable');
    END
    """,
    _no_delete_sqlite("training_requests", "training_request_delete_forbidden"),
    _insert_initial_sqlite(
        "training_request_revisions", "DRAFT", "training_request_revision_initial_status_invalid"
    ),
    _transition_sqlite(
        "training_request_revisions",
        _REVISION_PAIRS,
        "training_request_revision_state_transition_invalid",
    ),
    _immutable_sqlite(
        "training_request_revisions",
        REVISION_IMMUTABLE_COLUMNS,
        "training_request_revision_immutable",
    ),
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_training_request_revisions_decision_immutable
    BEFORE UPDATE ON training_request_revisions
    FOR EACH ROW
    WHEN OLD.status IN ('APPROVED', 'REJECTED')
      AND ({_any_changed_sqlite(REVISION_APPROVAL_COLUMNS)})
    BEGIN
        SELECT RAISE(ABORT, 'training_request_revision_decision_immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_training_request_revisions_terminal_immutable
    BEFORE UPDATE ON training_request_revisions
    FOR EACH ROW
    WHEN OLD.status IN ({_REVISION_TERMINAL_SQL})
    BEGIN
        SELECT RAISE(ABORT, 'training_request_revision_terminal_immutable');
    END
    """,
    _no_delete_sqlite("training_request_revisions", "training_request_revision_delete_forbidden"),
    _insert_initial_sqlite(
        "training_deliveries", "DRAFT", "training_delivery_initial_status_invalid"
    ),
    _transition_sqlite(
        "training_deliveries", _DELIVERY_PAIRS, "training_delivery_state_transition_invalid"
    ),
    _immutable_sqlite(
        "training_deliveries", DELIVERY_IMMUTABLE_COLUMNS, "training_delivery_reference_immutable"
    ),
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_training_deliveries_terminal_immutable
    BEFORE UPDATE ON training_deliveries
    FOR EACH ROW
    WHEN OLD.status IN ({_DELIVERY_TERMINAL_SQL})
    BEGIN
        SELECT RAISE(ABORT, 'training_delivery_terminal_immutable');
    END
    """,
    _no_delete_sqlite("training_deliveries", "training_delivery_delete_forbidden"),
    _insert_initial_sqlite(
        "external_training_references",
        "SUBMITTED",
        "external_training_reference_initial_status_invalid",
    ),
    _transition_sqlite(
        "external_training_references",
        _EXTERNAL_PAIRS,
        "external_training_reference_state_transition_invalid",
        column="external_status",
    ),
    _immutable_sqlite(
        "external_training_references",
        EXTERNAL_IMMUTABLE_COLUMNS,
        "external_training_reference_identity_immutable",
    ),
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_external_training_references_terminal_immutable
    BEFORE UPDATE ON external_training_references
    FOR EACH ROW
    WHEN OLD.external_status IN ({_EXTERNAL_TERMINAL_SQL})
    BEGIN
        SELECT RAISE(ABORT, 'external_training_reference_terminal_immutable');
    END
    """,
    _no_delete_sqlite(
        "external_training_references", "external_training_reference_delete_forbidden"
    ),
    _insert_initial_sqlite(
        "dataset_grants", "PENDING_APPROVAL", "dataset_grant_initial_status_invalid"
    ),
    _transition_sqlite("dataset_grants", _GRANT_PAIRS, "dataset_grant_state_transition_invalid"),
    _immutable_sqlite(
        "dataset_grants", GRANT_IMMUTABLE_COLUMNS, "dataset_grant_identity_immutable"
    ),
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_dataset_grants_terminal_immutable
    BEFORE UPDATE ON dataset_grants
    FOR EACH ROW
    WHEN OLD.status IN ({_GRANT_TERMINAL_SQL})
    BEGIN
        SELECT RAISE(ABORT, 'dataset_grant_terminal_immutable');
    END
    """,
    _no_delete_sqlite("dataset_grants", "dataset_grant_delete_forbidden"),
)


def _pg_function(name: str, body: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION {name}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
{body}
END;
$$;
"""


_REQUEST_FUNCTION = _pg_function(
    "ddp_training_request_guard",
    f"""    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'training_request_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_REQUEST_PAIRS}) THEN
        RAISE EXCEPTION 'training_request_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF ({_any_changed_pg(REQUEST_IMMUTABLE_COLUMNS)}) THEN
        RAISE EXCEPTION 'training_request_identity_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ({_REQUEST_TERMINAL_SQL}) THEN
        RAISE EXCEPTION 'training_request_terminal_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;""",
)

_REVISION_FUNCTION = _pg_function(
    "ddp_training_request_revision_guard",
    f"""    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'training_request_revision_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_REVISION_PAIRS}) THEN
        RAISE EXCEPTION 'training_request_revision_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF ({_any_changed_pg(REVISION_IMMUTABLE_COLUMNS)}) THEN
        RAISE EXCEPTION 'training_request_revision_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('APPROVED', 'REJECTED')
       AND ({_any_changed_pg(REVISION_APPROVAL_COLUMNS)}) THEN
        RAISE EXCEPTION 'training_request_revision_decision_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ({_REVISION_TERMINAL_SQL}) THEN
        RAISE EXCEPTION 'training_request_revision_terminal_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;""",
)

_DELIVERY_FUNCTION = _pg_function(
    "ddp_training_delivery_guard",
    f"""    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'training_delivery_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_DELIVERY_PAIRS}) THEN
        RAISE EXCEPTION 'training_delivery_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF ({_any_changed_pg(DELIVERY_IMMUTABLE_COLUMNS)}) THEN
        RAISE EXCEPTION 'training_delivery_reference_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ({_DELIVERY_TERMINAL_SQL}) THEN
        RAISE EXCEPTION 'training_delivery_terminal_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;""",
)

_EXTERNAL_FUNCTION = _pg_function(
    "ddp_external_training_reference_guard",
    f"""    IF TG_OP = 'INSERT' THEN
        IF NEW.external_status <> 'SUBMITTED' THEN
            RAISE EXCEPTION 'external_training_reference_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.external_status IS DISTINCT FROM NEW.external_status AND NOT ({_EXTERNAL_PAIRS}) THEN
        RAISE EXCEPTION 'external_training_reference_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF ({_any_changed_pg(EXTERNAL_IMMUTABLE_COLUMNS)}) THEN
        RAISE EXCEPTION 'external_training_reference_identity_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.external_status IN ({_EXTERNAL_TERMINAL_SQL}) THEN
        RAISE EXCEPTION 'external_training_reference_terminal_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;""",
)

_GRANT_FUNCTION = _pg_function(
    "ddp_dataset_grant_guard",
    f"""    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING_APPROVAL' THEN
            RAISE EXCEPTION 'dataset_grant_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_GRANT_PAIRS}) THEN
        RAISE EXCEPTION 'dataset_grant_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF ({_any_changed_pg(GRANT_IMMUTABLE_COLUMNS)}) THEN
        RAISE EXCEPTION 'dataset_grant_identity_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ({_GRANT_TERMINAL_SQL}) THEN
        RAISE EXCEPTION 'dataset_grant_terminal_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;""",
)

_DELETE_FUNCTION = """
CREATE OR REPLACE FUNCTION ddp_training_delete_guard()
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

_FUNCTIONS = (
    _REQUEST_FUNCTION,
    _REVISION_FUNCTION,
    _DELIVERY_FUNCTION,
    _EXTERNAL_FUNCTION,
    _GRANT_FUNCTION,
    _DELETE_FUNCTION,
)

_TRIGGERS = (
    (
        "trg_training_requests_initial_status",
        "training_requests",
        "INSERT",
        "ddp_training_request_guard",
    ),
    (
        "trg_training_requests_transition",
        "training_requests",
        "UPDATE",
        "ddp_training_request_guard",
    ),
    (
        "trg_training_requests_immutable",
        "training_requests",
        "UPDATE",
        "ddp_training_request_guard",
    ),
    (
        "trg_training_requests_terminal_immutable",
        "training_requests",
        "UPDATE",
        "ddp_training_request_guard",
    ),
    ("trg_training_requests_no_delete", "training_requests", "DELETE", "ddp_training_delete_guard"),
    (
        "trg_training_request_revisions_initial_status",
        "training_request_revisions",
        "INSERT",
        "ddp_training_request_revision_guard",
    ),
    (
        "trg_training_request_revisions_transition",
        "training_request_revisions",
        "UPDATE",
        "ddp_training_request_revision_guard",
    ),
    (
        "trg_training_request_revisions_immutable",
        "training_request_revisions",
        "UPDATE",
        "ddp_training_request_revision_guard",
    ),
    (
        "trg_training_request_revisions_decision_immutable",
        "training_request_revisions",
        "UPDATE",
        "ddp_training_request_revision_guard",
    ),
    (
        "trg_training_request_revisions_terminal_immutable",
        "training_request_revisions",
        "UPDATE",
        "ddp_training_request_revision_guard",
    ),
    (
        "trg_training_request_revisions_no_delete",
        "training_request_revisions",
        "DELETE",
        "ddp_training_delete_guard",
    ),
    (
        "trg_training_deliveries_initial_status",
        "training_deliveries",
        "INSERT",
        "ddp_training_delivery_guard",
    ),
    (
        "trg_training_deliveries_transition",
        "training_deliveries",
        "UPDATE",
        "ddp_training_delivery_guard",
    ),
    (
        "trg_training_deliveries_immutable",
        "training_deliveries",
        "UPDATE",
        "ddp_training_delivery_guard",
    ),
    (
        "trg_training_deliveries_terminal_immutable",
        "training_deliveries",
        "UPDATE",
        "ddp_training_delivery_guard",
    ),
    (
        "trg_training_deliveries_no_delete",
        "training_deliveries",
        "DELETE",
        "ddp_training_delete_guard",
    ),
    (
        "trg_external_training_references_initial_status",
        "external_training_references",
        "INSERT",
        "ddp_external_training_reference_guard",
    ),
    (
        "trg_external_training_references_transition",
        "external_training_references",
        "UPDATE",
        "ddp_external_training_reference_guard",
    ),
    (
        "trg_external_training_references_immutable",
        "external_training_references",
        "UPDATE",
        "ddp_external_training_reference_guard",
    ),
    (
        "trg_external_training_references_terminal_immutable",
        "external_training_references",
        "UPDATE",
        "ddp_external_training_reference_guard",
    ),
    (
        "trg_external_training_references_no_delete",
        "external_training_references",
        "DELETE",
        "ddp_training_delete_guard",
    ),
    ("trg_dataset_grants_initial_status", "dataset_grants", "INSERT", "ddp_dataset_grant_guard"),
    ("trg_dataset_grants_transition", "dataset_grants", "UPDATE", "ddp_dataset_grant_guard"),
    ("trg_dataset_grants_immutable", "dataset_grants", "UPDATE", "ddp_dataset_grant_guard"),
    (
        "trg_dataset_grants_terminal_immutable",
        "dataset_grants",
        "UPDATE",
        "ddp_dataset_grant_guard",
    ),
    ("trg_dataset_grants_no_delete", "dataset_grants", "DELETE", "ddp_training_delete_guard"),
)

_REQUIRED_TABLES = frozenset(table for _trigger, table, _event, _function in _TRIGGERS)


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_training_guards(connection: Connection) -> None:
    if not all(_has_table(connection, name) for name in _REQUIRED_TABLES):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in _FUNCTIONS:
            connection.exec_driver_sql(sql)
        for trigger, table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in _TRIGGERS:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_training_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_training_request_guard()",
            "ddp_training_request_revision_guard()",
            "ddp_training_delivery_guard()",
            "ddp_external_training_reference_guard()",
            "ddp_dataset_grant_guard()",
            "ddp_training_delete_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
