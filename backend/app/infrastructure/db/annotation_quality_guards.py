"""黄金集、抽检与 IAA 报告数据库兜底触发器。"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.annotation.state_machines import (
    gold_set_transition_pairs,
    iaa_report_transition_pairs,
    quality_sample_transition_pairs,
)

_GOLD_SET_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(gold_set_transition_pairs())
)
_QUALITY_SAMPLE_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(quality_sample_transition_pairs())
)
_IAA_REPORT_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(iaa_report_transition_pairs())
)


def _changed(column: str) -> str:
    # JSON/JSONB 在 PostgreSQL 无通用 <> 运算符；统一按文本比较，
    # SQLite 的 JSON 以 TEXT 存储，两端语义一致。
    return (
        f"(CAST(OLD.{column} AS TEXT) <> CAST(NEW.{column} AS TEXT)"
        f" OR (OLD.{column} IS NULL AND NEW.{column} IS NOT NULL)"
        f" OR (OLD.{column} IS NOT NULL AND NEW.{column} IS NULL))"
    )


_GOLD_SET_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "schema_id",
        "schema_version",
        "schema_sha256",
        "blind",
        "items",
        "exposure_policy",
        "max_exposure_per_annotator",
        "rotation_policy",
        "usage_policy",
    )
)
_QUALITY_SAMPLE_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "task_id",
        "submission_id",
        "annotator_id",
        "sampling_policy_version",
        "sample_type",
        "stratum",
        "sample_reason",
        "item_refs",
    )
)
_IAA_REPORT_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "task_id",
        "schema_id",
        "schema_version",
        "metric_definitions_version",
        "sample_item_count",
        "overlap_item_count",
        "annotator_count",
        "excluded_items",
        "metrics",
    )
)

_SQLITE_QUALITY_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_sets_initial_status
    BEFORE INSERT ON annotation_gold_sets
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_sets_transition
    BEFORE UPDATE ON annotation_gold_sets
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_GOLD_SET_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_sets_immutable
    BEFORE UPDATE ON annotation_gold_sets
    FOR EACH ROW
    WHEN OLD.status IN ('ACTIVE', 'RETIRED', 'REVOKED') AND ({_GOLD_SET_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_sets_no_delete
    BEFORE DELETE ON annotation_gold_sets
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_set_exposures_append_only
    BEFORE UPDATE ON annotation_gold_set_exposures
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_exposure_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_set_exposures_no_delete
    BEFORE DELETE ON annotation_gold_set_exposures
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_exposure_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_set_evaluations_append_only
    BEFORE UPDATE ON annotation_gold_set_evaluations
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_evaluation_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_gold_set_evaluations_no_delete
    BEFORE DELETE ON annotation_gold_set_evaluations
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_gold_set_evaluation_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_quality_samples_initial_status
    BEFORE INSERT ON annotation_quality_samples
    FOR EACH ROW
    WHEN NEW.status <> 'PLANNED'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_quality_sample_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_quality_samples_transition
    BEFORE UPDATE ON annotation_quality_samples
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_QUALITY_SAMPLE_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_quality_sample_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_quality_samples_immutable
    BEFORE UPDATE ON annotation_quality_samples
    FOR EACH ROW
    WHEN OLD.status IN ('CLOSED', 'CANCELLED') AND ({_QUALITY_SAMPLE_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_quality_sample_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_quality_samples_no_delete
    BEFORE DELETE ON annotation_quality_samples
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_quality_sample_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_iaa_reports_initial_status
    BEFORE INSERT ON annotation_iaa_reports
    FOR EACH ROW
    WHEN NEW.status <> 'QUEUED'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_iaa_report_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_iaa_reports_transition
    BEFORE UPDATE ON annotation_iaa_reports
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_IAA_REPORT_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_iaa_report_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_iaa_reports_immutable
    BEFORE UPDATE ON annotation_iaa_reports
    FOR EACH ROW
    WHEN OLD.status IN ('SUCCEEDED', 'INSUFFICIENT_DATA', 'CANCELLED')
      AND ({_IAA_REPORT_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_iaa_report_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_iaa_reports_no_delete
    BEFORE DELETE ON annotation_iaa_reports
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_iaa_report_delete_forbidden');
    END
    """,
)

_GOLD_SET_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_gold_set_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'annotation_gold_set_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_GOLD_SET_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_gold_set_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('ACTIVE', 'RETIRED', 'REVOKED') AND ({_GOLD_SET_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_gold_set_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_QUALITY_SAMPLE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_quality_sample_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PLANNED' THEN
            RAISE EXCEPTION 'annotation_quality_sample_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_QUALITY_SAMPLE_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_quality_sample_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('CLOSED', 'CANCELLED') AND ({_QUALITY_SAMPLE_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_quality_sample_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_IAA_REPORT_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_iaa_report_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'QUEUED' THEN
            RAISE EXCEPTION 'annotation_iaa_report_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_IAA_REPORT_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_iaa_report_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('SUCCEEDED', 'INSUFFICIENT_DATA', 'CANCELLED')
       AND ({_IAA_REPORT_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_iaa_report_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_annotation_quality_append_only_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || '_append_only', ERRCODE = '23514';
END;
$$;
"""

_TRIGGER_TABLES = (
    (
        "trg_annotation_gold_sets_initial_status",
        "annotation_gold_sets",
        "INSERT",
        "ddp_annotation_gold_set_guard",
    ),
    (
        "trg_annotation_gold_sets_transition",
        "annotation_gold_sets",
        "UPDATE",
        "ddp_annotation_gold_set_guard",
    ),
    (
        "trg_annotation_gold_sets_immutable",
        "annotation_gold_sets",
        "UPDATE",
        "ddp_annotation_gold_set_guard",
    ),
    (
        "trg_annotation_gold_sets_no_delete",
        "annotation_gold_sets",
        "DELETE",
        "ddp_annotation_delete_guard",
    ),
    (
        "trg_annotation_gold_set_exposures_append_only",
        "annotation_gold_set_exposures",
        "UPDATE",
        "ddp_annotation_quality_append_only_guard",
    ),
    (
        "trg_annotation_gold_set_exposures_no_delete",
        "annotation_gold_set_exposures",
        "DELETE",
        "ddp_annotation_quality_append_only_guard",
    ),
    (
        "trg_annotation_gold_set_evaluations_append_only",
        "annotation_gold_set_evaluations",
        "UPDATE",
        "ddp_annotation_quality_append_only_guard",
    ),
    (
        "trg_annotation_gold_set_evaluations_no_delete",
        "annotation_gold_set_evaluations",
        "DELETE",
        "ddp_annotation_quality_append_only_guard",
    ),
    (
        "trg_annotation_quality_samples_initial_status",
        "annotation_quality_samples",
        "INSERT",
        "ddp_annotation_quality_sample_guard",
    ),
    (
        "trg_annotation_quality_samples_transition",
        "annotation_quality_samples",
        "UPDATE",
        "ddp_annotation_quality_sample_guard",
    ),
    (
        "trg_annotation_quality_samples_immutable",
        "annotation_quality_samples",
        "UPDATE",
        "ddp_annotation_quality_sample_guard",
    ),
    (
        "trg_annotation_quality_samples_no_delete",
        "annotation_quality_samples",
        "DELETE",
        "ddp_annotation_delete_guard",
    ),
    (
        "trg_annotation_iaa_reports_initial_status",
        "annotation_iaa_reports",
        "INSERT",
        "ddp_annotation_iaa_report_guard",
    ),
    (
        "trg_annotation_iaa_reports_transition",
        "annotation_iaa_reports",
        "UPDATE",
        "ddp_annotation_iaa_report_guard",
    ),
    (
        "trg_annotation_iaa_reports_immutable",
        "annotation_iaa_reports",
        "UPDATE",
        "ddp_annotation_iaa_report_guard",
    ),
    (
        "trg_annotation_iaa_reports_no_delete",
        "annotation_iaa_reports",
        "DELETE",
        "ddp_annotation_delete_guard",
    ),
)


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _ensure_delete_guard(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION ddp_annotation_delete_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || '_delete_forbidden', ERRCODE = '23514';
        END;
        $$;
        """
    )


def install_quality_guards(connection: Connection) -> None:
    required = {
        "annotation_gold_sets",
        "annotation_gold_set_exposures",
        "annotation_gold_set_evaluations",
        "annotation_quality_samples",
        "annotation_iaa_reports",
    }
    if not all(_has_table(connection, name) for name in required):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_QUALITY_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in (
            _GOLD_SET_FUNCTION_SQL,
            _QUALITY_SAMPLE_FUNCTION_SQL,
            _IAA_REPORT_FUNCTION_SQL,
            _APPEND_ONLY_FUNCTION_SQL,
        ):
            connection.exec_driver_sql(sql)
        _ensure_delete_guard(connection)
        for trigger, table, _event, _function in _TRIGGER_TABLES:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in _TRIGGER_TABLES:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_quality_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _TRIGGER_TABLES:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _TRIGGER_TABLES:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_annotation_gold_set_guard()",
            "ddp_annotation_quality_sample_guard()",
            "ddp_annotation_iaa_report_guard()",
            "ddp_annotation_quality_append_only_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
