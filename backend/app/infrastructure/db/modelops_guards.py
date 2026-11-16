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
        (
            "metrics",
            "thresholds",
            "verdict",
            "dataset_version_id",
            "model_version_id",
            # 基线与基线数据集版本决定"这条评测是跟谁比出来的"，评测成功后改它
            # 等于篡改结论口径。此前遗漏了两者（与 0038 漏登记 requirement_match
            # 同类）。新列随迁移 0043 加入，但这里必须先按实际列过滤再生成 SQL——
            # 0043 在 SQLite 上要重建 evaluations 表，若触发器正文引用了尚未
            # 建出的列，重建会失败（见 training_guards 的同款处理）。
            "baseline_evaluation_id",
            "baseline_dataset_version_id",
        ),
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


def _active_columns(
    table: str, columns: tuple[str, ...], existing: dict[str, frozenset[str]]
) -> tuple[str, ...]:
    """只保留该表**当前实际存在**的列。

    ``_IMMUTABLE_WHEN`` 是源码常量，而迁移可能在它之后才给表加列（例如 0043
    加 ``baseline_dataset_version_id``）。若触发器正文引用了尚未建出的列：

    - SQLite 在重建该表（``batch_alter_table``）时解析触发器会失败；
    - 更隐蔽的是**回滚**：迁移 0043 的 downgrade 会重装守卫，此时列已被删掉，
      触发器正文引用它同样让整条回滚失败。

    因此按实际结构生成 SQL，与 ``training_guards._immutable_sqlite`` 同一做法。
    列全部不存在时返回空元组，调用方跳过该触发器（建一个永不触发的没有意义）。
    """
    present = existing.get(table)
    if present is None:
        return columns
    return tuple(column for column in columns if column in present)


def _sqlite_statements(existing: dict[str, frozenset[str]]) -> tuple[str, ...]:
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
        active = _active_columns(table, columns, existing)
        if not active:
            continue
        statements.append(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_immutable
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            WHEN OLD.status = '{status}' AND ({_any_changed_sqlite(active)})
            BEGIN
                SELECT RAISE(ABORT, '{table}_immutable');
            END
            """
        )
    return tuple(statements)


def _pg_functions(existing: dict[str, frozenset[str]]) -> tuple[str, ...]:
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
        active = _active_columns(table, columns, existing)
        if not active:
            continue
        functions.append(
            f"""
CREATE OR REPLACE FUNCTION ddp_{table}_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = '{status}' AND ({_any_changed_pg(active)}) THEN
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
    """全部触发器清单（含不可变触发器）。

    ``drop`` 用的就是这份未过滤的清单：即使某表当前没有可守卫的列，
    也要把历史遗留的不可变触发器一并删掉，否则它会继续引用已删列。
    """
    triggers: list[tuple[str, str, str, str]] = []
    for table in _TABLES:
        triggers.append((f"trg_{table}_initial_status", table, "INSERT", f"ddp_{table}_guard"))
        triggers.append((f"trg_{table}_transition", table, "UPDATE", f"ddp_{table}_guard"))
        triggers.append((f"trg_{table}_no_delete", table, "DELETE", "ddp_modelops_delete_guard"))
    for table in _IMMUTABLE_WHEN:
        triggers.append((f"trg_{table}_immutable", table, "UPDATE", f"ddp_{table}_immutable_guard"))
    return tuple(triggers)


def _pg_triggers_to_create(
    existing: dict[str, frozenset[str]],
) -> tuple[tuple[str, str, str, str], ...]:
    """安装用的清单：列全部不存在的表不建不可变触发器。

    建一个正文引用不存在列的触发器，会在第一次 UPDATE 时抛
    ``column ... does not exist``——比不建更糟。
    """
    skipped = {
        table
        for table, (_, columns) in _IMMUTABLE_WHEN.items()
        if not _active_columns(table, columns, existing)
    }
    return tuple(
        item
        for item in _pg_triggers()
        if not (item[0].endswith("_immutable") and item[1] in skipped)
    )


_REQUIRED_TABLES = frozenset(_TABLES)


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _existing_columns(connection: Connection) -> dict[str, frozenset[str]]:
    """读出被守卫表的实际列集合，供生成不可变触发器时过滤。"""
    inspector = inspect(connection)
    result: dict[str, frozenset[str]] = {}
    for table in _IMMUTABLE_WHEN:
        if table in result:
            continue
        if not inspector.has_table(table):
            result[table] = frozenset()
            continue
        result[table] = frozenset(column["name"] for column in inspector.get_columns(table))
    return result


def install_modelops_guards(connection: Connection) -> None:
    if not all(_has_table(connection, name) for name in _REQUIRED_TABLES):
        return
    existing = _existing_columns(connection)
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _sqlite_statements(existing):
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        for sql in _pg_functions(existing):
            connection.exec_driver_sql(sql)
        for trigger, table, _event, _function in _pg_triggers():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in _pg_triggers_to_create(existing):
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
