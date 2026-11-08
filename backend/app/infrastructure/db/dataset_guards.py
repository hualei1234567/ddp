"""数据集域数据库兜底触发器。

SQLite 用于测试，PostgreSQL 为验收目标；迁移与 ``create_all`` 安装同一份 DDL。
覆盖：合法状态迁移、validity_status 分离迁移、冻结不可变、条目/报告/策略只追加、
全部数据集对象禁删。禁止用后台脚本直写绕过领域动作。
"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.datasets.state_machines import (
    FROZEN_IMMUTABLE_COLUMNS,
    dataset_transition_pairs,
    validity_transition_pairs,
    version_transition_pairs,
)


def _pairs_sql(pairs: frozenset[tuple[str, str]]) -> str:
    return " OR ".join(
        f"(OLD.status = '{current}' AND NEW.status = '{target}')"
        for current, target in sorted(pairs)
    )


def _validity_pairs_sql(pairs: frozenset[tuple[str, str]]) -> str:
    return " OR ".join(
        f"(OLD.validity_status = '{current}' AND NEW.validity_status = '{target}')"
        for current, target in sorted(pairs)
    )


def _changed(column: str) -> str:
    return (
        f"(CAST(OLD.{column} AS TEXT) <> CAST(NEW.{column} AS TEXT)"
        f" OR (OLD.{column} IS NULL AND NEW.{column} IS NOT NULL)"
        f" OR (OLD.{column} IS NOT NULL AND NEW.{column} IS NULL))"
    )


_DATASET_PAIRS = _pairs_sql(dataset_transition_pairs())
_VERSION_PAIRS = _pairs_sql(version_transition_pairs())
_VALIDITY_PAIRS = _validity_pairs_sql(validity_transition_pairs())
_VERSION_IMMUTABLE = " OR ".join(_changed(column) for column in FROZEN_IMMUTABLE_COLUMNS)
_DATASET_IMMUTABLE = " OR ".join(
    _changed(column) for column in ("code", "dataset_lineage_id", "owner_id")
)


def _append_only_sqlite(table: str, message: str) -> tuple[str, str]:
    return (
        f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
    BEFORE UPDATE ON {table}
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """,
        f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
    BEFORE DELETE ON {table}
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, '{message}');
    END
    """,
    )


_SQLITE_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_datasets_initial_status
    BEFORE INSERT ON datasets
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'dataset_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_datasets_transition
    BEFORE UPDATE ON datasets
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_DATASET_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'dataset_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_datasets_identity_immutable
    BEFORE UPDATE ON datasets
    FOR EACH ROW
    WHEN OLD.status <> 'DRAFT' AND ({_DATASET_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'dataset_identity_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_datasets_no_delete
    BEFORE DELETE ON datasets
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'dataset_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_dataset_versions_initial_status
    BEFORE INSERT ON dataset_versions
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT' OR NEW.validity_status <> 'VALID'
    BEGIN
        SELECT RAISE(ABORT, 'dataset_version_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_dataset_versions_transition
    BEFORE UPDATE ON dataset_versions
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_VERSION_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'dataset_version_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_dataset_versions_validity_transition
    BEFORE UPDATE ON dataset_versions
    FOR EACH ROW
    WHEN OLD.validity_status <> NEW.validity_status
      AND NOT ({_VALIDITY_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'dataset_version_validity_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_dataset_versions_immutable
    BEFORE UPDATE ON dataset_versions
    FOR EACH ROW
    WHEN OLD.status IN ('FROZEN', 'SUPERSEDED', 'ARCHIVED', 'CANCELLED')
      AND ({_VERSION_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'dataset_version_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_dataset_versions_no_delete
    BEFORE DELETE ON dataset_versions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'dataset_version_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_dataset_items_no_update
    BEFORE UPDATE ON dataset_items
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'dataset_item_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_dataset_items_no_delete
    BEFORE DELETE ON dataset_items
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'dataset_item_delete_forbidden');
    END
    """,
    *_append_only_sqlite("dataset_dedup_policies", "dataset_dedup_policy_append_only"),
    *_append_only_sqlite("dataset_split_policies", "dataset_split_policy_append_only"),
    *_append_only_sqlite("dataset_leakage_policies", "dataset_leakage_policy_append_only"),
    *_append_only_sqlite("dataset_quality_policies", "dataset_quality_policy_append_only"),
    *_append_only_sqlite("dataset_leakage_reports", "dataset_leakage_report_append_only"),
    *_append_only_sqlite("dataset_quality_reports", "dataset_quality_report_append_only"),
)

_DATASET_FUNCTION = f"""
CREATE OR REPLACE FUNCTION ddp_dataset_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'dataset_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_DATASET_PAIRS}) THEN
        RAISE EXCEPTION 'dataset_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'DRAFT' AND ({_DATASET_IMMUTABLE}) THEN
        RAISE EXCEPTION 'dataset_identity_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_VERSION_FUNCTION = f"""
CREATE OR REPLACE FUNCTION ddp_dataset_version_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' OR NEW.validity_status <> 'VALID' THEN
            RAISE EXCEPTION 'dataset_version_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_VERSION_PAIRS}) THEN
        RAISE EXCEPTION 'dataset_version_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.validity_status IS DISTINCT FROM NEW.validity_status
       AND NOT ({_VALIDITY_PAIRS}) THEN
        RAISE EXCEPTION 'dataset_version_validity_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('FROZEN', 'SUPERSEDED', 'ARCHIVED', 'CANCELLED')
       AND ({_VERSION_IMMUTABLE}) THEN
        RAISE EXCEPTION 'dataset_version_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION ddp_dataset_append_only_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = TG_TABLE_NAME || '_append_only',
        ERRCODE = '23514';
END;
$$;
"""

_DELETE_FUNCTION = """
CREATE OR REPLACE FUNCTION ddp_dataset_delete_guard()
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

_TRIGGERS = (
    ("trg_datasets_initial_status", "datasets", "INSERT", "ddp_dataset_guard"),
    ("trg_datasets_transition", "datasets", "UPDATE", "ddp_dataset_guard"),
    ("trg_datasets_identity_immutable", "datasets", "UPDATE", "ddp_dataset_guard"),
    ("trg_datasets_no_delete", "datasets", "DELETE", "ddp_dataset_delete_guard"),
    ("trg_dataset_versions_initial_status", "dataset_versions", "INSERT", "ddp_dataset_version_guard"),
    ("trg_dataset_versions_transition", "dataset_versions", "UPDATE", "ddp_dataset_version_guard"),
    (
        "trg_dataset_versions_validity_transition",
        "dataset_versions",
        "UPDATE",
        "ddp_dataset_version_guard",
    ),
    ("trg_dataset_versions_immutable", "dataset_versions", "UPDATE", "ddp_dataset_version_guard"),
    ("trg_dataset_versions_no_delete", "dataset_versions", "DELETE", "ddp_dataset_delete_guard"),
    ("trg_dataset_items_no_update", "dataset_items", "UPDATE", "ddp_dataset_append_only_guard"),
    ("trg_dataset_items_no_delete", "dataset_items", "DELETE", "ddp_dataset_delete_guard"),
    (
        "trg_dataset_dedup_policies_no_update",
        "dataset_dedup_policies",
        "UPDATE",
        "ddp_dataset_append_only_guard",
    ),
    (
        "trg_dataset_dedup_policies_no_delete",
        "dataset_dedup_policies",
        "DELETE",
        "ddp_dataset_delete_guard",
    ),
    (
        "trg_dataset_split_policies_no_update",
        "dataset_split_policies",
        "UPDATE",
        "ddp_dataset_append_only_guard",
    ),
    (
        "trg_dataset_split_policies_no_delete",
        "dataset_split_policies",
        "DELETE",
        "ddp_dataset_delete_guard",
    ),
    (
        "trg_dataset_leakage_policies_no_update",
        "dataset_leakage_policies",
        "UPDATE",
        "ddp_dataset_append_only_guard",
    ),
    (
        "trg_dataset_leakage_policies_no_delete",
        "dataset_leakage_policies",
        "DELETE",
        "ddp_dataset_delete_guard",
    ),
    (
        "trg_dataset_quality_policies_no_update",
        "dataset_quality_policies",
        "UPDATE",
        "ddp_dataset_append_only_guard",
    ),
    (
        "trg_dataset_quality_policies_no_delete",
        "dataset_quality_policies",
        "DELETE",
        "ddp_dataset_delete_guard",
    ),
    (
        "trg_dataset_leakage_reports_no_update",
        "dataset_leakage_reports",
        "UPDATE",
        "ddp_dataset_append_only_guard",
    ),
    (
        "trg_dataset_leakage_reports_no_delete",
        "dataset_leakage_reports",
        "DELETE",
        "ddp_dataset_delete_guard",
    ),
    (
        "trg_dataset_quality_reports_no_update",
        "dataset_quality_reports",
        "UPDATE",
        "ddp_dataset_append_only_guard",
    ),
    (
        "trg_dataset_quality_reports_no_delete",
        "dataset_quality_reports",
        "DELETE",
        "ddp_dataset_delete_guard",
    ),
)

_REQUIRED_TABLES = frozenset(table for _trigger, table, _event, _function in _TRIGGERS)


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_dataset_guards(connection: Connection) -> None:
    if not all(_has_table(connection, name) for name in _REQUIRED_TABLES):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in (
            _DATASET_FUNCTION,
            _VERSION_FUNCTION,
            _APPEND_ONLY_FUNCTION,
            _DELETE_FUNCTION,
        ):
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


def drop_dataset_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_dataset_guard()",
            "ddp_dataset_version_guard()",
            "ddp_dataset_append_only_guard()",
            "ddp_dataset_delete_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
