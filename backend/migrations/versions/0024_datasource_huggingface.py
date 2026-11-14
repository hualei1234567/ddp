"""stage7 data source registry

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-14

阶段 7 第一批：外部数据源登记与导入记录（`docs/ddp/07` §2）。

两处关键约束直接写进数据库，而不是只靠应用层：

- ``revision_is_commit_sha``：revision 必须是 40 位 commit sha。``main`` 会移动，
  只存分支名的导入不可复现——让非法值根本进不了库。
- ``gated_consistency``：``access_condition='GATED'`` 与 ``is_gated`` 必须一致，
  防止"标成公开、实际要申请"导致合规误判。

不新建审计/outbox/幂等表：复用既有横切表。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
)

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_TABLES = (
    DataSourceModel.__table__,
    DataSourceImportModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
