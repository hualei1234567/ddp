"""training_deliveries.requirement_match

Revision ID: 0038
Revises: 0032
Create Date: 2026-11-16

TRN-002：交付时必须把「已批准需求快照」与「实际交付的数据集版本」核对一遍。
此前只校验 revision 已批准、版本 FROZEN、export job 匹配——「交付的是不是
需求要的东西」完全靠人工，属于业务正确性缺口。核对结果需要随交付单留证，
因此新增 JSON 列 ``training_deliveries.requirement_match``。

列语义
------
- 存 ``MatchReport.to_payload()`` 的稳定载荷（逐维度 expected/actual/status）。
- **可空**：本列上线前创建的交付没有核对记录。NULL 表示"未核对"，
  **不等于**通过；读取方不得把 NULL 折叠成 PASSED。
- 写入后不可变：``DELIVERY_IMMUTABLE_COLUMNS`` 已加入该列，应用层与
  ``training_guards`` 的触发器共用同一份清单，防止事后把 FAILED 改成 PASSED。

为什么需要存在性判断（不是防御性编程，是真实的两条路径）
--------------------------------------------------------
``0019_stage4_training.py`` 用 ``TrainingDeliveryModel.__table__.create()`` 建表，
而该 ORM 模型**已经**包含本次新增的列。于是在两条路径上本迁移要做的事不同：

1. **全新库**：``upgrade head`` 依次执行 0019（此时列已随 ORM 元数据建出）
   再到 0038 → 列为空操作。若直接 ``add_column`` 会因列重复而失败。
2. **存量库**：已升级到 0032 的库，0019 早已执行完，表里没有该列 →
   必须 ``add_column``。

因此 upgrade/downgrade 都以 ``inspector`` 的实际列状态为准，两个方向都幂等，
可反复升级/降级。回滚会丢弃已写入的核对报告——它们是可由同一快照与版本事实
重算的派生结论，不是唯一真相来源。

为什么不沿用 ``Model.__table__.create()``
----------------------------------------
那是"从零建整表"的写法（0017–0021 的场景）。本迁移只改一列，重建整表会连带
丢掉 ``training_deliveries`` 上 4 个触发器与其复合外键，风险与收益完全不成
比例。显式 ``add_column``/``drop_column`` 才是可审查、可回滚的最小操作。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0038"
down_revision = "0032"
branch_labels = None
depends_on = None

_TABLE = "training_deliveries"
_COLUMN = "requirement_match"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _has_column():
        # 全新库路径：0019 已按 ORM 元数据建出该列。
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.JSON().with_variant(JSONB, "postgresql"), nullable=True),
    )


def downgrade() -> None:
    if not _has_column():
        return
    op.drop_column(_TABLE, _COLUMN)
