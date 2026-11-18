"""外部训练引用记录执行方式

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-18

为什么加这一列：此前训练引用只记录"提交给了哪个 Adapter"，不记录"在哪里跑"。
同一部署里可能既有 ``local-training``（本机 CPU 参考实现，真实执行）又有真实
远端训练平台；响应里没有执行方式，Mock/本地结果就会被当成真实训练结果。

**存量为什么回填 ``UNKNOWN`` 而不是 ``REMOTE``**

迁移执行时拿不到部署当时的 Adapter 配置（``DDP_TRAINING_ADAPTER_EXECUTION_MODES``
是运行时环境变量），历史行既可能是本地参考实现、也可能是真实远端平台，无法回溯
判定。把一个未知事实标成 ``REMOTE`` 正是本任务要杜绝的"冒充真实训练"；按
``local-training`` 之类的 ID 硬编码映射又违反"禁止硬编码部署配置"。因此存量统一
回填 ``UNKNOWN``，由前端显式标注"执行方式未知（迁移前记录）"。

``UNKNOWN`` 只用于存量：新提交必须由 Adapter 配置或请求显式声明
LOCAL/MOCK/DRY_RUN/REMOTE 之一，否则服务端拒绝（422），不会写入本列。

**为什么这里要判列是否存在**：建表迁移 0019 直接用运行时 ORM 元数据建表，
全新库升级到 0054 之前，0019 建出的表已经带 ``execution_mode`` 与对应 CHECK
约束；此时再 ADD COLUMN 会 ``duplicate column``。与 0051 采用同一处理方式。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_TABLE = "external_training_references"
_COLUMN = "execution_mode"
_CONSTRAINT = "execution_mode_allowed"
# 迁移是历史快照：这里的取值必须冻结在 0054 时点，不能引用会随代码演进的 Enum，
# 否则未来新增枚举值会悄悄改写这条历史迁移的约束。
_MODE_VALUES = "'LOCAL', 'MOCK', 'DRY_RUN', 'REMOTE', 'UNKNOWN'"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _has_column():
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(
            sa.Column(
                _COLUMN,
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'UNKNOWN'"),
            )
        )
        batch.create_check_constraint(_CONSTRAINT, f"{_COLUMN} IN ({_MODE_VALUES})")


def downgrade() -> None:
    if not _has_column():
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.drop_column(_COLUMN)
