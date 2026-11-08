"""stage4 export job

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-09

阶段 4 第二批：ExportJob 异步导出。表结构直接复用运行时 ORM 元数据，
触发器由 ``export_guards`` 安装同一份 SQLite/PostgreSQL DDL。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.export_guards import (
    drop_export_guards,
    install_export_guards,
)
from app.infrastructure.db.models.export_jobs import ExportJobModel

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TABLES = (ExportJobModel.__table__,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)
    install_export_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    drop_export_guards(bind)
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
