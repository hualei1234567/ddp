"""prm_annotations：PRM 步级标注表（V52-020）

Revision ID: 0078
Revises: 0074
Create Date: 2026-11-25

蓝图 N1「步骤级过程奖励标注」的持久化承载：一条 Agent 轨迹一行，
``step_actions``（步骤序列）、``marks``（已标注步骤）与 ``fork_lineage``
（分叉血缘）走 JSON 列。步级结论与偏好对按 MEM-DEC-002 用 JSON 约定值
表达，**不新增标签类型**，也不做"每步一行"的展开——分叉重写会整段替换
后缀步骤，行级展开反而无法回答"哪些步是重写产物"。

设计要点
--------
- ``project_id + trajectory_id`` 唯一：同一轨迹在项目内只有一份步级标注；
  分叉轨迹的派生 id（如 ``<原 id>#fork-1``）由调用方分配并天然不冲突。
- ``step_count`` / ``marked_count`` 是冗余计数列，与 JSON 载荷由应用层
  一致性保证（``marked_count <= step_count`` 有 CHECK 兜底），支撑看板
  "标注覆盖率"查询不必反序列化大 JSON。
- ``fork_lineage`` 可空：原轨迹没有血缘；NULL ≠ "不是分叉"，也不等于
  "分叉点为 0"——整条重写的分叉也有血缘（``fork_point_index = 0``）。
- 双方言兼容（MEM-INC-013）：SQLite 侧由 ``op.create_table`` 原生建表
  （JSON 列无 PG 专有语法），无 ``ALTER TABLE ... DROP CONSTRAINT``，
  无 ``pg_constraint`` 查询，upgrade/downgrade 均为对称的建表/删表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0078"
down_revision = "0074"
branch_labels = None
depends_on = None

_TABLE = "prm_annotations"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("trajectory_id", sa.String(length=200), nullable=False),
        sa.Column("task_goal", sa.String(length=2000), nullable=True),
        sa.Column("step_actions", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("marks", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("fork_lineage", sa.JSON().with_variant(JSONB, "postgresql"), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("marked_count", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_prm_annotations"),
        sa.UniqueConstraint("project_id", "id", name="uq_prm_annotations_project_id_id"),
        sa.UniqueConstraint("project_id", "trajectory_id", name="uq_prm_annotations_trajectory"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_prm_annotations_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_prm_annotations_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_prm_annotations_updated_by_users",
        ),
        sa.CheckConstraint(
            "length(trajectory_id) > 0", name="ck_prm_annotations_trajectory_id_non_empty"
        ),
        sa.CheckConstraint(
            "step_count >= 1 AND step_count <= 512", name="ck_prm_annotations_step_count_bounds"
        ),
        sa.CheckConstraint(
            "marked_count >= 0 AND marked_count <= step_count",
            name="ck_prm_annotations_marked_count_bounds",
        ),
        sa.CheckConstraint("version >= 1", name="ck_prm_annotations_version_positive"),
    )
    op.create_index(
        "ix_prm_annotations_project_trajectory",
        _TABLE,
        ["project_id", "trajectory_id"],
        unique=False,
    )


def downgrade() -> None:
    # 对称回滚：先删索引再删表。本迁移只建表、无 PG 专有语句
    # （无 pg_constraint 查询、无 DROP CONSTRAINT），SQLite 与 PostgreSQL
    # 走同一套 DDL；MEM-INC-013 的方言分支在此不适用（没有要分支的东西）。
    op.drop_index("ix_prm_annotations_project_trajectory", table_name=_TABLE)
    op.drop_table(_TABLE)
