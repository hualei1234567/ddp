"""stage6 supplier and inbound acceptance

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-14

阶段 6：供应商档案、到货批次、到货片段与只追加验收账本。表结构复用运行时
ORM 元数据；状态迁移与账本禁改触发器由 ``suppliers_guards`` 安装同一份 DDL。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.models.suppliers import (
    AcceptanceLedgerModel,
    DeliveryBatchModel,
    InboundSegmentModel,
    SupplierModel,
)
from app.infrastructure.db.suppliers_guards import (
    drop_suppliers_guards,
    install_suppliers_guards,
)

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_TABLES = (
    SupplierModel.__table__,
    DeliveryBatchModel.__table__,
    InboundSegmentModel.__table__,
    AcceptanceLedgerModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)
    install_suppliers_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    drop_suppliers_guards(bind)
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
