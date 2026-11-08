"""stage3 annotation schemas, sample sets and episode refs

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-09

阶段 3 第一批标注域（上）：标签 Schema、标注样本集与 Episode 引用。
- 所有表强制 ``project_id``，父引用使用 ``(project_id, id)`` 复合外键；
- 样本引用只允许 Episode，不建立 RawAsset 直连；
- 状态由 CHECK + 触发器双重校验，发布/快照后不可变、记录不可删除。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.annotation_catalog_guards import (
    drop_catalog_guards,
    install_catalog_guards,
)

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


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
            ["project_id"],
            ["projects.id"],
            name=f"fk_{table}_project_id_projects",
            ondelete="CASCADE",
        ),
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
        "annotation_schemas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("schema_lineage_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("annotation_levels", sa.JSON(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("compatibility", sa.String(length=32), nullable=False),
        sa.Column("schema_sha256", sa.String(length=71), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_schemas"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_schemas_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "schema_lineage_id",
            "schema_version",
            name="uq_annotation_schemas_project_lineage_version",
        ),
        sa.UniqueConstraint(
            "project_id",
            "code",
            "schema_version",
            name="uq_annotation_schemas_project_code_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_schemas_project_supersedes",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'DEPRECATED', 'REVOKED')",
            name=op.f("ck_annotation_schemas_status_allowed"),
        ),
        sa.CheckConstraint(
            "compatibility IN ('BACKWARD_COMPATIBLE', 'BREAKING')",
            name=op.f("ck_annotation_schemas_compatibility_allowed"),
        ),
        sa.CheckConstraint(
            _SHA_CHECK.format(column="schema_sha256"),
            name=op.f("ck_annotation_schemas_schema_sha256_format"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_annotation_schemas_schema_version_positive"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_annotation_schemas_version_positive")),
        *_audit_constraints("annotation_schemas"),
    )
    op.create_index(
        "ix_annotation_schemas_project_status_created_at",
        "annotation_schemas",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_schemas_project_lineage",
        "annotation_schemas",
        ["project_id", "schema_lineage_id"],
        unique=False,
    )

    op.create_table(
        "annotation_sample_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selection_rule_version", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("filters", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("strata", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("dedup_key", sa.String(length=32), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("deduplicated_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=71), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.String(length=1000), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "exclusion_summary",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_sample_sets"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_sample_sets_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_annotation_sample_sets_project_code"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PREVIEW', 'SNAPSHOTTED', 'CONSUMED', 'CANCELLED')",
            name=op.f("ck_annotation_sample_sets_status_allowed"),
        ),
        sa.CheckConstraint(
            "strategy IN ('FILTER', 'STRATIFIED', 'RISK_BASED', 'MANUAL')",
            name=op.f("ck_annotation_sample_sets_strategy_allowed"),
        ),
        sa.CheckConstraint(
            "dedup_key IN ('EPISODE_LINEAGE', 'EPISODE_VERSION', 'NONE')",
            name=op.f("ck_annotation_sample_sets_dedup_key_allowed"),
        ),
        sa.CheckConstraint(
            "data_source IN ('REAL', 'SYNTHETIC', 'MIXED', 'UNKNOWN')",
            name=op.f("ck_annotation_sample_sets_data_source_allowed"),
        ),
        sa.CheckConstraint(
            "candidate_count >= 0",
            name=op.f("ck_annotation_sample_sets_candidate_count_non_negative"),
        ),
        sa.CheckConstraint(
            "selected_count >= 0",
            name=op.f("ck_annotation_sample_sets_selected_count_non_negative"),
        ),
        sa.CheckConstraint(
            "excluded_count >= 0",
            name=op.f("ck_annotation_sample_sets_excluded_count_non_negative"),
        ),
        sa.CheckConstraint(
            "deduplicated_count >= 0",
            name=op.f("ck_annotation_sample_sets_deduplicated_count_non_negative"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('DRAFT', 'PREVIEW') OR snapshot_sha256 IS NULL",
            name=op.f("ck_annotation_sample_sets_snapshot_hash_only_after_preview"),
        ),
        sa.CheckConstraint(
            "snapshot_sha256 IS NULL OR (" + _SHA_CHECK.format(column="snapshot_sha256") + ")",
            name=op.f("ck_annotation_sample_sets_snapshot_sha256_format"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_sample_sets_version_positive"),
        ),
        *_audit_constraints("annotation_sample_sets"),
    )
    op.create_index(
        "ix_annotation_sample_sets_project_status_created_at",
        "annotation_sample_sets",
        ["project_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "annotation_sample_episodes",
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("sample_set_id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("episode_lineage_id", sa.Uuid(), nullable=False),
        sa.Column("episode_version", sa.Integer(), nullable=False),
        sa.Column("start_time_ns", sa.Integer(), nullable=True),
        sa.Column("end_time_ns", sa.Integer(), nullable=True),
        sa.Column("time_domain", sa.String(length=16), nullable=False),
        sa.Column("stratum", sa.String(length=100), nullable=True),
        sa.Column("sample_role", sa.String(length=16), nullable=False),
        sa.Column("selected_reason", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("ref_id", name="pk_annotation_sample_episodes"),
        sa.UniqueConstraint(
            "project_id",
            "sample_set_id",
            "episode_id",
            name="uq_annotation_sample_episodes_set_episode",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotation_sample_episodes_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "sample_set_id"],
            ["annotation_sample_sets.project_id", "annotation_sample_sets.id"],
            name="fk_annotation_sample_episodes_project_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_annotation_sample_episodes_project_episode",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "episode_version >= 1",
            name=op.f("ck_annotation_sample_episodes_episode_version_positive"),
        ),
        sa.CheckConstraint(
            "sample_role IN ('NORMAL', 'GOLD', 'IAA', 'QUALITY')",
            name=op.f("ck_annotation_sample_episodes_sample_role_allowed"),
        ),
        sa.CheckConstraint(
            "time_domain IN ('UTC', 'ROS', 'DEVICE', 'HOST', 'MONOTONIC', 'UNKNOWN')",
            name=op.f("ck_annotation_sample_episodes_time_domain_allowed"),
        ),
        sa.CheckConstraint(
            "(start_time_ns IS NULL AND end_time_ns IS NULL) OR ("
            "start_time_ns IS NOT NULL AND end_time_ns IS NOT NULL "
            "AND start_time_ns < end_time_ns)",
            name=op.f("ck_annotation_sample_episodes_time_range_valid"),
        ),
        sa.CheckConstraint(
            "length(selected_reason) >= 1",
            name=op.f("ck_annotation_sample_episodes_selected_reason_non_empty"),
        ),
    )
    op.create_index(
        "ix_annotation_sample_episodes_project_set",
        "annotation_sample_episodes",
        ["project_id", "sample_set_id"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_sample_episodes_project_episode",
        "annotation_sample_episodes",
        ["project_id", "episode_id"],
        unique=False,
    )

    install_catalog_guards(op.get_bind())


def downgrade() -> None:
    drop_catalog_guards(op.get_bind())
    op.drop_index(
        "ix_annotation_sample_episodes_project_episode",
        table_name="annotation_sample_episodes",
    )
    op.drop_index(
        "ix_annotation_sample_episodes_project_set",
        table_name="annotation_sample_episodes",
    )
    op.drop_table("annotation_sample_episodes")
    op.drop_index(
        "ix_annotation_sample_sets_project_status_created_at",
        table_name="annotation_sample_sets",
    )
    op.drop_table("annotation_sample_sets")
    op.drop_index(
        "ix_annotation_schemas_project_lineage",
        table_name="annotation_schemas",
    )
    op.drop_index(
        "ix_annotation_schemas_project_status_created_at",
        table_name="annotation_schemas",
    )
    op.drop_table("annotation_schemas")
