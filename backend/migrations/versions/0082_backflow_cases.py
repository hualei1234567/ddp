"""评测失败案例回流表（V52-030，蓝图 N5 数据飞轮入口 / G5）

Revision ID: 0082
Revises: 0080
Create Date: 2026-11-26

背景
----
V5.2 阶段 1/2 已把 LLM Judge、PRM 步级标注接进平台，但**评测平台的失败
案例没有回流通道**：评测发现模型在哪类能力上失败，bad case 只停留在评测
报告里，无法自动变成人工标注任务——蓝图 G5 指出的数据飞轮入口缺失。

本迁移新建 ``annotation_backflow_cases``：一条外部评测失败案例一行。
它是机器回调写入的**事实表**，不是人类操作产物，因此**不套
``AnnotationAuditMixin``**（无 users 外键 actor），口径参考 ``edge_agents``
表——人类操作记录另走审计。

设计要点
--------
- 幂等键 ``(source, case_ref)`` 唯一约束：评测平台重发同一案例时，后到者
  撞唯一键 → 仓储捕获 ``IntegrityError`` 翻译为 ``SKIPPED_DUPLICATE``，
  **绝不冒泡成可重试失败**（MEM-INC-001：无限重试会放大回调风暴）；
- ``status`` 库层 CHECK 枚举：``RECEIVED`` / ``CONVERTED`` /
  ``SKIPPED_DUPLICATE`` / ``RATE_LIMITED``，与领域 ``BackflowStatus`` 一致，
  防绕过应用层直写脏值（MEM-INC-012 的教训：枚举值必须同步库层约束）；
- ``suggested_priority`` 库层 CHECK 枚举 ``P0..P3``（与 ``TaskPriority`` 一致）；
- ``payload`` 用 ``sa.JSON().with_variant(JSONB)``：SQLite 存 TEXT、
  PG 存 JSONB（0038 建立的仓库惯例）；
- ``task_id`` 可空外键：受理但尚未转成任务时为 NULL，转换成功后回填；
- 索引 ``(project_id, received_at)`` 支撑"最近回流"，``(source, status)``
  支撑按来源视角的运营查询（限流窗口统计走应用层批量查询）。

回滚
----
``downgrade`` 删索引再删表，是 upgrade 严格逆操作；不触碰既有表。

双方言（MEM-INC-013）
--------------------
建表 DDL 两侧同构：无 ``ALTER TYPE``、无 ``DROP CONSTRAINT``、无
``pg_constraint`` 查询；唯一约束与 CHECK 均在建表语句内声明。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082"
down_revision = "0083"  # 重排：链 0074→0078→0081→0080→0083→0082（#139 先合）
branch_labels = None
depends_on = None

_TABLE = "annotation_backflow_cases"

_INDEXES = (
    ("ix_backflow_project_received", ["project_id", "received_at"]),
    ("ix_backflow_source_status", ["source", "status"]),
)

_STATUS_ALLOWED_SQL = "('RECEIVED', 'CONVERTED', 'SKIPPED_DUPLICATE', 'RATE_LIMITED')"
_PRIORITY_ALLOWED_SQL = "('P0', 'P1', 'P2', 'P3')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("case_ref", sa.String(length=200), nullable=False),
        sa.Column("capability", sa.String(length=120), nullable=False),
        sa.Column("failure_summary", sa.String(length=1000), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("suggested_priority", sa.String(length=2), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("decision_reason", sa.String(length=1000), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("source", "case_ref", name=f"uq_{_TABLE}_source_case_ref"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["annotation_tasks.id"],
            name=f"fk_{_TABLE}_task_id_annotation_tasks",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            f"status IN {_STATUS_ALLOWED_SQL}",
            name=f"ck_{_TABLE}_status_allowed",
        ),
        sa.CheckConstraint(
            f"suggested_priority IN {_PRIORITY_ALLOWED_SQL}",
            name=f"ck_{_TABLE}_priority_allowed",
        ),
        sa.CheckConstraint("failure_count >= 1", name=f"ck_{_TABLE}_failure_count_positive"),
        sa.CheckConstraint("length(source) > 0", name=f"ck_{_TABLE}_source_non_empty"),
        sa.CheckConstraint("length(case_ref) > 0", name=f"ck_{_TABLE}_case_ref_non_empty"),
        sa.CheckConstraint("length(capability) > 0", name=f"ck_{_TABLE}_capability_non_empty"),
    )
    for name, columns in _INDEXES:
        op.create_index(name, _TABLE, columns)


def downgrade() -> None:
    for name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
