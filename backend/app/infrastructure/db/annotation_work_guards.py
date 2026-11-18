"""任务、批次、分配与分配事件数据库兜底触发器。"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.annotation.state_machines import (
    assignment_transition_pairs,
    batch_transition_pairs,
    task_transition_pairs,
)

_TASK_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(task_transition_pairs())
)
_BATCH_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(batch_transition_pairs())
)
_ASSIGNMENT_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(assignment_transition_pairs())
)

_TASK_BINDING_IMMUTABLE = (
    "OLD.sample_set_id <> NEW.sample_set_id"
    " OR OLD.schema_id <> NEW.schema_id"
    " OR OLD.schema_version <> NEW.schema_version"
    " OR OLD.schema_sha256 <> NEW.schema_sha256"
)

_BATCH_TASK_LEAD = """
    (NEW.status = 'READY' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('READY', 'ASSIGNED', 'IN_PROGRESS'))
    OR (NEW.status = 'ASSIGNED' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('READY', 'ASSIGNED', 'IN_PROGRESS'))
    OR (NEW.status = 'IN_PROGRESS' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') <> 'IN_PROGRESS')
    OR (NEW.status = 'PAUSED' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('IN_PROGRESS', 'PAUSED'))
    OR (NEW.status = 'BLOCKED' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('IN_PROGRESS', 'BLOCKED'))
    OR (NEW.status = 'SUBMITTED' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('IN_PROGRESS', 'PAUSED', 'BLOCKED', 'SUBMITTED', 'REVIEWING', 'ACCEPTED'))
    OR (NEW.status = 'REVIEWING' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('SUBMITTED', 'REVIEWING'))
    OR (NEW.status = 'ACCEPTED' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('REVIEWING', 'ACCEPTED', 'COMPLETED'))
    OR (NEW.status = 'REJECTED' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('REVIEWING', 'REJECTED', 'REWORK'))
    OR (NEW.status = 'REWORK' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('REJECTED', 'REWORK'))
    OR (NEW.status = 'ARBITRATION' AND COALESCE((
        SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id
    ), '') NOT IN ('REVIEWING', 'ARBITRATION'))
"""

_ASSIGNMENT_TASK_BATCH_LEAD = """
    (NEW.status = 'CLAIMED' AND (
        COALESCE((SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id), '')
            NOT IN ('ASSIGNED', 'IN_PROGRESS')
        OR COALESCE((SELECT status FROM annotation_batches WHERE project_id = NEW.project_id AND id = NEW.batch_id), '')
            NOT IN ('ASSIGNED', 'IN_PROGRESS')
    ))
    OR (NEW.status = 'IN_PROGRESS' AND (
        COALESCE((SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id), '')
            NOT IN ('IN_PROGRESS', 'REWORK')
        OR COALESCE((SELECT status FROM annotation_batches WHERE project_id = NEW.project_id AND id = NEW.batch_id), '')
            NOT IN ('IN_PROGRESS', 'REWORK')
    ))
    OR (NEW.status = 'SUBMITTED' AND (
        COALESCE((SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id), '')
            NOT IN ('IN_PROGRESS', 'PAUSED', 'BLOCKED', 'SUBMITTED', 'REVIEWING')
        OR COALESCE((SELECT status FROM annotation_batches WHERE project_id = NEW.project_id AND id = NEW.batch_id), '')
            NOT IN ('IN_PROGRESS', 'PAUSED', 'BLOCKED', 'SUBMITTED', 'REVIEWING')
    ))
    OR (NEW.status = 'COMPLETED' AND (
        COALESCE((SELECT status FROM annotation_tasks WHERE project_id = NEW.project_id AND id = NEW.task_id), '')
            NOT IN ('REVIEWING', 'ACCEPTED', 'COMPLETED')
        OR COALESCE((SELECT status FROM annotation_batches WHERE project_id = NEW.project_id AND id = NEW.batch_id), '')
            NOT IN ('REVIEWING', 'ACCEPTED')
    ))
"""

_TASK_TRANSITION_CHECK = f"""
    NOT ({_TASK_PAIRS})
    OR (NEW.status = 'READY' AND (
        COALESCE((
            SELECT status FROM annotation_sample_sets
            WHERE project_id = NEW.project_id AND id = NEW.sample_set_id
        ), '') NOT IN ('SNAPSHOTTED', 'CONSUMED')
        OR COALESCE((
            SELECT status FROM annotation_schemas
            WHERE project_id = NEW.project_id AND id = NEW.schema_id
        ), '') <> 'PUBLISHED'
    ))
    OR (NEW.status = 'SUBMITTED' AND (
        NOT EXISTS (
            SELECT 1 FROM annotation_batches
            WHERE project_id = NEW.project_id AND task_id = NEW.id AND status <> 'CANCELLED'
        )
        OR EXISTS (
            SELECT 1 FROM annotation_batches
            WHERE project_id = NEW.project_id AND task_id = NEW.id
              AND status NOT IN ('SUBMITTED', 'REVIEWING', 'ACCEPTED', 'CANCELLED')
        )
    ))
    OR (NEW.status = 'COMPLETED' AND EXISTS (
        SELECT 1 FROM annotation_batches
        WHERE project_id = NEW.project_id AND task_id = NEW.id
          AND status NOT IN ('ACCEPTED', 'CANCELLED')
    ))
"""

_SQLITE_WORK_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_tasks_initial_status
    BEFORE INSERT ON annotation_tasks
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_task_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_tasks_transition
    BEFORE UPDATE ON annotation_tasks
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND ({_TASK_TRANSITION_CHECK})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_task_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_tasks_binding_insert
    BEFORE INSERT ON annotation_tasks
    FOR EACH ROW
    WHEN
        NOT EXISTS (
            SELECT 1 FROM annotation_sample_sets
            WHERE project_id = NEW.project_id AND id = NEW.sample_set_id
              AND status IN ('SNAPSHOTTED', 'CONSUMED')
        )
        OR NOT EXISTS (
            SELECT 1 FROM annotation_schemas
            WHERE project_id = NEW.project_id AND id = NEW.schema_id
              AND status = 'PUBLISHED'
              AND schema_version = NEW.schema_version
              AND schema_sha256 = NEW.schema_sha256
        )
    BEGIN
        SELECT RAISE(ABORT, 'annotation_task_binding_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_tasks_binding_immutable
    BEFORE UPDATE ON annotation_tasks
    FOR EACH ROW
    WHEN {_TASK_BINDING_IMMUTABLE}
    BEGIN
        SELECT RAISE(ABORT, 'annotation_task_binding_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_tasks_no_delete
    BEFORE DELETE ON annotation_tasks
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_task_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_batches_initial_status
    BEFORE INSERT ON annotation_batches
    FOR EACH ROW
    WHEN NEW.status <> 'PLANNED'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_batch_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_batches_transition
    BEFORE UPDATE ON annotation_batches
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND (
        NOT ({_BATCH_PAIRS}) OR {_BATCH_TASK_LEAD}
    )
    BEGIN
        SELECT RAISE(ABORT, 'annotation_batch_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_batches_no_delete
    BEFORE DELETE ON annotation_batches
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_batch_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_assignments_initial_status
    BEFORE INSERT ON annotation_assignments
    FOR EACH ROW
    WHEN NEW.status <> 'ASSIGNED'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_assignment_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_assignments_transition
    BEFORE UPDATE ON annotation_assignments
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND (
        NOT ({_ASSIGNMENT_PAIRS}) OR {_ASSIGNMENT_TASK_BATCH_LEAD}
    )
    BEGIN
        SELECT RAISE(ABORT, 'annotation_assignment_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_assignments_separation
    BEFORE INSERT ON annotation_assignments
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM annotation_assignments
        WHERE project_id = NEW.project_id
          AND batch_id = NEW.batch_id
          AND assignee_id = NEW.assignee_id
          AND assignment_role <> NEW.assignment_role
    )
    BEGIN
        SELECT RAISE(ABORT, 'annotation_assignment_role_conflict');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_assignments_no_delete
    BEFORE DELETE ON annotation_assignments
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_assignment_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_assignment_events_no_update
    BEFORE UPDATE ON annotation_assignment_events
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_assignment_event_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_assignment_events_no_delete
    BEFORE DELETE ON annotation_assignment_events
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_assignment_event_append_only');
    END
    """,
)

_TASK_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_task_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'annotation_task_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF {_TASK_BINDING_IMMUTABLE} THEN
        RAISE EXCEPTION 'annotation_task_binding_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF {_TASK_TRANSITION_CHECK} THEN
            RAISE EXCEPTION 'annotation_task_state_transition_invalid' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

_TASK_BINDING_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_annotation_task_binding_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM annotation_sample_sets
        WHERE project_id = NEW.project_id AND id = NEW.sample_set_id
          AND status IN ('SNAPSHOTTED', 'CONSUMED')
    ) THEN
        RAISE EXCEPTION 'annotation_task_binding_invalid' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM annotation_schemas
        WHERE project_id = NEW.project_id AND id = NEW.schema_id
          AND status = 'PUBLISHED'
          AND schema_version = NEW.schema_version
          AND schema_sha256 = NEW.schema_sha256
    ) THEN
        RAISE EXCEPTION 'annotation_task_binding_invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_BATCH_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_batch_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PLANNED' THEN
            RAISE EXCEPTION 'annotation_batch_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT ({_BATCH_PAIRS}) OR {_BATCH_TASK_LEAD} THEN
            RAISE EXCEPTION 'annotation_batch_state_transition_invalid' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

_ASSIGNMENT_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_assignment_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'ASSIGNED' THEN
            RAISE EXCEPTION 'annotation_assignment_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT ({_ASSIGNMENT_PAIRS}) OR {_ASSIGNMENT_TASK_BATCH_LEAD} THEN
            RAISE EXCEPTION 'annotation_assignment_state_transition_invalid' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

_ASSIGNMENT_SEPARATION_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_annotation_assignment_separation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM annotation_assignments
        WHERE project_id = NEW.project_id
          AND batch_id = NEW.batch_id
          AND assignee_id = NEW.assignee_id
          AND assignment_role <> NEW.assignment_role
    ) THEN
        RAISE EXCEPTION 'annotation_assignment_role_conflict' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_EVENT_APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_annotation_event_append_only_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'annotation_assignment_event_append_only' USING ERRCODE = '23514';
END;
$$;
"""


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


def install_work_guards(connection: Connection) -> None:
    required = {
        "annotation_tasks",
        "annotation_batches",
        "annotation_assignments",
        "annotation_assignment_events",
    }
    if not all(_has_table(connection, name) for name in required):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_WORK_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in (
            _TASK_FUNCTION_SQL,
            _TASK_BINDING_FUNCTION_SQL,
            _BATCH_FUNCTION_SQL,
            _ASSIGNMENT_FUNCTION_SQL,
            _ASSIGNMENT_SEPARATION_FUNCTION_SQL,
            _EVENT_APPEND_ONLY_FUNCTION_SQL,
        ):
            connection.exec_driver_sql(sql)
        _ensure_delete_guard(connection)
        triggers = (
            (
                "trg_annotation_tasks_initial_status",
                "annotation_tasks",
                "INSERT",
                "ddp_annotation_task_guard",
            ),
            (
                "trg_annotation_tasks_transition",
                "annotation_tasks",
                "UPDATE",
                "ddp_annotation_task_guard",
            ),
            (
                "trg_annotation_tasks_binding_insert",
                "annotation_tasks",
                "INSERT",
                "ddp_annotation_task_binding_guard",
            ),
            (
                "trg_annotation_tasks_binding_immutable",
                "annotation_tasks",
                "UPDATE",
                "ddp_annotation_task_guard",
            ),
            (
                "trg_annotation_tasks_no_delete",
                "annotation_tasks",
                "DELETE",
                "ddp_annotation_delete_guard",
            ),
            (
                "trg_annotation_batches_initial_status",
                "annotation_batches",
                "INSERT",
                "ddp_annotation_batch_guard",
            ),
            (
                "trg_annotation_batches_transition",
                "annotation_batches",
                "UPDATE",
                "ddp_annotation_batch_guard",
            ),
            (
                "trg_annotation_batches_no_delete",
                "annotation_batches",
                "DELETE",
                "ddp_annotation_delete_guard",
            ),
            (
                "trg_annotation_assignments_initial_status",
                "annotation_assignments",
                "INSERT",
                "ddp_annotation_assignment_guard",
            ),
            (
                "trg_annotation_assignments_transition",
                "annotation_assignments",
                "UPDATE",
                "ddp_annotation_assignment_guard",
            ),
            (
                "trg_annotation_assignments_separation",
                "annotation_assignments",
                "INSERT",
                "ddp_annotation_assignment_separation_guard",
            ),
            (
                "trg_annotation_assignments_no_delete",
                "annotation_assignments",
                "DELETE",
                "ddp_annotation_delete_guard",
            ),
            (
                "trg_annotation_assignment_events_no_update",
                "annotation_assignment_events",
                "UPDATE",
                "ddp_annotation_event_append_only_guard",
            ),
            (
                "trg_annotation_assignment_events_no_delete",
                "annotation_assignment_events",
                "DELETE",
                "ddp_annotation_event_append_only_guard",
            ),
        )
        for trigger, table, _event, _function in triggers:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in triggers:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_work_guards(connection: Connection) -> None:
    triggers = (
        ("trg_annotation_tasks_initial_status", "annotation_tasks"),
        ("trg_annotation_tasks_transition", "annotation_tasks"),
        ("trg_annotation_tasks_binding_insert", "annotation_tasks"),
        ("trg_annotation_tasks_binding_immutable", "annotation_tasks"),
        ("trg_annotation_tasks_no_delete", "annotation_tasks"),
        ("trg_annotation_batches_initial_status", "annotation_batches"),
        ("trg_annotation_batches_transition", "annotation_batches"),
        ("trg_annotation_batches_no_delete", "annotation_batches"),
        ("trg_annotation_assignments_initial_status", "annotation_assignments"),
        ("trg_annotation_assignments_transition", "annotation_assignments"),
        ("trg_annotation_assignments_separation", "annotation_assignments"),
        ("trg_annotation_assignments_no_delete", "annotation_assignments"),
        ("trg_annotation_assignment_events_no_update", "annotation_assignment_events"),
        ("trg_annotation_assignment_events_no_delete", "annotation_assignment_events"),
    )
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table in triggers:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table in triggers:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_annotation_task_guard()",
            "ddp_annotation_task_binding_guard()",
            "ddp_annotation_batch_guard()",
            "ddp_annotation_assignment_guard()",
            "ddp_annotation_assignment_separation_guard()",
            "ddp_annotation_event_append_only_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
