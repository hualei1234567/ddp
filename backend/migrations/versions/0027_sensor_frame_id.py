"""sensor frame_id and frame_convention

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-15

ADR-0019 阶段 1：把 `coordinate_frame`（自由文本）拆成
`frame_id`（坐标系标识）+ `frame_convention`（坐标约定），
同时**保留旧列并继续写入**，避免破坏已冻结的 `ddp.device/1.0.0`。

回填策略：`frame_id := coordinate_frame`。这是事实上的等价——
阶段 1 要求二者表达同一坐标系（应用层校验不一致即 422）。

SQLite 不支持 `ALTER COLUMN ... SET NOT NULL`，故：
- `frame_id` 以可空列加入并回填；
- 非空由应用层保证；真实 PostgreSQL 上额外尝试置为 NOT NULL。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("sensors", sa.Column("frame_id", sa.String(200), nullable=True))
    op.add_column(
        "sensors",
        sa.Column(
            "frame_convention",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'UNSPECIFIED'"),
        ),
    )
    bind.execute(sa.text("UPDATE sensors SET frame_id = coordinate_frame"))
    # NOT NULL 与 CHECK 只在 PostgreSQL 上加：
    # SQLite 不支持 ALTER 加约束，其约束由 ORM __table_args__ 在建表时提供
    # （测试走 create_all 路径，仍会生效）。
    if bind.dialect.name == "postgresql":
        op.alter_column("sensors", "frame_id", nullable=False)
        op.create_check_constraint(
            "frame_convention_allowed",
            "sensors",
            "frame_convention IN ('ROS_REP_103','OPENCV','UNITY_LHS','UNSPECIFIED')",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("frame_convention_allowed", "sensors", type_="check")
    op.drop_column("sensors", "frame_convention")
    op.drop_column("sensors", "frame_id")
