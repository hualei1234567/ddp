"""robot status semantics

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-15

G-01 前置：Robot 状态语义字段与 Device/Sensor 对齐。

G-09 当时只给 devices / sensors 补了 ``status_reason`` / ``fault_*`` /
``status_changed_*``，robots 表漏了。补 Robot 路由时发现：状态动作的
reason 与 fault 信息在 Robot 上无处持久化，等于"点挂起但不知道为什么"。
本迁移补齐六列，全部可空，对既有行无影响，可完整回滚。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("status_reason", sa.String(500)),
    ("fault_code", sa.String(64)),
    ("fault_detail", sa.String(2000)),
    ("fault_occurred_at", sa.DateTime(timezone=True)),
    ("status_changed_at", sa.DateTime(timezone=True)),
    # 不加外键：actor 溯源列不做强引用，用户离开项目后历史仍需可读。
    ("status_changed_by", sa.Uuid()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("robots", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("robots", name)
