"""stage4 reinstall training guards

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-09

DDP-329 发现外部训练引用守卫错误使用 ``status`` 列（实际列为
``external_status``）。守卫 DDL 由应用模块生成，已迁移到 0019 的库
执行 ``upgrade head`` 不会自动重装，本迁移显式重装训练域守卫。

downgrade 不回滚安全修复：外部引用的合法状态迁移必须保持可用。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.training_guards import install_training_guards

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_training_guards(op.get_bind())


def downgrade() -> None:
    pass
