"""stage7 data source registry

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-14

阶段 7 第一批：外部数据源登记与导入记录（`docs/ddp/07` §2）。

两处关键约束直接写进数据库，而不是只靠应用层：

- ``revision_is_commit_sha``：revision 必须是 40 位 commit sha。``main`` 会移动，
  只存分支名的导入不可复现——让非法值根本进不了库。
- ``gated_consistency``：``access_condition='GATED'`` 与 ``is_gated`` 必须一致，
  防止"标成公开、实际要申请"导致合规误判。

不新建审计/outbox/幂等表：复用既有横切表。

**为什么这里写显式 DDL 而不复制 ORM 模型**
-------------------------------------------
本迁移原先写的是 ``DataSourceModel.__table__.create(...)``。后来给
``data_source_imports`` 加 ``attempt`` / ``max_attempts``（迁移 0034），
0024 在全新库上就把这两列一起建了出来，0034 再 ``add_column`` 直接报
``column "attempt" already exists`` — 全新库升级必然失败，老库看不出来。

迁移是**历史快照**：0024 描述 0024 那一刻的表结构，后续增量由各自迁移负责。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("repo_id", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.String(length=100), nullable=False),
        sa.Column("license", sa.String(length=100), nullable=False),
        sa.Column("access_condition", sa.String(length=32), nullable=False),
        sa.Column("is_gated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("config_name", sa.String(length=100), nullable=True),
        sa.Column("split", sa.String(length=100), nullable=True),
        sa.Column("dataset_card_url", sa.String(length=2048), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_data_sources_project_code"),
        sa.UniqueConstraint(
            "project_id",
            "repo_id",
            "revision",
            "config_name",
            "split",
            name="uq_data_sources_project_repo_revision",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_data_sources_project_id_id"),
        sa.CheckConstraint(
            "access_condition IN ('PUBLIC','GATED','PRIVATE','UNKNOWN')",
            name="access_condition_allowed",
        ),
        sa.CheckConstraint("kind IN ('HUGGINGFACE')", name="kind_allowed"),
        sa.CheckConstraint(
            "(access_condition = 'GATED' AND is_gated = true) "
            "OR (access_condition <> 'GATED' AND access_condition <> 'PUBLIC') "
            "OR (access_condition = 'PUBLIC' AND is_gated = false)",
            name="gated_consistency",
        ),
        sa.CheckConstraint("length(revision) = 40", name="revision_is_commit_sha"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index("ix_data_sources_project_kind", "data_sources", ["project_id", "kind"])

    op.create_table(
        "data_source_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("data_source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("tool_version", sa.String(length=100), nullable=False),
        sa.Column("sample_limit", sa.Integer(), nullable=True),
        sa.Column("cache_location", sa.String(length=1024), nullable=True),
        sa.Column("imported_row_count", sa.Integer(), nullable=True),
        sa.Column("imported_file_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_data_source_imports_project_id_id"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="import_status_allowed",
        ),
        sa.CheckConstraint("scope IN ('SAMPLE','SPLIT','FULL')", name="import_scope_allowed"),
        sa.CheckConstraint(
            "scope <> 'SAMPLE' OR sample_limit IS NOT NULL",
            name="sample_scope_requires_limit",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index(
        "ix_data_source_imports_source_status",
        "data_source_imports",
        ["project_id", "data_source_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_data_source_imports_source_status", table_name="data_source_imports")
    op.drop_table("data_source_imports")
    op.drop_index("ix_data_sources_project_kind", table_name="data_sources")
    op.drop_table("data_sources")
