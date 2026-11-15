"""stage7 imported media assets

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-15

阶段 7 第二批：导入产出的媒体资产。

数据库只存元数据、对象存储引用、Hash 与审看所需的时序提示；文件本体在对象存储。
`uq_media_assets_import_path` 保证同一导入内同一路径只出现一次——
重复会导致审看列表出现重复项，这类问题在 UI 上很难排查。

**为什么改为显式 `op.create_table` 而不复用 ORM 模型**
------------------------------------------------------
本迁移原先写的是 ``MediaAssetModel.__table__.create(...)``，即用**当前**的
ORM 模型反射建表。这让迁移不再是历史快照：后来给模型加了
``raw_asset_id`` 列（迁移 0033），历史迁移 0025 就跟着把新列一起建出来，
0033 再 ``add_column`` 便报 ``duplicate column name``——在全新库上
``alembic upgrade head`` 必然失败，只有已经升级过的旧库才看不出来。

迁移必须是**不可变快照**：0025 描述的是 0025 那一刻的表结构，0033 负责
增量。这里按 0033 之前的形态显式建表；后续该表的任何变更都只能新增迁移，
不得回头改本文件。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_source_media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("data_source_id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("episode_key", sa.String(length=1024), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("camera", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("excerpt_start_seconds", sa.Float(), nullable=True),
        sa.Column("excerpt_seconds", sa.Float(), nullable=True),
        sa.Column("source_fps", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["data_source_imports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "import_id", "path", name="uq_media_assets_import_path"),
        sa.UniqueConstraint("project_id", "id", name="uq_media_assets_project_id_id"),
        sa.CheckConstraint("size_bytes > 0", name="media_size_positive"),
        sa.CheckConstraint("length(sha256) = 71", name="media_sha256_length"),
        sa.CheckConstraint(
            "excerpt_start_seconds IS NULL OR excerpt_start_seconds >= 0",
            name="media_excerpt_start_non_negative",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index(
        "ix_media_assets_project_episode",
        "data_source_media_assets",
        ["project_id", "data_source_id", "episode_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_project_episode", table_name="data_source_media_assets")
    op.drop_table("data_source_media_assets")
