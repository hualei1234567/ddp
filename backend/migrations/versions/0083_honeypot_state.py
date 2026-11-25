"""黄金题熔断状态表：annotation_honeypot_state（V52-008，蓝图 N3 Honeypot）

Revision ID: 0083
Revises: 0080
Create Date: 2026-11-26

背景（黄金题接线，阶段 3 T5 前置）
--------------------------------
阶段 1 的 :class:`~app.domain.annotation.honeypot_breaker.HoneypotBreaker`
已钉死领域规则（连续 2 个满窗口准确率 <85% → PAUSED），但它是纯内存
状态机：进程重启即丢失。本迁移新建按 ``(project_id, annotator_id)``
唯一的状态行，把滑窗命中数（``hits`` / ``total``）、熔断状态
（``NORMAL`` / ``PAUSED``）与暂停原因持久化，供派单入口消费。

设计要点
--------
- ``state`` 用库层 CHECK 枚举（``NORMAL`` / ``PAUSED``），与领域
  :class:`~app.domain.annotation.honeypot_breaker.BreakerState` 一致，
  防绕过应用层直写脏值；
- ``(project_id, annotator_id)`` 唯一：一个标注员在一个项目只有一行
  熔断状态（黄金题按项目隔离，跨项目不共享窗口）；
- ``hits`` / ``total`` 非负且 ``hits <= total``：库层 CHECK 兜底，
  领域层 ``WindowStat`` 有同款不变式；
- ``window_history`` 是滑窗的忠实记录（``0``/``1`` 字符序列，最长
  ``window_size * consecutive_windows`` = 20）：**聚合值（hits/total）
  丢失提交顺序，无法忠实重放"按顺序分窗"的判定**——实测反例：两窗各
  80%（0.8/0.8 → 真实 PAUSED）与"窗1 100% + 窗2 60%"（→ NORMAL）聚合
  同为 hits=16/total=20。重放必须用序列，hits/total 只作为派生聚合列
  （运维可读性 + 库层约束兜底）；
- ``paused_reason`` / ``paused_at`` 仅 PAUSED 时有值（NORMAL 时置空）；
- 索引一条： ``(project_id, state)`` 支撑"本项目有哪些人被熔断"的
  运维查询；行定位靠唯一约束即可，不另建行级索引；
- 项目与审计外键由列级 ``ForeignKey``（``projects`` / ``users``）声明，
  与 ORM :class:`~app.infrastructure.db.models.annotation_honeypot_state.
  AnnotationHoneypotState` 的 ``AnnotationAuditMixin`` 完全一致。

**为什么用显式 ``op.create_table`` 而不是 ``Model.__table__.create()``**
历史迁移必须是历史快照，不得随 ORM 漂移（0025 的教训），见
``backend/tests/migrations/test_migrations_are_immutable_snapshots.py``。

**为什么是普通建表而不是 batch_alter_table**
本迁移只新建独立表，不改任何既有表：不触碰 ``annotation_tasks`` 等
被守卫触发器引用的表，MEM-INC-020 的 drop→改表→重装流程不适用。

回滚
----
``downgrade`` 先删索引再删表，是 upgrade 的严格逆操作；本迁移不触碰
既有表，因此回滚不丢任何既有数据（新表数据随表删除）。

双方言（MEM-INC-013）
--------------------
建表 DDL 两侧同构，唯一分歧是 ``window_history`` 的二进制字符校验：
SQLite 用 ``GLOB``（``[^01]`` 字符类语义）、PostgreSQL 用 ``!~``（POSIX
正则）——``LIKE`` 在两者里都不支持字符类取反，必须按 dialect 分支。
除这一条 CHECK 外无 ``ALTER TYPE``、无 ``DROP CONSTRAINT``、无
``pg_constraint`` 查询；迁移测试在 SQLite 上验证 upgrade → downgrade
往返与约束行为，真实 PG 由配 ``DDP_TEST_POSTGRES_URL`` 的用例覆盖。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0080"  # main 实际 head：链 0074→0078→0081→0080→0083
branch_labels = None
depends_on = None

_TABLE = "annotation_honeypot_state"

_INDEXES = (("ix_annotation_honeypot_state_project_state", ["project_id", "state"]),)

_STATE_ALLOWED_SQL = "('NORMAL', 'PAUSED')"


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    # ``LIKE`` 在 SQLite/PG 都不支持字符类取反：SQLite 用 GLOB、PG 用正则。
    if dialect == "postgresql":
        _BINARY_CHECK = "window_history !~ '[^01]'"
    else:
        _BINARY_CHECK = "window_history NOT GLOB '*[^01]*'"
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("annotator_id", sa.Uuid(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("window_history", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("paused_reason", sa.String(length=2000), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "project_id",
            "annotator_id",
            name=f"uq_{_TABLE}_project_annotator",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["annotator_id"],
            ["users.id"],
            name=f"fk_{_TABLE}_annotator_id_users",
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
            f"state IN {_STATE_ALLOWED_SQL}",
            name=f"ck_{_TABLE}_state_allowed",
        ),
        sa.CheckConstraint(
            "hits >= 0",
            name=f"ck_{_TABLE}_hits_non_negative",
        ),
        sa.CheckConstraint(
            "total >= 0",
            name=f"ck_{_TABLE}_total_non_negative",
        ),
        sa.CheckConstraint(
            "hits <= total",
            name=f"ck_{_TABLE}_hits_lte_total",
        ),
        sa.CheckConstraint(
            "total <= 20",
            name=f"ck_{_TABLE}_total_capacity",
        ),
        sa.CheckConstraint(
            "length(window_history) = total",
            name=f"ck_{_TABLE}_window_history_matches_total",
        ),
        sa.CheckConstraint(
            _BINARY_CHECK,
            name=f"ck_{_TABLE}_window_history_binary",
        ),
        sa.CheckConstraint(
            "paused_reason IS NULL OR length(paused_reason) > 0",
            name=f"ck_{_TABLE}_paused_reason_non_empty",
        ),
    )
    for index_name, columns in _INDEXES:
        op.create_index(index_name, _TABLE, columns)


def downgrade() -> None:
    for index_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
