"""stage5 model closed-loop tables and guards

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-09

阶段 5：Model/ModelVersion、Evaluation、InferenceService/Task、ModelFeedback、
DataRequirement 与模型 Adapter 回调防重放表。表结构复用运行时 ORM 元数据，
触发器由 ``modelops_guards`` 安装同一份 SQLite/PostgreSQL DDL。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.modelops_guards import (
    drop_modelops_guards,
    install_modelops_guards,
)
from app.infrastructure.db.models.modelops import (
    DataRequirementRow,
    EvaluationRow,
    InferenceServiceRow,
    InferenceTaskRow,
    ModelCallbackRow,
    ModelFeedbackRow,
    ModelRow,
    ModelVersionRow,
)

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_TABLES = (
    ModelRow.__table__,
    ModelVersionRow.__table__,
    EvaluationRow.__table__,
    InferenceServiceRow.__table__,
    InferenceTaskRow.__table__,
    ModelFeedbackRow.__table__,
    DataRequirementRow.__table__,
    ModelCallbackRow.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)
    install_modelops_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    drop_modelops_guards(bind)
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
