"""device and sensor status semantics

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-14

阶段 7 前置：设备与传感器状态需要表达"为什么进入该状态"。

此前动作请求体（``ActionRequest`` 的 reason / fault_code / fault_detail /
occurred_at）在路由层被 ``del payload`` 丢弃（审计 G-10），设备域也没有任何
审计与 outbox 写入（G-11）。本迁移补齐持久化列；审计与 outbox 复用既有横切表
``audit_logs`` / ``outbox_events``，不新建表。

列全部可空，对既有行无影响；``downgrade`` 可完整回滚。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_TABLES = ("devices", "sensors")


def _status_columns() -> list[sa.Column]:
    """每次调用返回全新的 Column 实例：Column 不可跨表复用，copy() 已弃用。

    不加外键：SQLite 不支持 ALTER 追加约束，且 actor 溯源列不需要强引用
    （用户离开项目后历史仍需可读）。取值由应用层从 ProjectScope 写入。
    """
    return [
        sa.Column("status_reason", sa.String(1000), nullable=True),
        sa.Column("fault_code", sa.String(64), nullable=True),
        sa.Column("fault_detail", sa.String(2000), nullable=True),
        sa.Column("fault_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by", sa.Uuid(), nullable=True),
    ]


def _add_status_columns(table: str) -> None:
    # 一律用原生 ADD COLUMN：SQLite 与 PostgreSQL 都支持，且不触发
    # batch 模式在 SQLite 上的 copy-and-move（会临时改名表，连带引用的
    # 其它表触发器失效）。
    for column in _status_columns():
        op.add_column(table, column)


def _drop_status_columns(table: str) -> None:
    # 原生 DROP COLUMN（SQLite 3.35+ / PostgreSQL 均支持）。不用 batch：
    # 它在 SQLite 上做 copy-and-move（临时改名表），会让引用该表的其它
    # 触发器失效。
    for column in (
        "status_changed_by",
        "status_changed_at",
        "fault_occurred_at",
        "fault_detail",
        "fault_code",
        "status_reason",
    ):
        op.drop_column(table, column)


def upgrade() -> None:
    for table in _TABLES:
        _add_status_columns(table)


def downgrade() -> None:
    for table in _TABLES:
        _drop_status_columns(table)
