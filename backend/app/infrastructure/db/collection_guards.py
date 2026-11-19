"""采集域数据库兜底：合法状态迁移、不可删除与审计只追加。

迁移 0006/0007/0041 与 ``Base.metadata.create_all`` 安装同一份 DDL，避免测试库与
迁移后的库行为漂移。SQLite 仅用于测试；PostgreSQL 为验收目标。

N8 追加素材表（方案 / SOP / 场景 / 动作库）的合法迁移与初始状态兜底。
素材表**没有**禁删触发器：``DRAFT`` 素材允许硬删除（未被任务引用时），
数据库侧由 ``ON DELETE RESTRICT`` 复合外键保证"被引用就删不掉"。
再加一条"一律禁删"的触发器会把合法的删除路径也堵死。
"""

# ruff: noqa: S608 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.collection.asset_state_machines import asset_transition_pairs
from app.domain.collection.state_machines import (
    batch_transition_pairs,
    session_transition_pairs,
    task_transition_pairs,
)


def _pairs_sql(pairs: frozenset[tuple[str, str]]) -> str:
    return " OR ".join(
        f"(OLD.status = '{current}' AND NEW.status = '{target}')"
        for current, target in sorted(pairs)
    )


_TASK_PAIRS = _pairs_sql(task_transition_pairs())
_BATCH_PAIRS = _pairs_sql(batch_transition_pairs())
_SESSION_PAIRS = _pairs_sql(session_transition_pairs())
_ASSET_PAIRS = _pairs_sql(asset_transition_pairs())

# 素材四表：表名 -> 触发器名。顺序稳定，便于 downgrade 逆序卸载。
_ASSET_TABLES: tuple[str, ...] = (
    "collection_plans",
    "collection_sops",
    "scenarios",
    "action_specs",
)


# 素材表兜底：初始状态必须 DRAFT，状态变更必须落在白名单内。
# 用 ``CREATE TRIGGER IF NOT EXISTS`` 而不是先 DROP：``create_all`` 会被
# 重复调用（测试夹具每次建库），重复安装必须幂等。
def _sqlite_asset_statements(table: str) -> tuple[str, str]:
    """单个素材表的两条 SQLite 触发器（表格名只来自本模块常量）。"""
    return (
        f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_initial_status
    BEFORE INSERT ON {table}
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'collection_asset_initial_status_invalid');
    END
    """,
        f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_transition
    BEFORE UPDATE ON {table}
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_ASSET_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'collection_asset_state_transition_invalid');
    END
    """,
    )


_ASSET_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_collection_asset_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'collection_asset_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSIF OLD.status IS DISTINCT FROM NEW.status
          AND NOT ({_ASSET_PAIRS}) THEN
        RAISE EXCEPTION 'collection_asset_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_SQLITE_AUDIT_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'audit_log_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'audit_log_append_only');
    END
    """,
)

_SQLITE_TASK_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_collection_tasks_initial_status
    BEFORE INSERT ON collection_tasks
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'collection_task_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_collection_tasks_transition
    BEFORE UPDATE ON collection_tasks
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_TASK_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'collection_task_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_collection_tasks_no_delete
    BEFORE DELETE ON collection_tasks
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'collection_task_delete_forbidden');
    END
    """,
)

_SQLITE_BATCH_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_collection_batches_initial_status
    BEFORE INSERT ON collection_batches
    FOR EACH ROW
    WHEN NEW.status <> 'PLANNED'
    BEGIN
        SELECT RAISE(ABORT, 'collection_batch_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_collection_batches_transition
    BEFORE UPDATE ON collection_batches
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND (
        NOT ({_BATCH_PAIRS})
        OR (
            NEW.status = 'READY'
            AND COALESCE((
                SELECT status FROM collection_tasks
                WHERE project_id = NEW.project_id AND id = NEW.task_id
            ), '') NOT IN ('READY', 'ASSIGNED', 'IN_PROGRESS')
        )
        OR (
            NEW.status = 'IN_PROGRESS'
            AND COALESCE((
                SELECT status FROM collection_tasks
                WHERE project_id = NEW.project_id AND id = NEW.task_id
            ), '') <> 'IN_PROGRESS'
        )
        OR (
            NEW.status = 'SUBMITTED'
            AND COALESCE((
                SELECT status FROM collection_tasks
                WHERE project_id = NEW.project_id AND id = NEW.task_id
            ), '') NOT IN ('IN_PROGRESS', 'SUBMITTED', 'DATA_CHECKING', 'REJECTED', 'REWORK')
        )
        OR (
            NEW.status = 'ACCEPTED'
            AND COALESCE((
                SELECT status FROM collection_tasks
                WHERE project_id = NEW.project_id AND id = NEW.task_id
            ), '') NOT IN ('DATA_CHECKING', 'ACCEPTED', 'COMPLETED')
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'collection_batch_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_collection_batches_no_delete
    BEFORE DELETE ON collection_batches
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'collection_batch_delete_forbidden');
    END
    """,
)

_SQLITE_SESSION_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_capture_sessions_initial_status
    BEFORE INSERT ON capture_sessions
    FOR EACH ROW
    WHEN NEW.status <> 'SCHEDULED'
    BEGIN
        SELECT RAISE(ABORT, 'capture_session_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_capture_sessions_transition
    BEFORE UPDATE ON capture_sessions
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_SESSION_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'capture_session_state_transition_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_capture_sessions_no_delete
    BEFORE DELETE ON capture_sessions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'capture_session_delete_forbidden');
    END
    """,
)

_SESSION_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_capture_session_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'SCHEDULED' THEN
            RAISE EXCEPTION 'capture_session_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSIF OLD.status IS DISTINCT FROM NEW.status
          AND NOT ({_SESSION_PAIRS}) THEN
        RAISE EXCEPTION 'capture_session_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_SESSION_DELETE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_capture_session_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'capture_session_delete_forbidden' USING ERRCODE = '23514';
END;
$$;
"""

_AUDIT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_audit_logs_append_only_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_log_append_only' USING ERRCODE = '23514';
END;
$$;
"""

_TASK_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_collection_task_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'collection_task_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSIF OLD.status IS DISTINCT FROM NEW.status
          AND NOT ({_TASK_PAIRS}) THEN
        RAISE EXCEPTION 'collection_task_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_TASK_DELETE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_collection_task_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'collection_task_delete_forbidden' USING ERRCODE = '23514';
END;
$$;
"""

_BATCH_DELETE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_collection_batch_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'collection_batch_delete_forbidden' USING ERRCODE = '23514';
END;
$$;
"""

_BATCH_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_collection_batch_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_task_status varchar(32);
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PLANNED' THEN
            RAISE EXCEPTION 'collection_batch_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT ({_BATCH_PAIRS}) THEN
            RAISE EXCEPTION 'collection_batch_state_transition_invalid'
                USING ERRCODE = '23514';
        END IF;

        SELECT status INTO v_task_status
        FROM collection_tasks
        WHERE project_id = NEW.project_id AND id = NEW.task_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'collection_batch_task_missing' USING ERRCODE = '23514';
        END IF;

        IF NEW.status = 'READY'
           AND v_task_status NOT IN ('READY', 'ASSIGNED', 'IN_PROGRESS') THEN
            RAISE EXCEPTION 'collection_batch_leads_task' USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'IN_PROGRESS' AND v_task_status <> 'IN_PROGRESS' THEN
            RAISE EXCEPTION 'collection_batch_leads_task' USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'SUBMITTED'
           AND v_task_status NOT IN (
               'IN_PROGRESS', 'SUBMITTED', 'DATA_CHECKING', 'REJECTED', 'REWORK'
           ) THEN
            RAISE EXCEPTION 'collection_batch_leads_task' USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'ACCEPTED'
           AND v_task_status NOT IN ('DATA_CHECKING', 'ACCEPTED', 'COMPLETED') THEN
            RAISE EXCEPTION 'collection_batch_leads_task' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_audit_guards(connection: Connection) -> None:
    """安装审计只追加兜底。"""
    if not _has_table(connection, "audit_logs"):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_AUDIT_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_AUDIT_FUNCTION_SQL)
        for trigger in ("trg_audit_logs_no_update", "trg_audit_logs_no_delete"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON audit_logs")
        for trigger, event in (
            ("trg_audit_logs_no_update", "UPDATE"),
            ("trg_audit_logs_no_delete", "DELETE"),
        ):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON audit_logs "
                "FOR EACH ROW EXECUTE FUNCTION ddp_audit_logs_append_only_guard()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_audit_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_audit_logs_no_update")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete")
        return
    if dialect == "postgresql":
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_audit_logs_no_update ON audit_logs")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete ON audit_logs")
        connection.exec_driver_sql("DROP FUNCTION IF EXISTS ddp_audit_logs_append_only_guard()")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def install_asset_state_guards(connection: Connection) -> None:
    """安装素材表（方案 / SOP / 场景 / 动作库）的初始状态与迁移兜底。"""
    present = [table for table in _ASSET_TABLES if _has_table(connection, table)]
    if not present:
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for table in present:
            for statement in _sqlite_asset_statements(table):
                connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_ASSET_FUNCTION_SQL)
        for table in present:
            for trigger, event in (
                (f"trg_{table}_initial_status", "INSERT"),
                (f"trg_{table}_transition", "UPDATE"),
            ):
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
                connection.exec_driver_sql(
                    f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION ddp_collection_asset_guard()"
                )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_asset_state_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for table in _ASSET_TABLES:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_initial_status")
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_transition")
        return
    if dialect == "postgresql":
        for table in _ASSET_TABLES:
            if not _has_table(connection, table):
                continue
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS trg_{table}_initial_status ON {table}"
            )
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_transition ON {table}")
        connection.exec_driver_sql("DROP FUNCTION IF EXISTS ddp_collection_asset_guard()")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def install_collection_state_guards(connection: Connection) -> None:
    """安装任务/批次合法迁移、初始状态与禁删触发器。"""
    if not (
        _has_table(connection, "collection_tasks") and _has_table(connection, "collection_batches")
    ):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in (*_SQLITE_TASK_STATEMENTS, *_SQLITE_BATCH_STATEMENTS):
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_TASK_FUNCTION_SQL)
        connection.exec_driver_sql(_TASK_DELETE_FUNCTION_SQL)
        connection.exec_driver_sql(_BATCH_FUNCTION_SQL)
        connection.exec_driver_sql(_BATCH_DELETE_FUNCTION_SQL)
        for trigger in (
            "trg_collection_tasks_initial_status",
            "trg_collection_tasks_transition",
            "trg_collection_tasks_no_delete",
            "trg_collection_batches_initial_status",
            "trg_collection_batches_transition",
            "trg_collection_batches_no_delete",
        ):
            table = "collection_tasks" if "tasks" in trigger else "collection_batches"
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, event, function in (
            (
                "trg_collection_tasks_initial_status",
                "INSERT",
                "ddp_collection_task_guard",
            ),
            (
                "trg_collection_tasks_transition",
                "UPDATE",
                "ddp_collection_task_guard",
            ),
            (
                "trg_collection_batches_initial_status",
                "INSERT",
                "ddp_collection_batch_guard",
            ),
            (
                "trg_collection_batches_transition",
                "UPDATE",
                "ddp_collection_batch_guard",
            ),
        ):
            table = "collection_tasks" if "tasks" in trigger else "collection_batches"
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_collection_tasks_no_delete "
            "BEFORE DELETE ON collection_tasks FOR EACH ROW "
            "EXECUTE FUNCTION ddp_collection_task_delete_guard()"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_collection_batches_no_delete "
            "BEFORE DELETE ON collection_batches FOR EACH ROW "
            "EXECUTE FUNCTION ddp_collection_batch_delete_guard()"
        )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_collection_state_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger in (
            "trg_collection_tasks_initial_status",
            "trg_collection_tasks_transition",
            "trg_collection_tasks_no_delete",
            "trg_collection_batches_initial_status",
            "trg_collection_batches_transition",
            "trg_collection_batches_no_delete",
        ):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table in (
            ("trg_collection_tasks_initial_status", "collection_tasks"),
            ("trg_collection_tasks_transition", "collection_tasks"),
            ("trg_collection_tasks_no_delete", "collection_tasks"),
            ("trg_collection_batches_initial_status", "collection_batches"),
            ("trg_collection_batches_transition", "collection_batches"),
            ("trg_collection_batches_no_delete", "collection_batches"),
        ):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_collection_task_guard()",
            "ddp_collection_task_delete_guard()",
            "ddp_collection_batch_guard()",
            "ddp_collection_batch_delete_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def install_capture_session_state_guards(connection: Connection) -> None:
    """安装采集会话的初始状态、合法迁移与禁删兜底。

    会话是执行记录：删除会丢失"这段执行发生过"的事实，因此与任务/批次
    一致加禁删触发器；终结靠状态（``COMPLETED`` / ``ABORTED``），不靠删除。
    """
    if not _has_table(connection, "capture_sessions"):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_SESSION_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_SESSION_FUNCTION_SQL)
        connection.exec_driver_sql(_SESSION_DELETE_FUNCTION_SQL)
        for trigger in (
            "trg_capture_sessions_initial_status",
            "trg_capture_sessions_transition",
            "trg_capture_sessions_no_delete",
        ):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON capture_sessions")
        for trigger, event, function in (
            (
                "trg_capture_sessions_initial_status",
                "INSERT",
                "ddp_capture_session_guard",
            ),
            (
                "trg_capture_sessions_transition",
                "UPDATE",
                "ddp_capture_session_guard",
            ),
            (
                "trg_capture_sessions_no_delete",
                "DELETE",
                "ddp_capture_session_delete_guard",
            ),
        ):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON capture_sessions "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_capture_session_state_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger in (
            "trg_capture_sessions_initial_status",
            "trg_capture_sessions_transition",
            "trg_capture_sessions_no_delete",
        ):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger in (
            "trg_capture_sessions_initial_status",
            "trg_capture_sessions_transition",
            "trg_capture_sessions_no_delete",
        ):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON capture_sessions")
        for function in (
            "ddp_capture_session_guard()",
            "ddp_capture_session_delete_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
