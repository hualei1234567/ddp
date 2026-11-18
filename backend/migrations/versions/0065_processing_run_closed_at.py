"""N4-006：处理运行失败收口时间 ``closed_at``

Revision ID: 0065
Revises: 0064
Create Date: 2026-11-19

为什么加这一列
--------------
N4-006 需要把 ``FAILED`` 运行收口为"保留错误原因、不可再操作"的终态，但：

- ``CANCELLED`` 不允许携带 ``error_*``（既有 CHECK），从 ``FAILED`` 取消会把
  失败原因永久抹掉；
- 新状态值需要改 ``status`` CHECK 与契约枚举，且仍然表达不了"错误保留"。

因此复用既有 ``FAILED`` 状态，新增可空时间列 ``closed_at``：``NULL`` 表示仍可
按 Worker 重试策略拉起，非空表示已由服务端 ``system-close`` 收口。状态判定只
依赖本列（``ProcessingRun.closed_at`` / ``is_terminal``），**不再从审计表派生**。

为什么只 ADD COLUMN
-------------------
- 本列可空、无默认值，存量行升级后为 ``NULL``（语义为"未收口"），不需要回填；
- SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，加 CHECK 必须重建表；
  ``processing_runs`` 被 ``processing_run_steps`` 外键引用，重建会牵动子表。
  因此 ``closed_at IS NULL OR status = 'FAILED'`` 与
  ``closed_at >= finished_at`` 两条不变量只在领域层
  （``ProcessingRun.__post_init__``）校验，数据库只保证列类型。
- 不加索引：收口状态不是列表主查询条件，主查询仍是
  ``(project_id, status, created_at)``。

回滚
----
``downgrade`` 删除本列。升级后写入的收口时间会丢失，运行回到"``FAILED`` 仍可
重试"的旧语义；行本身与 ``error_*`` 不受影响，且收口动作仍可在审计日志中追溯。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

_TABLE = "processing_runs"
_COLUMN = "closed_at"


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN in _existing_columns():
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    if _COLUMN not in _existing_columns():
        return
    op.drop_column(_TABLE, _COLUMN)
