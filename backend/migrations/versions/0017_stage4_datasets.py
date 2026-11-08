"""stage4 dataset and dataset version

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-09

阶段 4 第一批：Dataset、DatasetVersion、DatasetItem、去重/划分/泄漏/质量策略、
泄漏报告与质量报告。表结构直接复用运行时 ORM 元数据，避免迁移与模型漂移；
触发器由 ``dataset_guards`` 安装同一份 SQLite/PostgreSQL DDL。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.dataset_guards import (
    drop_dataset_guards,
    install_dataset_guards,
)
from app.infrastructure.db.models.dataset_policies import (
    DatasetDedupPolicyModel,
    DatasetLeakagePolicyModel,
    DatasetQualityPolicyModel,
    DatasetSplitPolicyModel,
)
from app.infrastructure.db.models.dataset_reports import (
    DatasetLeakageReportModel,
    DatasetQualityReportModel,
)
from app.infrastructure.db.models.datasets import (
    DatasetItemModel,
    DatasetModel,
    DatasetVersionModel,
)

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_TABLES = (
    DatasetModel.__table__,
    DatasetVersionModel.__table__,
    DatasetItemModel.__table__,
    DatasetDedupPolicyModel.__table__,
    DatasetSplitPolicyModel.__table__,
    DatasetLeakagePolicyModel.__table__,
    DatasetQualityPolicyModel.__table__,
    DatasetLeakageReportModel.__table__,
    DatasetQualityReportModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)
    install_dataset_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    drop_dataset_guards(bind)
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
