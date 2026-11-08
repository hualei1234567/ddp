"""提交、记录、复核与仲裁数据库兜底触发器。

SQLite 用于测试，PostgreSQL 为验收目标；迁移与 ``create_all`` 安装同一份 DDL。
"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.annotation.state_machines import (
    arbitration_transition_pairs,
    record_transition_pairs,
    review_transition_pairs,
    submission_transition_pairs,
)

_SUBMISSION_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(submission_transition_pairs())
)
_RECORD_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(record_transition_pairs())
)
_REVIEW_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(review_transition_pairs())
)
_ARBITRATION_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(arbitration_transition_pairs())
)


def _changed(column: str) -> str:
    return (
        f"(OLD.{column} <> NEW.{column}"
        f" OR (OLD.{column} IS NULL AND NEW.{column} IS NOT NULL)"
        f" OR (OLD.{column} IS NOT NULL AND NEW.{column} IS NULL))"
    )


_SUBMISSION_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "task_id",
        "batch_id",
        "assignment_id",
        "annotator_id",
        "submission_version",
        "schema_id",
        "schema_version",
        "schema_sha256",
        "item_count",
        "records_digest",
        "data_source",
        "record_refs",
        "supersedes_id",
    )
)
_RECORD_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "submission_id",
        "task_id",
        "episode_id",
        "episode_version",
        "annotation_lineage_id",
        "annotation_version",
        "schema_id",
        "schema_version",
        "time_range",
        "labels",
        "evidence_refs",
        "record_sha256",
        "supersedes_id",
    )
)
_REVIEW_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "submission_id",
        "task_id",
        "reviewer_id",
        "review_round",
        "decision",
        "issues",
        "checklist",
        "reason",
    )
)
_ARBITRATION_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "task_id",
        "submission_id",
        "review_id",
        "arbiter_ids",
        "quorum",
        "trigger",
        "votes",
        "final_decision",
        "reason",
    )
)

_SUBMISSION_SNAPSHOT_CHECK = """
    NOT EXISTS (
        SELECT 1 FROM annotation_tasks t
        WHERE t.project_id = NEW.project_id
          AND t.id = NEW.task_id
          AND t.schema_id = NEW.schema_id
          AND t.schema_version = NEW.schema_version
          AND t.schema_sha256 = NEW.schema_sha256
    )
    OR COALESCE((
        SELECT status FROM annotation_tasks
        WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') = 'CANCELLED'
"""

_REVIEW_SELF_CHECK = """
    EXISTS (
        SELECT 1 FROM annotation_submissions s
        WHERE s.project_id = NEW.project_id
          AND s.id = NEW.submission_id
          AND s.annotator_id = NEW.reviewer_id
    )
"""

_ARBITRATION_DECISION_CHECK = """
    (NEW.status = 'RESOLVED_APPROVED' AND NEW.final_decision <> 'APPROVE')
    OR (NEW.status = 'RESOLVED_REJECTED' AND NEW.final_decision <> 'REJECT')
    OR (NEW.status = 'RESOLVED_REWORK' AND NEW.final_decision <> 'REWORK')
"""

_SQLITE_RESULT_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_submissions_initial_status
    BEFORE INSERT ON annotation_submissions
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_submission_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_submissions_transition
    BEFORE UPDATE ON annotation_submissions
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_SUBMISSION_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_submission_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_submissions_immutable
    BEFORE UPDATE ON annotation_submissions
    FOR EACH ROW
    WHEN OLD.status <> 'DRAFT' AND ({_SUBMISSION_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_submission_immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_submissions_snapshot
    BEFORE UPDATE ON annotation_submissions
    FOR EACH ROW
    WHEN NEW.status = 'SUBMITTED' AND ({_SUBMISSION_SNAPSHOT_CHECK})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_submission_schema_snapshot_mismatch');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_submissions_no_delete
    BEFORE DELETE ON annotation_submissions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_submission_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_records_initial_status
    BEFORE INSERT ON annotation_records
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_record_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_records_transition
    BEFORE UPDATE ON annotation_records
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_RECORD_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_record_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_records_immutable
    BEFORE UPDATE ON annotation_records
    FOR EACH ROW
    WHEN OLD.status <> 'DRAFT' AND ({_RECORD_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_record_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_records_no_delete
    BEFORE DELETE ON annotation_records
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_record_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_reviews_initial_status
    BEFORE INSERT ON annotation_reviews
    FOR EACH ROW
    WHEN NEW.status <> 'PENDING'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_review_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_reviews_transition
    BEFORE UPDATE ON annotation_reviews
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_REVIEW_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_review_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_reviews_separation
    BEFORE INSERT ON annotation_reviews
    FOR EACH ROW
    WHEN {_REVIEW_SELF_CHECK}
    BEGIN
        SELECT RAISE(ABORT, 'annotation_review_self_forbidden');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_reviews_immutable
    BEFORE UPDATE ON annotation_reviews
    FOR EACH ROW
    WHEN OLD.status IN ('APPROVED', 'REJECTED', 'ESCALATED', 'CANCELLED')
      AND ({_REVIEW_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_review_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_reviews_no_delete
    BEFORE DELETE ON annotation_reviews
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_review_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_arbitrations_initial_status
    BEFORE INSERT ON annotation_arbitrations
    FOR EACH ROW
    WHEN NEW.status <> 'PENDING'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_arbitration_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_arbitrations_transition
    BEFORE UPDATE ON annotation_arbitrations
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_ARBITRATION_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_arbitration_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_arbitrations_decision
    BEFORE UPDATE ON annotation_arbitrations
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND ({_ARBITRATION_DECISION_CHECK})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_arbitration_decision_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_arbitrations_immutable
    BEFORE UPDATE ON annotation_arbitrations
    FOR EACH ROW
    WHEN OLD.status IN ('RESOLVED_APPROVED', 'RESOLVED_REJECTED', 'RESOLVED_REWORK', 'CANCELLED')
      AND ({_ARBITRATION_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_arbitration_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_arbitrations_no_delete
    BEFORE DELETE ON annotation_arbitrations
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_arbitration_delete_forbidden');
    END
    """,
)

_SUBMISSION_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_submission_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'annotation_submission_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_SUBMISSION_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_submission_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'DRAFT' AND ({_SUBMISSION_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_submission_immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'SUBMITTED' AND ({_SUBMISSION_SNAPSHOT_CHECK}) THEN
        RAISE EXCEPTION 'annotation_submission_schema_snapshot_mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_RECORD_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_record_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'annotation_record_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_RECORD_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_record_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'DRAFT' AND ({_RECORD_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_record_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_REVIEW_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_review_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING' THEN
            RAISE EXCEPTION 'annotation_review_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        IF ({_REVIEW_SELF_CHECK}) THEN
            RAISE EXCEPTION 'annotation_review_self_forbidden' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_REVIEW_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_review_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('APPROVED', 'REJECTED', 'ESCALATED', 'CANCELLED')
       AND ({_REVIEW_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_review_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_ARBITRATION_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_arbitration_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING' THEN
            RAISE EXCEPTION 'annotation_arbitration_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_ARBITRATION_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_arbitration_state_transition_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND ({_ARBITRATION_DECISION_CHECK}) THEN
        RAISE EXCEPTION 'annotation_arbitration_decision_invalid' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('RESOLVED_APPROVED', 'RESOLVED_REJECTED', 'RESOLVED_REWORK', 'CANCELLED')
       AND ({_ARBITRATION_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_arbitration_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_TRIGGER_TABLES = (
    (
        "trg_annotation_submissions_initial_status",
        "annotation_submissions",
        "INSERT",
        "ddp_annotation_submission_guard",
    ),
    (
        "trg_annotation_submissions_transition",
        "annotation_submissions",
        "UPDATE",
        "ddp_annotation_submission_guard",
    ),
    (
        "trg_annotation_submissions_immutable",
        "annotation_submissions",
        "UPDATE",
        "ddp_annotation_submission_guard",
    ),
    (
        "trg_annotation_submissions_snapshot",
        "annotation_submissions",
        "UPDATE",
        "ddp_annotation_submission_guard",
    ),
    (
        "trg_annotation_submissions_no_delete",
        "annotation_submissions",
        "DELETE",
        "ddp_annotation_delete_guard",
    ),
    (
        "trg_annotation_records_initial_status",
        "annotation_records",
        "INSERT",
        "ddp_annotation_record_guard",
    ),
    (
        "trg_annotation_records_transition",
        "annotation_records",
        "UPDATE",
        "ddp_annotation_record_guard",
    ),
    (
        "trg_annotation_records_immutable",
        "annotation_records",
        "UPDATE",
        "ddp_annotation_record_guard",
    ),
    (
        "trg_annotation_records_no_delete",
        "annotation_records",
        "DELETE",
        "ddp_annotation_delete_guard",
    ),
    (
        "trg_annotation_reviews_initial_status",
        "annotation_reviews",
        "INSERT",
        "ddp_annotation_review_guard",
    ),
    (
        "trg_annotation_reviews_transition",
        "annotation_reviews",
        "UPDATE",
        "ddp_annotation_review_guard",
    ),
    (
        "trg_annotation_reviews_separation",
        "annotation_reviews",
        "INSERT",
        "ddp_annotation_review_guard",
    ),
    (
        "trg_annotation_reviews_immutable",
        "annotation_reviews",
        "UPDATE",
        "ddp_annotation_review_guard",
    ),
    (
        "trg_annotation_reviews_no_delete",
        "annotation_reviews",
        "DELETE",
        "ddp_annotation_delete_guard",
    ),
    (
        "trg_annotation_arbitrations_initial_status",
        "annotation_arbitrations",
        "INSERT",
        "ddp_annotation_arbitration_guard",
    ),
    (
        "trg_annotation_arbitrations_transition",
        "annotation_arbitrations",
        "UPDATE",
        "ddp_annotation_arbitration_guard",
    ),
    (
        "trg_annotation_arbitrations_decision",
        "annotation_arbitrations",
        "UPDATE",
        "ddp_annotation_arbitration_guard",
    ),
    (
        "trg_annotation_arbitrations_immutable",
        "annotation_arbitrations",
        "UPDATE",
        "ddp_annotation_arbitration_guard",
    ),
    (
        "trg_annotation_arbitrations_no_delete",
        "annotation_arbitrations",
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


def install_result_guards(connection: Connection) -> None:
    required = {
        "annotation_submissions",
        "annotation_records",
        "annotation_reviews",
        "annotation_arbitrations",
    }
    if not all(_has_table(connection, name) for name in required):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_RESULT_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in (
            _SUBMISSION_FUNCTION_SQL,
            _RECORD_FUNCTION_SQL,
            _REVIEW_FUNCTION_SQL,
            _ARBITRATION_FUNCTION_SQL,
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


def drop_result_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _TRIGGER_TABLES:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _TRIGGER_TABLES:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_annotation_submission_guard()",
            "ddp_annotation_record_guard()",
            "ddp_annotation_review_guard()",
            "ddp_annotation_arbitration_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
