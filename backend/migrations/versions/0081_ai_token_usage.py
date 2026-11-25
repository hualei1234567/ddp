"""Token 成本台账：ai_token_usage（V52-010，蓝图 N6）

Revision ID: 0081
Revises: 0074
Create Date: 2026-11-25

背景（N6 成本经营缺口）
--------------------
V5.2 阶段 1 的 judge 路由与 PRM 导出已把 LLM 调用接入预标注与评审，
但每次调用的 token 消耗、折算成本没有任何计量：成本只存在于供应商账单，
平台侧无法回答「哪个项目、哪个模型、哪种用途烧了多少」，更无法与
节省人工工时做 ROI 对比。本迁移新建计量事实表 ``ai_token_usage``：
一次 LLM 调用一行，成本归属键与 ``annotation_ai_suggestions`` 的模型
身份三列同源（``model_id`` / ``model_version`` / ``prompt_version``）。

设计要点
--------
- ``purpose`` 用库层 CHECK 枚举（``PRE_LABEL`` / ``LLM_JUDGE`` / ``EMBED``），
  与领域 :class:`~app.domain.annotation.token_usage.TokenUsagePurpose` 一致，
  防绕过应用层直写脏值；
- ``cost_estimate`` 用 ``Numeric(14, 4)``：金额是货币值，浮点误差随聚合放大；
  ``NULL`` 表示"未配置价格"（不是 0——0 是真免费，NULL 是漏配，语义分开），
  ``CHECK cost_estimate >= 0`` 对 NULL 恒真（SQL 三值逻辑），不会误拒未计价行；
- ``occurred_at`` 用 timezone-aware ``DateTime``，与全库时间语义一致；
- 索引两条： ``(project_id, occurred_at)`` 支撑时间窗聚合（"本月花了多少"），
  ``(project_id, model_id, purpose)`` 支撑成本归属聚合（按模型/用途切分）；
- 项目与审计外键由列级 ``ForeignKey``（``projects`` / ``users``）声明，
  与 ORM :class:`~app.infrastructure.db.models.annotation_ai_token_usage.AiTokenUsage`
  的 ``AnnotationAuditMixin`` 完全一致。

**为什么用显式 ``op.create_table`` 而不是 ``Model.__table__.create()``**
历史迁移必须是历史快照，不得随 ORM 漂移（0025 的教训），见
``backend/tests/migrations/test_migrations_are_immutable_snapshots.py``。

回滚
----
``downgrade`` 先删索引再删表，是 upgrade 的严格逆操作；本迁移不触碰
既有表，因此回滚不丢任何既有数据（新表数据随表删除）。

双方言（MEM-INC-013）
--------------------
建表 DDL 两侧同构：无 ``ALTER TYPE``、无 ``DROP CONSTRAINT``、无
``pg_constraint`` 查询，SQLite 与 PostgreSQL 都走同一条路径；
迁移测试在 SQLite 上验证 upgrade → downgrade 往返与约束行为。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0074"
branch_labels = None
depends_on = None

_TABLE = "ai_token_usage"

_INDEXES = (
    ("ix_ai_token_usage_project_occurred", ["project_id", "occurred_at"]),
    ("ix_ai_token_usage_project_model_purpose", ["project_id", "model_id", "purpose"]),
)

_PURPOSE_ALLOWED_SQL = "('PRE_LABEL', 'LLM_JUDGE', 'EMBED')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_estimate", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("annotation_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            f"purpose IN {_PURPOSE_ALLOWED_SQL}",
            name=f"ck_{_TABLE}_purpose_allowed",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0",
            name=f"ck_{_TABLE}_input_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "output_tokens >= 0",
            name=f"ck_{_TABLE}_output_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "cost_estimate >= 0",
            name=f"ck_{_TABLE}_cost_non_negative",
        ),
        sa.CheckConstraint(
            "length(model_id) > 0",
            name=f"ck_{_TABLE}_model_id_non_empty",
        ),
        sa.CheckConstraint(
            "length(model_version) > 0",
            name=f"ck_{_TABLE}_model_version_non_empty",
        ),
        sa.CheckConstraint(
            "prompt_version IS NULL OR length(prompt_version) > 0",
            name=f"ck_{_TABLE}_prompt_version_non_empty",
        ),
    )
    for index_name, columns in _INDEXES:
        op.create_index(index_name, _TABLE, columns)


def downgrade() -> None:
    for index_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
