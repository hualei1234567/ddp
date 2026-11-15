"""outbox dispatch semantics

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-15

R-13 / T-1、T-2：把 ``outbox_events`` 的两类职责在数据层显式区分。

- ``dispatch = true``（投递类）：有执行方需要据此做事，写入 ``PENDING``，
  由 worker 抢占、重试、必要时进死信。
- ``dispatch = false``（留痕类）：只记录"发生了什么"，写入即 ``RECORDED`` 终态，
  不进重试队列，也不会被 worker 捞起来。

兼容性：存量行全部按"投递类"回填（``dispatch = true``），保持原有行为不变——
它们此前都是 ``PENDING`` 等消费者。若把存量改成留痕类，等于把可能待投递的消息
静默丢掉。新写入的行由应用层显式决定。

``RECORDED`` 加入 status 的 CHECK 允许值；``dispatch_status_consistent``
把两类语义的对应关系写进数据库，避免"投递消息被标成已记录"。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_STATUS_OLD = "status IN ('PENDING', 'PUBLISHED', 'FAILED')"
_STATUS_NEW = "status IN ('PENDING', 'PUBLISHED', 'FAILED', 'RECORDED')"


def _dialect() -> str:
    return op.get_bind().dialect.name


def _rebuild_sqlite_outbox(*, values: str, with_dispatch: bool) -> None:
    """SQLite 无法 ALTER 约束：重建表以替换 status 的 CHECK。

    SQLite 只用于测试与本地；PostgreSQL 走下面的原生约束分支。
    重建时显式列出所有列，避免遗漏后续新增列——列清单变化会让本函数失效，
    因此一旦有列变动就必须同步这里（迁移是历史快照，不改动历史版本）。
    """
    dispatch_col = "            dispatch BOOLEAN NOT NULL DEFAULT 0,\n" if with_dispatch else ""
    # 注意逗号位置：这是最后一个约束，带 dispatch 时前面要多一个逗号。
    trailing_check = (
        "            ,CONSTRAINT dispatch_status_consistent CHECK (\n"
        "                (dispatch = 0 AND status = 'RECORDED')\n"
        "                OR (dispatch = 1 AND status <> 'RECORDED')\n"
        "            )"
        if with_dispatch
        else ""
    )
    dispatch_index = (
        "CREATE INDEX ix_outbox_events_dispatch_status "
        "ON outbox_events (dispatch, status, available_at)"
        if with_dispatch
        else None
    )
    op.execute("ALTER TABLE outbox_events RENAME TO outbox_events_old")
    # 这是一段 DDL 模板，插入的 values/with_dispatch 都来自本模块常量，
    # 不含外部输入；SQLite 不支持参数化 DDL，只能拼字符串。
    op.execute(  # noqa: S608
        f"""
        CREATE TABLE outbox_events (
            id CHAR(32) NOT NULL PRIMARY KEY,
            project_id CHAR(32) NOT NULL REFERENCES projects(id),
            aggregate_type VARCHAR(64) NOT NULL,
            aggregate_id CHAR(32) NOT NULL,
            event_type VARCHAR(128) NOT NULL,
            payload JSON NOT NULL DEFAULT '{{}}',
            status VARCHAR(16) NOT NULL,
{dispatch_col}            attempts INTEGER NOT NULL,
            available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_at DATETIME,
            last_error VARCHAR(2000),
            CONSTRAINT status_allowed CHECK (status IN ({values})),
            CONSTRAINT attempts_non_negative CHECK (attempts >= 0)
{trailing_check}        )
        """
    )
    columns = (
        "id, project_id, aggregate_type, aggregate_id, event_type, payload, status, "
        + ("dispatch, " if with_dispatch else "")
        + "attempts, available_at, created_at, published_at, last_error"
    )
    insert_sql = f"INSERT INTO outbox_events ({columns}) SELECT {columns} FROM outbox_events_old"  # noqa: S608
    op.execute(insert_sql)  # noqa: S608 —— 列名来自本模块常量，无外部输入
    op.execute("DROP TABLE outbox_events_old")
    op.execute(
        "CREATE INDEX ix_outbox_events_status_available_at "
        "ON outbox_events (status, available_at)"
    )
    if dispatch_index is not None:
        op.execute(dispatch_index)
    op.execute(
        "CREATE INDEX ix_outbox_events_project_aggregate "
        "ON outbox_events (project_id, aggregate_type, aggregate_id)"
    )


def upgrade() -> None:
    # SQLite 不支持 ALTER ADD CONSTRAINT，因此先加列（带默认值），
    # 约束由 ORM 的 __table_args__ 在 create_all 时建立（测试路径），
    # 生产路径由下面的 PG 分支显式建立。
    op.add_column(
        "outbox_events",
        sa.Column("dispatch", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # 存量行一律视为投递类：它们本来就在等消费者。
    op.execute("UPDATE outbox_events SET dispatch = true WHERE status = 'PENDING'")

    if _dialect() == "sqlite":
        _rebuild_sqlite_outbox(
            values="'PENDING', 'PUBLISHED', 'FAILED', 'RECORDED'", with_dispatch=True
        )
        return

    if _dialect() == "postgresql":
        # Alembic 的命名约定会自动加 ``ck_<table>_`` 前缀；drop/create 都传裸名。
        op.drop_constraint("status_allowed", "outbox_events", type_="check")
        op.create_check_constraint("status_allowed", "outbox_events", _STATUS_NEW)
        op.create_check_constraint(
            "dispatch_status_consistent",
            "outbox_events",
            "(dispatch = false AND status = 'RECORDED') "
            "OR (dispatch = true AND status <> 'RECORDED')",
        )
        op.create_index(
            "ix_outbox_events_dispatch_status",
            "outbox_events",
            ["dispatch", "status", "available_at"],
        )


def downgrade() -> None:
    if _dialect() == "sqlite":
        op.execute("UPDATE outbox_events SET status = 'PUBLISHED' WHERE status = 'RECORDED'")
        _rebuild_sqlite_outbox(values="'PENDING', 'PUBLISHED', 'FAILED'", with_dispatch=False)
        return

    if _dialect() == "postgresql":
        op.drop_index("ix_outbox_events_dispatch_status", table_name="outbox_events")
        op.drop_constraint("dispatch_status_consistent", "outbox_events", type_="check")
        # RECORDED 行在旧约束下非法，回滚前必须把它们落回已知状态。
        op.execute("UPDATE outbox_events SET status = 'PUBLISHED' WHERE status = 'RECORDED'")
        op.drop_constraint("status_allowed", "outbox_events", type_="check")
        op.create_check_constraint("status_allowed", "outbox_events", _STATUS_OLD)
    op.drop_column("outbox_events", "dispatch")
