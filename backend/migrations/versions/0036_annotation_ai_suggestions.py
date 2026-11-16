"""标注 AI 建议独立存储：annotation_ai_suggestions

Revision ID: 0036
Revises: 0032
Create Date: 2026-11-16

背景（N4 / QUA-005）
--------------------
现状审计确认：AI 结果与人工结果写在**同一张表、同一 JSONB 字段**
（``annotation_records.labels``），仅靠每个标签的 ``source`` 枚举
（``HUMAN`` / ``AI_ASSISTED`` / ``IMPORTED``）与可选 ``confidence`` 区分。
由此产生三个已实测的缺口：

1. **模型身份完全没落库**：``labels`` 里不存在 ``model_id`` /
   ``model_version`` / ``prompt_version``，无法追溯"哪批标签来自哪个模型版本"；
2. **人工结果覆盖 AI 建议**：同名字段一旦人工填写，原始建议即丢失，
   修订幅度无法计算；
3. ``source=AI_ASSISTED`` 且不给 ``confidence`` 也能通过领域校验——
   AI 来源与置信度之间没有任何强制关系。

N7 的「AI 建议 vs 人工修订」统计依赖第 1、2 点，当前实现无法支撑。

本迁移新建 ``annotation_ai_suggestions``：一份建议一行，``suggested_value``
与模型身份三列保留模型输出，``human_value`` / ``decided_by`` / ``decided_at``
保留人工决定。读既有表结构、不改 ``annotation_records`` 的任何列与语义——
那是受测的既有行为，改动风险远大于收益。

设计要点
--------
- ``(project_id, annotation_id)`` 复合外键指向 ``annotation_records``，
  与同域其它表一致，项目隔离由外键而非应用层自觉保证；
- ``(project_id, annotation_id, label_key, model_id, model_version,
  prompt_version)`` 唯一：同一模型版本对同一标签只留一条建议，换模型或换
  Prompt 版本自然产生新行，从而支持多模型横向对比；
- ``confidence`` 与 ``model_*`` 列的 CHECK 把领域约束在库里再兜一层：
  应用层被绕过时，空模型版本、越界置信度、``human_confirmed`` 与人工值
  不一致都会在写库时报错，而不是留下无法解释的数据。
- ``decided_by`` 走 ``(project_id, user_id)`` 指向 ``project_members``：
  决定人必须是本项目成员（与 ``annotation_submissions.annotator_id`` 同构）。

**为什么用显式 ``op.create_table`` 而不是 ``Model.__table__.create()``**
仓库刚因后者在全新库升级时失败（0025 的 ``MediaAssetModel.__table__`` 把当时
不存在的列一起建出来，导致后续迁移 duplicate column）。历史迁移必须是历史
快照，不得随 ORM 模型漂移，见
``backend/tests/migrations/test_migrations_are_immutable_snapshots.py``。

回滚
----
``downgrade`` 先删索引再删表，是 upgrade 的严格逆操作；本迁移不触碰既有表，
因此回滚不丢任何既有数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0032"
branch_labels = None
depends_on = None

_TABLE = "annotation_ai_suggestions"

# 与 ORM 一致：SQLite 用 JSON，PostgreSQL 用 JSONB（可索引、比较语义明确）。
#
# ``none_as_null=True`` 与 ORM 保持一致，理由是 ``decision_consistency`` 这条
# CHECK 用 ``human_value IS NULL`` 判定"未确认"。默认行为下 Python ``None``
# 会被序列化成 JSON 字面量而不是 SQL NULL，CHECK 因而不成立——实测在 SQLite
# 上会让每一条"未确认"的建议都写不进去。迁移创建的列类型本身不带这个开关
# （它属于绑定/序列化层），但两边必须表达同一语义，故此处显式声明。
_JSON_TYPE = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True),
    "postgresql",
)

# 与领域规则同义（domain/annotation/ai_suggestion.py）；库层是防绕过应用层的兜底。
_DECISION_CONSISTENCY = (
    "(human_confirmed = false"
    " AND human_value IS NULL AND decided_by IS NULL AND decided_at IS NULL)"
    " OR (human_confirmed = true"
    " AND human_value IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)"
)

_INDEXES = (
    (
        "ix_annotation_ai_suggestions_project_annotation",
        ["project_id", "annotation_id"],
    ),
    (
        "ix_annotation_ai_suggestions_project_model",
        ["project_id", "model_id", "model_version"],
    ),
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("annotation_id", sa.Uuid(), nullable=False),
        sa.Column("label_key", sa.String(length=100), nullable=False),
        sa.Column("suggested_value", _JSON_TYPE, nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "human_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("human_value", _JSON_TYPE, nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("project_id", "id", name=f"uq_{_TABLE}_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "annotation_id",
            "label_key",
            "model_id",
            "model_version",
            "prompt_version",
            name=f"uq_{_TABLE}_identity",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_updated_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "annotation_id"],
            ["annotation_records.project_id", "annotation_records.id"],
            name=f"fk_{_TABLE}_project_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "decided_by"],
            ["project_members.project_id", "project_members.user_id"],
            name=f"fk_{_TABLE}_project_decided_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f(f"ck_{_TABLE}_confidence_range"),
        ),
        sa.CheckConstraint(
            "length(label_key) > 0",
            name=op.f(f"ck_{_TABLE}_label_key_non_empty"),
        ),
        sa.CheckConstraint(
            "length(model_id) > 0",
            name=op.f(f"ck_{_TABLE}_model_id_non_empty"),
        ),
        sa.CheckConstraint(
            "length(model_version) > 0",
            name=op.f(f"ck_{_TABLE}_model_version_non_empty"),
        ),
        sa.CheckConstraint(
            "length(prompt_version) > 0",
            name=op.f(f"ck_{_TABLE}_prompt_version_non_empty"),
        ),
        sa.CheckConstraint(
            _DECISION_CONSISTENCY,
            name=op.f(f"ck_{_TABLE}_decision_consistency"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f(f"ck_{_TABLE}_version_positive"),
        ),
    )
    for index_name, columns in _INDEXES:
        op.create_index(index_name, _TABLE, columns)


def downgrade() -> None:
    for index_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
