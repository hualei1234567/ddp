"""stage2 ingestion jobs, episodes, episode assets and lineage

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08

建立 IngestionJob、Episode、Episode-RawAsset 与血缘投影/边：
- 所有表强制 ``project_id``，父引用使用 ``(project_id, id)`` 复合外键；
- 血缘边只追加，两端引用 ``lineage_node_refs`` 复合主键，天然阻断跨项目；
- Episode READY 前允许无 Manifest，READY 起由 CHECK/触发器要求 Manifest；
- 状态迁移、冻结字段与只追加约束由 0010 安装的触发器兜底。

阶段 2 尚无历史数据，本迁移不做回填。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

_JOB_TYPE_VALUES = "'VALIDATE', 'PARSE_METADATA', 'SEGMENT_EPISODE', 'REVALIDATE'"
_JOB_STATUS_VALUES = "'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLING', 'CANCELLED'"
_EPISODE_STATUS_VALUES = (
    "'PENDING', 'PROCESSING', 'READY', 'QUALITY_CHECKING', 'ACCEPTED', "
    "'COMPLETED', 'ARCHIVED', 'FAILED', 'REJECTED', 'INVALIDATED'"
)
_QUALITY_STATUS_VALUES = "'NOT_CHECKED', 'PASS', 'WARN', 'FAIL'"
_ANNOTATION_STATUS_VALUES = "'NOT_STARTED', 'IN_PROGRESS', 'SUBMITTED', 'APPROVED', 'REJECTED'"
_NODE_TYPE_VALUES = (
    "'RAW_ASSET', 'UPLOAD_SESSION', 'COLLECTION_TASK', 'COLLECTION_BATCH', "
    "'INGESTION_JOB', 'EPISODE', 'QUALITY_RUN', 'ANNOTATION', 'DATASET_VERSION', "
    "'TRAINING_RUN'"
)
_RELATION_VALUES = (
    "'UPLOADED_BY', 'PRODUCED_BY', 'USED_AS_INPUT', 'PRODUCED', 'SEGMENTED_INTO', "
    "'SUPERSEDES', 'INVALIDATES', 'EVALUATED_BY', 'ANNOTATED_BY', 'INCLUDED_IN'"
)
_READY_STATUSES = "'READY', 'QUALITY_CHECKING', 'ACCEPTED', 'COMPLETED', 'ARCHIVED'"
_CONFIG_HASH_CHECK = (
    "config_hash IS NULL OR ("
    "length(config_hash) = 71 AND substr(config_hash, 1, 7) = 'sha256:'"
    ")"
)
_MANIFEST_SHA_CHECK = (
    "manifest_sha256 IS NULL OR ("
    "length(manifest_sha256) = 71 AND substr(manifest_sha256, 1, 7) = 'sha256:'"
    ")"
)
_ASSET_SHA_CHECK = "length(sha256_snapshot) = 71 AND substr(sha256_snapshot, 1, 7) = 'sha256:'"


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    ]


def _audit_constraints(table: str) -> list[sa.schema.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{table}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{table}_updated_by_users",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("raw_asset_id", sa.Uuid(), nullable=True),
        sa.Column("raw_asset_ids", _JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("input_manifest_key", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("timeout_sec", sa.Integer(), nullable=False),
        sa.Column("pipeline_id", sa.String(length=128), nullable=True),
        sa.Column("pipeline_version", sa.String(length=64), nullable=True),
        sa.Column("parser_id", sa.String(length=128), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("config_hash", sa.String(length=71), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column("output_refs", _JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("log_ref", sa.String(length=1024), nullable=True),
        sa.Column("metrics", _JSON_TYPE, server_default=sa.text("'{}'"), nullable=False),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        sa.UniqueConstraint("project_id", "id", name="uq_ingestion_jobs_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_ingestion_jobs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "raw_asset_id"],
            ["raw_assets.project_id", "raw_assets.id"],
            name="fk_ingestion_jobs_project_raw_asset",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"job_type IN ({_JOB_TYPE_VALUES})",
            name=op.f("ck_ingestion_jobs_job_type_allowed"),
        ),
        sa.CheckConstraint(
            f"status IN ({_JOB_STATUS_VALUES})",
            name=op.f("ck_ingestion_jobs_status_allowed"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_ingestion_jobs_attempt_positive")),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name=op.f("ck_ingestion_jobs_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "attempt <= max_attempts",
            name=op.f("ck_ingestion_jobs_attempt_within_max"),
        ),
        sa.CheckConstraint("timeout_sec > 0", name=op.f("ck_ingestion_jobs_timeout_positive")),
        sa.CheckConstraint(
            "raw_asset_id IS NOT NULL OR input_manifest_key IS NOT NULL",
            name=op.f("ck_ingestion_jobs_input_present"),
        ),
        sa.CheckConstraint(_CONFIG_HASH_CHECK, name=op.f("ck_ingestion_jobs_config_hash_format")),
        sa.CheckConstraint(
            "status <> 'RUNNING' OR started_at IS NOT NULL",
            name=op.f("ck_ingestion_jobs_running_requires_started_at"),
        ),
        sa.CheckConstraint(
            "status <> 'SUCCEEDED' OR finished_at IS NOT NULL",
            name=op.f("ck_ingestion_jobs_succeeded_requires_finished_at"),
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR ("
            "error_code IS NOT NULL AND error_summary IS NOT NULL AND finished_at IS NOT NULL"
            ")",
            name=op.f("ck_ingestion_jobs_failed_requires_error"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ingestion_jobs_version_positive")),
        *_audit_constraints("ingestion_jobs"),
    )
    op.create_index(
        "ix_ingestion_jobs_project_status_created_at",
        "ingestion_jobs",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_jobs_project_raw_asset",
        "ingestion_jobs",
        ["project_id", "raw_asset_id"],
        unique=False,
    )
    op.create_index(
        "uq_ingestion_jobs_idempotency",
        "ingestion_jobs",
        ["project_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "episodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("episode_lineage_id", sa.Uuid(), nullable=False),
        sa.Column("episode_version", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("robot_id", sa.Uuid(), nullable=True),
        sa.Column("device_ids", _JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("sensor_ids", _JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "quality_status",
            sa.String(length=16),
            server_default=sa.text("'NOT_CHECKED'"),
            nullable=False,
        ),
        sa.Column(
            "annotation_status",
            sa.String(length=16),
            server_default=sa.text("'NOT_STARTED'"),
            nullable=False,
        ),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("failure_reason", sa.String(length=2000), nullable=True),
        sa.Column("scene_labels", _JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("language_instruction", sa.String(length=2000), nullable=True),
        sa.Column("start_time_ns", sa.BigInteger(), nullable=False),
        sa.Column("end_time_ns", sa.BigInteger(), nullable=False),
        sa.Column("duration_ns", sa.BigInteger(), nullable=False),
        sa.Column(
            "time_domain",
            sa.String(length=16),
            server_default=sa.text("'UTC'"),
            nullable=False,
        ),
        sa.Column("time_base", _JSON_TYPE, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("canonical_schema_version", sa.String(length=64), nullable=False),
        sa.Column("pipeline_id", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("segmentation_rule_version", sa.String(length=64), nullable=False),
        sa.Column("frame_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "calibration_snapshot",
            _JSON_TYPE,
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("manifest_key", sa.String(length=1024), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=71), nullable=True),
        sa.Column("preview_refs", _JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_episodes"),
        sa.UniqueConstraint("project_id", "id", name="uq_episodes_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_episodes_project_code"),
        sa.UniqueConstraint(
            "project_id",
            "episode_lineage_id",
            "episode_version",
            name="uq_episodes_project_lineage_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_episodes_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_episodes_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_episodes_project_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_episodes_project_supersedes",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN ({_EPISODE_STATUS_VALUES})",
            name=op.f("ck_episodes_status_allowed"),
        ),
        sa.CheckConstraint(
            f"quality_status IN ({_QUALITY_STATUS_VALUES})",
            name=op.f("ck_episodes_quality_status_allowed"),
        ),
        sa.CheckConstraint(
            f"annotation_status IN ({_ANNOTATION_STATUS_VALUES})",
            name=op.f("ck_episodes_annotation_status_allowed"),
        ),
        sa.CheckConstraint("time_domain = 'UTC'", name=op.f("ck_episodes_time_domain_utc")),
        sa.CheckConstraint(
            "start_time_ns < end_time_ns",
            name=op.f("ck_episodes_time_range_valid"),
        ),
        sa.CheckConstraint(
            "duration_ns = end_time_ns - start_time_ns",
            name=op.f("ck_episodes_duration_consistent"),
        ),
        sa.CheckConstraint(
            "episode_version >= 1",
            name=op.f("ck_episodes_episode_version_positive"),
        ),
        sa.CheckConstraint(
            "frame_count >= 0",
            name=op.f("ck_episodes_frame_count_non_negative"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_episodes_attempt_positive")),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name=op.f("ck_episodes_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "attempt <= max_attempts",
            name=op.f("ck_episodes_attempt_within_max"),
        ),
        sa.CheckConstraint(_MANIFEST_SHA_CHECK, name=op.f("ck_episodes_manifest_sha256_format")),
        sa.CheckConstraint(
            f"status NOT IN ({_READY_STATUSES}) OR ("
            "manifest_key IS NOT NULL AND manifest_sha256 IS NOT NULL)",
            name=op.f("ck_episodes_ready_requires_manifest"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_episodes_version_positive")),
        *_audit_constraints("episodes"),
    )
    op.create_index(
        "ix_episodes_project_status_created_at",
        "episodes",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_episodes_project_task",
        "episodes",
        ["project_id", "task_id"],
        unique=False,
    )
    op.create_index(
        "ix_episodes_project_batch",
        "episodes",
        ["project_id", "batch_id"],
        unique=False,
    )
    op.create_index(
        "ix_episodes_project_lineage",
        "episodes",
        ["project_id", "episode_lineage_id"],
        unique=False,
    )
    op.create_index(
        "uq_episodes_supersedes",
        "episodes",
        ["project_id", "supersedes_id"],
        unique=True,
        sqlite_where=sa.text("supersedes_id IS NOT NULL"),
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )

    op.create_table(
        "episode_raw_assets",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("start_time_ns", sa.BigInteger(), nullable=True),
        sa.Column("end_time_ns", sa.BigInteger(), nullable=True),
        sa.Column("sha256_snapshot", sa.String(length=71), nullable=False),
        sa.Column("size_bytes_snapshot", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "project_id",
            "episode_id",
            "asset_id",
            "role",
            name="pk_episode_raw_assets",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_episode_raw_assets_project_episode",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "asset_id"],
            ["raw_assets.project_id", "raw_assets.id"],
            name="fk_episode_raw_assets_project_asset",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(role) >= 1", name=op.f("ck_episode_raw_assets_role_non_empty")),
        sa.CheckConstraint(_ASSET_SHA_CHECK, name=op.f("ck_episode_raw_assets_sha256_format")),
        sa.CheckConstraint(
            "size_bytes_snapshot > 0",
            name=op.f("ck_episode_raw_assets_size_positive"),
        ),
        sa.CheckConstraint(
            "(start_time_ns IS NULL AND end_time_ns IS NULL) OR ("
            "start_time_ns IS NOT NULL AND end_time_ns IS NOT NULL "
            "AND start_time_ns < end_time_ns)",
            name=op.f("ck_episode_raw_assets_time_range_valid"),
        ),
    )
    op.create_index(
        "ix_episode_raw_assets_project_asset",
        "episode_raw_assets",
        ["project_id", "asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_episode_raw_assets_project_episode",
        "episode_raw_assets",
        ["project_id", "episode_id"],
        unique=False,
    )

    op.create_table(
        "lineage_node_refs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            "node_type",
            "node_id",
            name="pk_lineage_node_refs",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_lineage_node_refs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"node_type IN ({_NODE_TYPE_VALUES})",
            name=op.f("ck_lineage_node_refs_node_type_allowed"),
        ),
    )
    op.create_index(
        "ix_lineage_node_refs_project_node",
        "lineage_node_refs",
        ["project_id", "node_id"],
        unique=False,
    )

    op.create_table(
        "data_lineage_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("from_node_type", sa.String(length=32), nullable=False),
        sa.Column("from_node_id", sa.Uuid(), nullable=False),
        sa.Column("to_node_type", sa.String(length=32), nullable=False),
        sa.Column("to_node_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("metadata", _JSON_TYPE, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_lineage_edges"),
        sa.UniqueConstraint(
            "project_id",
            "from_node_type",
            "from_node_id",
            "to_node_type",
            "to_node_id",
            "relation",
            name="uq_data_lineage_edges_endpoints_relation",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_data_lineage_edges_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "from_node_type", "from_node_id"],
            [
                "lineage_node_refs.project_id",
                "lineage_node_refs.node_type",
                "lineage_node_refs.node_id",
            ],
            name="fk_data_lineage_edges_project_from_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "to_node_type", "to_node_id"],
            [
                "lineage_node_refs.project_id",
                "lineage_node_refs.node_type",
                "lineage_node_refs.node_id",
            ],
            name="fk_data_lineage_edges_project_to_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_data_lineage_edges_created_by_users",
        ),
        sa.CheckConstraint(
            f"from_node_type IN ({_NODE_TYPE_VALUES})",
            name=op.f("ck_data_lineage_edges_from_node_type_allowed"),
        ),
        sa.CheckConstraint(
            f"to_node_type IN ({_NODE_TYPE_VALUES})",
            name=op.f("ck_data_lineage_edges_to_node_type_allowed"),
        ),
        sa.CheckConstraint(
            f"relation IN ({_RELATION_VALUES})",
            name=op.f("ck_data_lineage_edges_relation_allowed"),
        ),
        sa.CheckConstraint(
            "NOT (from_node_type = to_node_type AND from_node_id = to_node_id)",
            name=op.f("ck_data_lineage_edges_no_self_edge"),
        ),
    )
    op.create_index(
        "ix_data_lineage_edges_project_from",
        "data_lineage_edges",
        ["project_id", "from_node_type", "from_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_lineage_edges_project_to",
        "data_lineage_edges",
        ["project_id", "to_node_type", "to_node_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_data_lineage_edges_project_to", table_name="data_lineage_edges")
    op.drop_index("ix_data_lineage_edges_project_from", table_name="data_lineage_edges")
    op.drop_table("data_lineage_edges")
    op.drop_index("ix_lineage_node_refs_project_node", table_name="lineage_node_refs")
    op.drop_table("lineage_node_refs")
    op.drop_index("ix_episode_raw_assets_project_episode", table_name="episode_raw_assets")
    op.drop_index("ix_episode_raw_assets_project_asset", table_name="episode_raw_assets")
    op.drop_table("episode_raw_assets")
    op.drop_index("uq_episodes_supersedes", table_name="episodes")
    op.drop_index("ix_episodes_project_lineage", table_name="episodes")
    op.drop_index("ix_episodes_project_batch", table_name="episodes")
    op.drop_index("ix_episodes_project_task", table_name="episodes")
    op.drop_index("ix_episodes_project_status_created_at", table_name="episodes")
    op.drop_table("episodes")
    op.drop_index("uq_ingestion_jobs_idempotency", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_project_raw_asset", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_project_status_created_at", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
