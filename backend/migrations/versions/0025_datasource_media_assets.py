"""stage7 imported media assets

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-15

阶段 7 第二批：导入产出的媒体资产。

数据库只存元数据、对象存储引用、Hash 与审看所需的时序提示；文件本体在对象存储。
`uq_media_assets_import_path` 保证同一导入内同一路径只出现一次——
重复会导致审看列表出现重复项，这类问题在 UI 上很难排查。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.models.datasources import MediaAssetModel

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    MediaAssetModel.__table__.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    MediaAssetModel.__table__.drop(op.get_bind(), checkfirst=False)
