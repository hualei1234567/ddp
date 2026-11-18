"""stage7 data source reviews

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-15

阶段 7 第三批：导入结果的人工审核记录。

`review_segregation_of_duties` 把"审核人不得是提交人"写进数据库：
应用层也拦，但这类规则一旦被绕过就是合规问题，值得兜底。
`uq_reviews_project_import` 保证一个导入只有一条审核记录——
多条会让"是否通过"的判定含糊。

**为什么改成显式 DDL**：本迁移原先用 ``DataReviewModel.__table__.create``
建表，属于"迁移引用活模型"的已知存量基线。迁移 0062 要给本表加
``evidence`` 列——只要本迁移还引用活模型，全新库升级到 0026 时就会把
新列一起建出来，0062 再 ADD COLUMN 必然 ``duplicate column``。按
``tests/migrations/test_migrations_are_immutable_snapshots.py`` 的约定，
加列之前先把本迁移冻结成历史快照（列与约束此处不含 0062 的 ``evidence``）。
约束名与 ORM 命名约定一致：CHECK 传短名（由 ``ck_<table>_<name>``
约定展开，避免二次加前缀），外键与唯一约束传展开后的全名，保证
"迁移建出的库"与 ``create_all`` 建出的库同名同形。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_source_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=2000), nullable=True),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["data_source_imports.id"],
            name="fk_data_source_reviews_import_id_data_source_imports",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_data_source_reviews_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_data_source_reviews_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_data_source_reviews_updated_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_source_reviews"),
        sa.UniqueConstraint("project_id", "import_id", name="uq_reviews_project_import"),
        sa.UniqueConstraint("project_id", "id", name="uq_reviews_project_id_id"),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')",
            name="review_status_allowed",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('ACCEPT','REJECT','NEEDS_FIX')",
            name="review_decision_allowed",
        ),
        sa.CheckConstraint(
            "reviewer_id IS NULL OR reviewer_id <> submitted_by",
            name="review_segregation_of_duties",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )


def downgrade() -> None:
    op.drop_table("data_source_reviews")
