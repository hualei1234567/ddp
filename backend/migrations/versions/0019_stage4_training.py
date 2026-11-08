"""stage4 training request, delivery, external reference and grant

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09

阶段 4 第三批：TrainingRequest/Revision、TrainingDelivery、外部训练引用、
DatasetGrant 与 Adapter 回调防重放记录。表结构直接复用运行时 ORM 元数据，
触发器由 ``training_guards`` 安装同一份 SQLite/PostgreSQL DDL。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.models.training import (
    DatasetGrantModel,
    ExternalTrainingReferenceModel,
    TrainingAdapterCallbackModel,
    TrainingDeliveryModel,
    TrainingRequestModel,
    TrainingRequestRevisionModel,
)
from app.infrastructure.db.training_guards import (
    drop_training_guards,
    install_training_guards,
)

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_TABLES = (
    TrainingRequestModel.__table__,
    TrainingRequestRevisionModel.__table__,
    TrainingDeliveryModel.__table__,
    ExternalTrainingReferenceModel.__table__,
    DatasetGrantModel.__table__,
    TrainingAdapterCallbackModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)
    install_training_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    drop_training_guards(bind)
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
