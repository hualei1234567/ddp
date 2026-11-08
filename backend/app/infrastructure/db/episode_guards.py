"""IngestionJob、Episode 与血缘的数据库兜底。

迁移 0010 与 ``Base.metadata.create_all`` 安装同一份 DDL：
- 初始状态、合法迁移、重试次数与终态不可回退；
- READY 后冻结 Episode 数据字段，ACCEPTED 后冻结质量结论；
- Episode-RawAsset、血缘节点投影与边只追加；
- 血缘跨项目由复合外键兜底，SUPERSEDES 单入单出且不成环。

SQLite 仅用于测试，PostgreSQL 为验收目标。
"""

# ruff: noqa: S608 - SQL 只由固定枚举/常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.episodes.state_machines import (
    EPISODE_DATA_FROZEN_FIELDS,
    episode_transition_pairs,
)
from app.domain.ingestion.job_state_machines import job_transition_pairs

_JOB_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(job_transition_pairs())
)
_EPISODE_PAIRS = " OR ".join(
    f"(OLD.status = '{current}' AND NEW.status = '{target}')"
    for current, target in sorted(episode_transition_pairs())
)

_JOB_INPUT_COLUMNS = (
    "project_id",
    "job_type",
    "raw_asset_id",
    "raw_asset_ids",
    "input_manifest_key",
    "idempotency_key",
    "max_attempts",
    "timeout_sec",
    "pipeline_id",
    "pipeline_version",
    "parser_id",
    "parser_version",
    "config_hash",
)
_EPISODE_FROZEN_COLUMNS = tuple(EPISODE_DATA_FROZEN_FIELDS)
_RELATION_VALUES = (
    "'UPLOADED_BY', 'PRODUCED_BY', 'USED_AS_INPUT', 'PRODUCED', 'SEGMENTED_INTO', "
    "'SUPERSEDES', 'INVALIDATES', 'EVALUATED_BY', 'ANNOTATED_BY', 'INCLUDED_IN'"
)
_NODE_TYPE_VALUES = (
    "'RAW_ASSET', 'UPLOAD_SESSION', 'COLLECTION_TASK', 'COLLECTION_BATCH', "
    "'INGESTION_JOB', 'EPISODE', 'QUALITY_RUN', 'ANNOTATION', 'DATASET_VERSION', "
    "'TRAINING_RUN'"
)
_READY_STATUSES = "'READY', 'QUALITY_CHECKING', 'ACCEPTED', 'COMPLETED', 'ARCHIVED'"
_QUALITY_FROZEN_STATUSES = "'ACCEPTED', 'COMPLETED', 'ARCHIVED'"


def _sqlite_immutable(columns: tuple[str, ...]) -> str:
    return " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns)


def _pg_immutable(columns: tuple[str, ...]) -> str:
    return " OR ".join(f"OLD.{column} IS DISTINCT FROM NEW.{column}" for column in columns)


_SQLITE_JOB_IMMUTABLE = _sqlite_immutable(_JOB_INPUT_COLUMNS)
_PG_JOB_IMMUTABLE = _pg_immutable(_JOB_INPUT_COLUMNS)
_SQLITE_EPISODE_IMMUTABLE = _sqlite_immutable(_EPISODE_FROZEN_COLUMNS)
_PG_EPISODE_IMMUTABLE = _pg_immutable(_EPISODE_FROZEN_COLUMNS)


_SQLITE_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_ingestion_jobs_initial_status
    BEFORE INSERT ON ingestion_jobs
    FOR EACH ROW
    WHEN NEW.status <> 'QUEUED'
    BEGIN
        SELECT RAISE(ABORT, 'ingestion_job_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_ingestion_jobs_transition
    BEFORE UPDATE ON ingestion_jobs
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND (
        NOT ({_JOB_PAIRS})
        OR (
            OLD.status = 'FAILED' AND NEW.status = 'QUEUED'
            AND (NEW.attempt <> OLD.attempt + 1 OR NEW.attempt > NEW.max_attempts)
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'ingestion_job_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_ingestion_jobs_input_immutable
    BEFORE UPDATE ON ingestion_jobs
    FOR EACH ROW
    WHEN {_SQLITE_JOB_IMMUTABLE}
    BEGIN
        SELECT RAISE(ABORT, 'ingestion_job_input_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_ingestion_jobs_no_delete
    BEFORE DELETE ON ingestion_jobs
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'ingestion_job_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_episodes_initial_status
    BEFORE INSERT ON episodes
    FOR EACH ROW
    WHEN NEW.status <> 'PENDING'
    BEGIN
        SELECT RAISE(ABORT, 'episode_initial_status_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_episodes_transition
    BEFORE UPDATE ON episodes
    FOR EACH ROW
    WHEN OLD.status <> NEW.status AND (
        NOT ({_EPISODE_PAIRS})
        OR (
            OLD.status = 'FAILED' AND NEW.status = 'PROCESSING'
            AND (NEW.attempt <> OLD.attempt + 1 OR NEW.attempt > NEW.max_attempts)
        )
        OR (
            NEW.status IN ({_READY_STATUSES})
            AND (NEW.manifest_key IS NULL OR NEW.manifest_sha256 IS NULL)
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'episode_state_transition_invalid');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_episodes_frozen_fields
    BEFORE UPDATE ON episodes
    FOR EACH ROW
    WHEN OLD.status IN ({_READY_STATUSES}) AND ({_SQLITE_EPISODE_IMMUTABLE})
    BEGIN
        SELECT RAISE(ABORT, 'episode_data_immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_episodes_quality_frozen
    BEFORE UPDATE ON episodes
    FOR EACH ROW
    WHEN OLD.status IN ({_QUALITY_FROZEN_STATUSES})
      AND OLD.quality_status IS NOT NEW.quality_status
    BEGIN
        SELECT RAISE(ABORT, 'episode_quality_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_episodes_no_delete
    BEFORE DELETE ON episodes
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'episode_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_episode_raw_assets_no_update
    BEFORE UPDATE ON episode_raw_assets
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'episode_raw_asset_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_episode_raw_assets_no_delete
    BEFORE DELETE ON episode_raw_assets
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'episode_raw_asset_delete_forbidden');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_episode_raw_assets_parent_frozen
    BEFORE INSERT ON episode_raw_assets
    FOR EACH ROW
    WHEN COALESCE((
        SELECT status FROM episodes
        WHERE project_id = NEW.project_id AND id = NEW.episode_id
    ), '') IN ({_READY_STATUSES})
    BEGIN
        SELECT RAISE(ABORT, 'episode_raw_asset_parent_frozen');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_lineage_node_refs_identity_immutable
    BEFORE UPDATE ON lineage_node_refs
    FOR EACH ROW
    WHEN OLD.project_id IS NOT NEW.project_id
      OR OLD.node_type IS NOT NEW.node_type
      OR OLD.node_id IS NOT NEW.node_id
    BEGIN
        SELECT RAISE(ABORT, 'lineage_node_ref_identity_immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_lineage_node_refs_no_delete
    BEFORE DELETE ON lineage_node_refs
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'lineage_node_ref_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_data_lineage_edges_no_update
    BEFORE UPDATE ON data_lineage_edges
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'lineage_edge_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_data_lineage_edges_no_delete
    BEFORE DELETE ON data_lineage_edges
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'lineage_edge_append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_data_lineage_edges_no_self
    BEFORE INSERT ON data_lineage_edges
    FOR EACH ROW
    WHEN NEW.from_node_type = NEW.to_node_type AND NEW.from_node_id = NEW.to_node_id
    BEGIN
        SELECT RAISE(ABORT, 'lineage_edge_self_forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_data_lineage_edges_supersedes_linear
    BEFORE INSERT ON data_lineage_edges
    FOR EACH ROW
    WHEN NEW.relation = 'SUPERSEDES' AND (
        EXISTS (
            SELECT 1 FROM data_lineage_edges e
            WHERE e.project_id = NEW.project_id
              AND e.relation = 'SUPERSEDES'
              AND e.from_node_type = NEW.from_node_type
              AND e.from_node_id = NEW.from_node_id
        )
        OR EXISTS (
            SELECT 1 FROM data_lineage_edges e
            WHERE e.project_id = NEW.project_id
              AND e.relation = 'SUPERSEDES'
              AND e.to_node_type = NEW.to_node_type
              AND e.to_node_id = NEW.to_node_id
        )
        OR EXISTS (
            WITH RECURSIVE chain(node_type, node_id) AS (
                SELECT e.to_node_type, e.to_node_id
                FROM data_lineage_edges e
                WHERE e.project_id = NEW.project_id
                  AND e.relation = 'SUPERSEDES'
                  AND e.from_node_type = NEW.to_node_type
                  AND e.from_node_id = NEW.to_node_id
                UNION ALL
                SELECT e.to_node_type, e.to_node_id
                FROM data_lineage_edges e
                JOIN chain c
                  ON e.from_node_type = c.node_type AND e.from_node_id = c.node_id
                WHERE e.project_id = NEW.project_id
                  AND e.relation = 'SUPERSEDES'
            )
            SELECT 1 FROM chain
            WHERE node_type = NEW.from_node_type AND node_id = NEW.from_node_id
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'lineage_supersedes_not_linear');
    END
    """,
)


_JOB_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_ingestion_job_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'QUEUED' THEN
            RAISE EXCEPTION 'ingestion_job_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT ({_JOB_PAIRS}) THEN
            RAISE EXCEPTION 'ingestion_job_state_transition_invalid' USING ERRCODE = '23514';
        END IF;
        IF OLD.status = 'FAILED' AND NEW.status = 'QUEUED'
           AND (NEW.attempt <> OLD.attempt + 1 OR NEW.attempt > NEW.max_attempts) THEN
            RAISE EXCEPTION 'ingestion_job_retry_not_allowed' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF {_PG_JOB_IMMUTABLE} THEN
        RAISE EXCEPTION 'ingestion_job_input_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_EPISODE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_episode_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING' THEN
            RAISE EXCEPTION 'episode_initial_status_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT ({_EPISODE_PAIRS}) THEN
            RAISE EXCEPTION 'episode_state_transition_invalid' USING ERRCODE = '23514';
        END IF;
        IF OLD.status = 'FAILED' AND NEW.status = 'PROCESSING'
           AND (NEW.attempt <> OLD.attempt + 1 OR NEW.attempt > NEW.max_attempts) THEN
            RAISE EXCEPTION 'episode_retry_not_allowed' USING ERRCODE = '23514';
        END IF;
        IF NEW.status IN ({_READY_STATUSES})
           AND (NEW.manifest_key IS NULL OR NEW.manifest_sha256 IS NULL) THEN
            RAISE EXCEPTION 'episode_manifest_required' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF OLD.status IN ({_READY_STATUSES}) AND ({_PG_EPISODE_IMMUTABLE}) THEN
        RAISE EXCEPTION 'episode_data_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ({_QUALITY_FROZEN_STATUSES})
       AND OLD.quality_status IS DISTINCT FROM NEW.quality_status THEN
        RAISE EXCEPTION 'episode_quality_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_EPISODE_RAW_ASSET_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION ddp_episode_raw_asset_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status varchar(32);
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION 'episode_raw_asset_immutable' USING ERRCODE = '23514';
    END IF;
    SELECT status INTO v_status
    FROM episodes
    WHERE project_id = NEW.project_id AND id = NEW.episode_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'episode_raw_asset_parent_missing' USING ERRCODE = '23514';
    END IF;
    IF v_status IN ({_READY_STATUSES}) THEN
        RAISE EXCEPTION 'episode_raw_asset_parent_frozen' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_DELETE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_row_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_code text;
BEGIN
    v_code := CASE TG_TABLE_NAME
        WHEN 'episodes' THEN 'episode_delete_forbidden'
        WHEN 'ingestion_jobs' THEN 'ingestion_job_delete_forbidden'
        WHEN 'lineage_node_refs' THEN 'lineage_node_ref_delete_forbidden'
        WHEN 'data_lineage_edges' THEN 'lineage_edge_append_only'
        ELSE 'row_delete_forbidden'
    END;
    RAISE EXCEPTION USING MESSAGE = v_code, ERRCODE = '23514';
END;
$$;
"""

_LINEAGE_NODE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_lineage_node_ref_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lineage_node_ref_delete_forbidden' USING ERRCODE = '23514';
    END IF;
    IF OLD.project_id IS DISTINCT FROM NEW.project_id
       OR OLD.node_type IS DISTINCT FROM NEW.node_type
       OR OLD.node_id IS DISTINCT FROM NEW.node_id THEN
        RAISE EXCEPTION 'lineage_node_ref_identity_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_LINEAGE_EDGE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ddp_lineage_edge_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION 'lineage_edge_append_only' USING ERRCODE = '23514';
    END IF;
    IF NEW.from_node_type = NEW.to_node_type AND NEW.from_node_id = NEW.to_node_id THEN
        RAISE EXCEPTION 'lineage_edge_self_forbidden' USING ERRCODE = '23514';
    END IF;
    IF NEW.relation = 'SUPERSEDES' AND (
        EXISTS (
            SELECT 1 FROM data_lineage_edges e
            WHERE e.project_id = NEW.project_id
              AND e.relation = 'SUPERSEDES'
              AND e.from_node_type = NEW.from_node_type
              AND e.from_node_id = NEW.from_node_id
        )
        OR EXISTS (
            SELECT 1 FROM data_lineage_edges e
            WHERE e.project_id = NEW.project_id
              AND e.relation = 'SUPERSEDES'
              AND e.to_node_type = NEW.to_node_type
              AND e.to_node_id = NEW.to_node_id
        )
        OR EXISTS (
            WITH RECURSIVE chain(node_type, node_id) AS (
                SELECT e.to_node_type, e.to_node_id
                FROM data_lineage_edges e
                WHERE e.project_id = NEW.project_id
                  AND e.relation = 'SUPERSEDES'
                  AND e.from_node_type = NEW.to_node_type
                  AND e.from_node_id = NEW.to_node_id
                UNION ALL
                SELECT e.to_node_type, e.to_node_id
                FROM data_lineage_edges e
                JOIN chain c
                  ON e.from_node_type = c.node_type AND e.from_node_id = c.node_id
                WHERE e.project_id = NEW.project_id
                  AND e.relation = 'SUPERSEDES'
            )
            SELECT 1 FROM chain
            WHERE node_type = NEW.from_node_type AND node_id = NEW.from_node_id
        )
    ) THEN
        RAISE EXCEPTION 'lineage_supersedes_not_linear' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

_PG_TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    ("trg_ingestion_jobs_initial_status", "ingestion_jobs", "INSERT", "ddp_ingestion_job_guard"),
    ("trg_ingestion_jobs_transition", "ingestion_jobs", "UPDATE", "ddp_ingestion_job_guard"),
    ("trg_ingestion_jobs_no_delete", "ingestion_jobs", "DELETE", "ddp_row_delete_guard"),
    ("trg_episodes_initial_status", "episodes", "INSERT", "ddp_episode_guard"),
    ("trg_episodes_transition", "episodes", "UPDATE", "ddp_episode_guard"),
    ("trg_episodes_no_delete", "episodes", "DELETE", "ddp_row_delete_guard"),
    (
        "trg_episode_raw_assets_no_update",
        "episode_raw_assets",
        "UPDATE",
        "ddp_episode_raw_asset_guard",
    ),
    (
        "trg_episode_raw_assets_no_delete",
        "episode_raw_assets",
        "DELETE",
        "ddp_episode_raw_asset_guard",
    ),
    (
        "trg_episode_raw_assets_parent_frozen",
        "episode_raw_assets",
        "INSERT",
        "ddp_episode_raw_asset_guard",
    ),
    (
        "trg_lineage_node_refs_identity_immutable",
        "lineage_node_refs",
        "UPDATE",
        "ddp_lineage_node_ref_guard",
    ),
    (
        "trg_lineage_node_refs_no_delete",
        "lineage_node_refs",
        "DELETE",
        "ddp_lineage_node_ref_guard",
    ),
    (
        "trg_data_lineage_edges_no_update",
        "data_lineage_edges",
        "UPDATE",
        "ddp_lineage_edge_guard",
    ),
    (
        "trg_data_lineage_edges_no_delete",
        "data_lineage_edges",
        "DELETE",
        "ddp_lineage_edge_guard",
    ),
    (
        "trg_data_lineage_edges_no_self",
        "data_lineage_edges",
        "INSERT",
        "ddp_lineage_edge_guard",
    ),
    (
        "trg_data_lineage_edges_supersedes_linear",
        "data_lineage_edges",
        "INSERT",
        "ddp_lineage_edge_guard",
    ),
)


def _has_tables(connection: Connection) -> bool:
    inspector = inspect(connection)
    return all(
        inspector.has_table(table)
        for table in (
            "ingestion_jobs",
            "episodes",
            "episode_raw_assets",
            "lineage_node_refs",
            "data_lineage_edges",
        )
    )


def install_episode_state_guards(connection: Connection) -> None:
    """安装 Job/Episode/血缘兜底触发器。"""
    if not _has_tables(connection):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for statement in _SQLITE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if dialect == "postgresql":
        connection.exec_driver_sql(_JOB_FUNCTION_SQL)
        connection.exec_driver_sql(_EPISODE_FUNCTION_SQL)
        connection.exec_driver_sql(_EPISODE_RAW_ASSET_FUNCTION_SQL)
        connection.exec_driver_sql(_DELETE_FUNCTION_SQL)
        connection.exec_driver_sql(_LINEAGE_NODE_FUNCTION_SQL)
        connection.exec_driver_sql(_LINEAGE_EDGE_FUNCTION_SQL)
        for trigger, table, _event, _function in _PG_TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table, event, function in _PG_TRIGGERS:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")


def drop_episode_state_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for trigger, _table, _event, _function in _PG_TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        return
    if dialect == "postgresql":
        for trigger, table, _event, _function in _PG_TRIGGERS:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "ddp_ingestion_job_guard()",
            "ddp_episode_guard()",
            "ddp_episode_raw_asset_guard()",
            "ddp_row_delete_guard()",
            "ddp_lineage_node_ref_guard()",
            "ddp_lineage_edge_guard()",
        ):
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
        return
    raise RuntimeError(f"未支持的数据库方言：{dialect}")
