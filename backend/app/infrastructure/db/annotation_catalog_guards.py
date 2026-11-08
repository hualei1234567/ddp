"""标签 Schema 与样本集数据库兜底触发器。

SQLite 用于测试，PostgreSQL 为验收目标；迁移与 ``Base.metadata.create_all``
安装同一份 DDL，避免测试库与迁移库行为漂移。
"""

# ruff: noqa: S608, E501 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.annotation.state_machines import (
    sample_set_transition_pairs,
    schema_transition_pairs,
)

_SCHEMA_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(schema_transition_pairs())
)
_SAMPLE_SET_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(sample_set_transition_pairs())
)


def _changed(column: str) -> str:
    """跨 SQLite/PostgreSQL 的 NULL 安全“字段已变化”表达式。"""
    return (
        f"(OLD.{column} <> NEW.{column}"
        f" OR (OLD.{column} IS NULL AND NEW.{column} IS NOT NULL)"
        f" OR (OLD.{column} IS NOT NULL AND NEW.{column} IS NULL))"
    )


_SCHEMA_IMMUTABLE = " OR ".join(
    _changed(column)
    for column in (
        "schema_lineage_id",
        "schema_version",
        "code",
        "name",
        "description",
        "annotation_levels",
        "labels",
        "compatibility",
        "schema_sha256",
        "supersedes_id",
    )
)
_SAMPLE_SET_SNAPSHOT = " OR ".join(
    _changed(column)
    for column in (
        "selection_rule_version",
        "strategy",
        "filters",
        "strata",
        "dedup_key",
        "candidate_count",
        "selected_count",
        "excluded_count",
        "deduplicated_count",
        "snapshot_sha256",
        "snapshot_at",
        "data_source",
        "exclusion_summary",
    )
)

_SQLITE_CATALOG_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_schemas_initial_status
    BEFORE INSERT ON annotation_schemas
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_schema_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_schemas_transition
    BEFORE UPDATE ON annotation_schemas
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND NOT ({_SCHEMA_PAIRS})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_schema_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_schemas_immutable
    BEFORE UPDATE ON annotation_schemas
    FOR EACH ROW
    WHEN OLD.status IN ('PUBLISHED', 'DEPRECATED', 'REVOKED') AND ({_SCHEMA_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_schema_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_schemas_no_delete
    BEFORE DELETE ON annotation_schemas
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_schema_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_sets_initial_status
    BEFORE INSERT ON annotation_sample_sets
    FOR EACH ROW
    WHEN NEW.status <> 'DRAFT'
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_set_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_sets_transition
    BEFORE UPDATE ON annotation_sample_sets
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND (
        NOT ({_SAMPLE_SET_PAIRS})
        OR (
            NEW.status = 'CANCELLED'
            AND EXISTS (
                SELECT 1 FROM annotation_tasks
                WHERE project_id = NEW.project_id
                  AND sample_set_id = NEW.id
                  AND status <> 'CANCELLED'
            )
        )
        OR (
            NEW.status = 'CONSUMED'
            AND NOT EXISTS (
                SELECT 1 FROM annotation_tasks
                WHERE project_id = NEW.project_id
                  AND sample_set_id = NEW.id
                  AND status NOT IN ('DRAFT', 'PENDING_REVIEW', 'CANCELLED')
            )
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_set_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_sets_immutable
    BEFORE UPDATE ON annotation_sample_sets
    FOR EACH ROW
    WHEN OLD.status IN ('SNAPSHOTTED', 'CONSUMED') AND ({_SAMPLE_SET_SNAPSHOT})
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_set_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_sets_no_delete
    BEFORE DELETE ON annotation_sample_sets
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_set_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_episodes_insert
    BEFORE INSERT ON annotation_sample_episodes
    FOR EACH ROW
    WHEN
        COALESCE((
            SELECT status FROM annotation_sample_sets
            WHERE project_id = NEW.project_id AND id = NEW.sample_set_id
        ), '') NOT IN ('DRAFT', 'PREVIEW')
        OR COALESCE((
            SELECT status FROM episodes
            WHERE project_id = NEW.project_id AND id = NEW.episode_id
        ), '') NOT IN ('ACCEPTED', 'COMPLETED')
        OR COALESCE((
            SELECT quality_status FROM episodes
            WHERE project_id = NEW.project_id AND id = NEW.episode_id
        ), '') NOT IN ('PASS', 'WARN')
        OR COALESCE((
            SELECT manifest_sha256 FROM episodes
            WHERE project_id = NEW.project_id AND id = NEW.episode_id
        ), '') = ''
        OR COALESCE((
            SELECT episode_lineage_id FROM episodes
            WHERE project_id = NEW.project_id AND id = NEW.episode_id
        ), '') <> NEW.episode_lineage_id
        OR COALESCE((
            SELECT episode_version FROM episodes
            WHERE project_id = NEW.project_id AND id = NEW.episode_id
        ), -1) <> NEW.episode_version
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_episode_ineligible');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_episodes_update
    BEFORE UPDATE ON annotation_sample_episodes
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_episode_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_annotation_sample_episodes_delete
    BEFORE DELETE ON annotation_sample_episodes
    FOR EACH ROW
    WHEN COALESCE((
        SELECT status FROM annotation_sample_sets
        WHERE project_id = OLD.project_id AND id = OLD.sample_set_id
    ), '') NOT IN ('DRAFT', 'PREVIEW')
    BEGIN
        SELECT RAISE(ABORT, 'annotation_sample_set_immutable');
    END
    """,
)

_SCHEMA_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_schema_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'annotation_schema_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM NEW.status AND NOT ({_SCHEMA_PAIRS}) THEN
        RAISE EXCEPTION 'annotation_schema_state_transition_invalid'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('PUBLISHED', 'DEPRECATED', 'REVOKED') AND ({_SCHEMA_IMMUTABLE}) THEN
        RAISE EXCEPTION 'annotation_schema_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_SAMPLE_SET_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_annotation_sample_set_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'annotation_sample_set_initial_status_invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT ({_SAMPLE_SET_PAIRS}) THEN
            RAISE EXCEPTION 'annotation_sample_set_state_transition_invalid'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'CANCELLED' AND EXISTS (
            SELECT 1 FROM annotation_tasks
            WHERE project_id = NEW.project_id
              AND sample_set_id = NEW.id
              AND status <> 'CANCELLED'
        ) THEN
            RAISE EXCEPTION 'annotation_sample_set_referenced'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'CONSUMED' AND NOT EXISTS (
            SELECT 1 FROM annotation_tasks
            WHERE project_id = NEW.project_id
              AND sample_set_id = NEW.id
              AND status NOT IN ('DRAFT', 'PENDING_REVIEW', 'CANCELLED')
        ) THEN
            RAISE EXCEPTION 'annotation_task_not_approved' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF OLD.status IN ('SNAPSHOTTED', 'CONSUMED') AND ({_SAMPLE_SET_SNAPSHOT}) THEN
        RAISE EXCEPTION 'annotation_sample_set_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_SAMPLE_EPISODE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_annotation_sample_episode_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status varchar(32);
    v_episode_status varchar(32);
    v_quality_status varchar(16);
    v_manifest_sha256 varchar(71);
    v_lineage_id uuid;
    v_episode_version integer;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'annotation_sample_episode_immutable' USING ERRCODE = '23514';
    END IF;

    SELECT status INTO v_status FROM annotation_sample_sets
    WHERE project_id = OLD.project_id AND id = OLD.sample_set_id;
    IF TG_OP = 'DELETE' THEN
        IF v_status NOT IN ('DRAFT', 'PREVIEW') THEN
            RAISE EXCEPTION 'annotation_sample_set_immutable' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    SELECT status INTO v_status FROM annotation_sample_sets
    WHERE project_id = NEW.project_id AND id = NEW.sample_set_id;
    SELECT status, quality_status, manifest_sha256, episode_lineage_id, episode_version
      INTO v_episode_status, v_quality_status, v_manifest_sha256, v_lineage_id, v_episode_version
      FROM episodes
     WHERE project_id = NEW.project_id AND id = NEW.episode_id;
    IF v_status IS NULL OR v_status NOT IN ('DRAFT', 'PREVIEW')
       OR v_episode_status IS NULL
       OR v_episode_status NOT IN ('ACCEPTED', 'COMPLETED')
       OR v_quality_status NOT IN ('PASS', 'WARN')
       OR v_manifest_sha256 IS NULL
       OR v_lineage_id IS DISTINCT FROM NEW.episode_lineage_id
       OR v_episode_version IS DISTINCT FROM NEW.episode_version THEN
        RAISE EXCEPTION 'annotation_sample_episode_ineligible' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_catalog_guards(connection: Connection) -> None:
    """安装 Schema/样本集/Episode 引用的初始状态、迁移、不可变与禁删兜底。"""
    required = {
        "annotation_schemas",
        "annotation_sample_sets",
        "annotation_sample_episodes",
    }
    if not all(_has_table(connection, name) for name in required):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_CATALOG_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_SCHEMA_FUNCTION_SQL)
        connection.exec_driver_sql(_SAMPLE_SET_FUNCTION_SQL)
        connection.exec_driver_sql(_SAMPLE_EPISODE_FUNCTION_SQL)
        triggers = (
            (
                "trg_annotation_schemas_initial_status",
                "annotation_schemas",
                "INSERT",
                "ddp_annotation_schema_guard",
            ),
            (
                "trg_annotation_schemas_transition",
                "annotation_schemas",
                "UPDATE",
                "ddp_annotation_schema_guard",
            ),
            (
                "trg_annotation_schemas_no_delete",
                "annotation_schemas",
                "DELETE",
                "ddp_annotation_delete_guard",
            ),
            (
                "trg_annotation_sample_sets_initial_status",
                "annotation_sample_sets",
                "INSERT",
                "ddp_annotation_sample_set_guard",
            ),
            (
                "trg_annotation_sample_sets_transition",
                "annotation_sample_sets",
                "UPDATE",
                "ddp_annotation_sample_set_guard",
            ),
            (
                "trg_annotation_sample_sets_no_delete",
                "annotation_sample_sets",
                "DELETE",
                "ddp_annotation_delete_guard",
            ),
            (
                "trg_annotation_sample_episodes_insert",
                "annotation_sample_episodes",
                "INSERT",
                "ddp_annotation_sample_episode_guard",
            ),
            (
                "trg_annotation_sample_episodes_update",
                "annotation_sample_episodes",
                "UPDATE",
                "ddp_annotation_sample_episode_guard",
            ),
            (
                "trg_annotation_sample_episodes_delete",
                "annotation_sample_episodes",
                "DELETE",
                "ddp_annotation_sample_episode_guard",
            ),
        )
        for trigger, table, _event, _function in triggers:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        _ensure_delete_guard(connection)
        for trigger, table, event, function in triggers:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_catalog_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    triggers = (
        ("trg_annotation_schemas_initial_status", "annotation_schemas"),
        ("trg_annotation_schemas_transition", "annotation_schemas"),
        ("trg_annotation_schemas_immutable", "annotation_schemas"),
        ("trg_annotation_schemas_no_delete", "annotation_schemas"),
        ("trg_annotation_sample_sets_initial_status", "annotation_sample_sets"),
        ("trg_annotation_sample_sets_transition", "annotation_sample_sets"),
        ("trg_annotation_sample_sets_immutable", "annotation_sample_sets"),
        ("trg_annotation_sample_sets_no_delete", "annotation_sample_sets"),
        ("trg_annotation_sample_episodes_insert", "annotation_sample_episodes"),
        ("trg_annotation_sample_episodes_update", "annotation_sample_episodes"),
        ("trg_annotation_sample_episodes_delete", "annotation_sample_episodes"),
    )
    if dialect == "sqlite":
        for trigger, _table in triggers:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table in triggers:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_annotation_schema_guard()",
            "ddp_annotation_sample_set_guard()",
            "ddp_annotation_sample_episode_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def _ensure_delete_guard(connection: Connection) -> None:
    """PostgreSQL 通用禁删函数；多个标注表共享。"""
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
