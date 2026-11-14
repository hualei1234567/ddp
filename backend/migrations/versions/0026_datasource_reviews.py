"""stage7 data source reviews

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-15

阶段 7 第三批：导入结果的人工审核记录。

`review_segregation_of_duties` 把"审核人不得是提交人"写进数据库：
应用层也拦，但这类规则一旦被绕过就是合规问题，值得兜底。
`uq_reviews_project_import` 保证一个导入只有一条审核记录——
多条会让"是否通过"的判定含糊。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.models.datasources import DataReviewModel

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    DataReviewModel.__table__.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    DataReviewModel.__table__.drop(op.get_bind(), checkfirst=False)
