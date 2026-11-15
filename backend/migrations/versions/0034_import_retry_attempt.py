"""导入任务可重试：data_source_imports 增加 attempt / max_attempts

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-16

背景（P0-4 重试自锁）
--------------------
Worker 执行导入失败时走 ``ImportRunner._fail`` 把任务写成 ``FAILED``；
``FAILED`` 按契约是终态（"终态之后任何动作都返回 state_transition_invalid"）。
但同一时刻 Worker 已经把那条 outbox 事件 ``mark_retry`` 排到下一轮，
下一轮 ``ImportRunner.execute`` 又要求任务状态为 ``PENDING``——

    Worker 失败 -> 任务 FAILED（终态）
                 -> 事件 mark_retry（下一轮再试）
                 -> 下一轮 execute 要求 PENDING -> 直接 raise
                 -> 事件反复重试，直到死信

即：**任何一次可恢复的失败（网络抖动、HF 限流）都会让这条导入永久卡死**，
而且没有任何 API 能救——FAILED 不接受任何客户端动作。

修法与 ``ingestion_jobs`` 完全同构（那套已存在且受测）：
- 增加 ``attempt`` / ``max_attempts``；
- 状态机增加**系统**动作 ``retry``：``FAILED -> PENDING``，带回 attempt+1；
- 它**不在** ``_IMPORT_ACTIONS`` 里，因此既不出现在 ``allowed_actions``，
  也不在契约 ``ImportAction`` 枚举中——客户端触发不了，只有 Worker 用；
- ``attempt >= max_attempts`` 时不再退，老老实实留在 FAILED 等人处理。

历史数据回填为 ``attempt=1, max_attempts=3``：既有的 FAILED 行确实只试过
一次，且不会因为本迁移突然变得"可以重试"——是否重试仍由 Worker 依据
事件的重试轮次判断，本迁移只提供字段。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


# SQLite 不支持 ``ALTER ... ADD CONSTRAINT``，必须走 batch 模式（重建表）。
# PostgreSQL 原生支持，但用同一份 batch 代码既能覆盖两方言，也避免
# "本地 SQLite 测试绿、真实 PG 才炸"这类只有上线才发现的分叉。
_ADDED_COLUMNS = (
    sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
    sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
)

_CHECK_CONSTRAINTS = (
    sa.CheckConstraint("attempt >= 1", name="import_attempt_positive"),
    sa.CheckConstraint("max_attempts >= 1", name="import_max_attempts_positive"),
    sa.CheckConstraint("attempt <= max_attempts", name="import_attempt_within_max"),
)


def upgrade() -> None:
    with op.batch_alter_table("data_source_imports", recreate="auto") as batch:
        for column in _ADDED_COLUMNS:
            batch.add_column(column)
        for constraint in _CHECK_CONSTRAINTS:
            batch.create_check_constraint(constraint.name, constraint.sqltext)


def downgrade() -> None:
    with op.batch_alter_table("data_source_imports", recreate="auto") as batch:
        for constraint in _CHECK_CONSTRAINTS:
            batch.drop_constraint(constraint.name, type_="check")
        batch.drop_column("max_attempts")
        batch.drop_column("attempt")
