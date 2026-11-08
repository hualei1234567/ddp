"""fix calibration immutable guard json comparisons

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08

PostgreSQL 的 ``json`` 类型没有等号运算符，0004 安装的触发器在比较
``parameters``/``evidence_asset_ids``/``uncertainty`` 时会报
``operator does not exist: json = json``。本迁移用 ``::text`` 比较重新安装
同一触发器；SQLite 路径也会重建等价触发器，行为不变。

downgrade 不恢复有缺陷的 0004 函数：触发器保持可用，避免降级后再次引入
运行期错误；迁移版本号仍可回退。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.guards import install_calibration_record_guards

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_calibration_record_guards(op.get_bind())


def downgrade() -> None:
    # 保留修复后的触发器；降级到 0004 不应恢复已知缺陷。
    pass
