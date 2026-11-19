"""N9-001：采集会话 ``capture_sessions``

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-19

为什么不扩展 ``collection_tasks``
--------------------------------
任务已经带有 ``actual_start`` / ``actual_end`` / ``assigned_robot_id`` 等字段，
看起来"能塞下"一次执行，但两者的生命周期语义不同：

- 任务是**计划 + 审核 + 验收**聚合：DRAFT → … → COMPLETED，包含提交、数据
  检查、驳回、返工等人工流程；
- 会话是**执行记录**：SCHEDULED → RUNNING ↔ PAUSED → COMPLETED / ABORTED，
  没有审核动作，也不参与任务的验收判定。

同一任务在真实采集里会有多次执行（换设备、换操作员、暂停后重开、失败重来）。
把会话压进任务字段，只能表达"最后一次执行"，历史执行无法保留；而把任务状态
机扩出一套执行态，会让审核流与运行时状态互相污染。详细边界见 ADR-0037。

表设计
------
- ``task_id`` / ``batch_id`` 可空：允许"先开录、后归属"，但一旦给出就必须
  同项目存在；批次给了而任务没给时，应用层自动继承批次的任务。
- ``robot_id`` / ``location_id`` 用 ``(project_id, id)`` 复合外键，禁止跨项目
  引用；``operator_id`` 引用全局 ``users.id``，成员资格由应用层校验。
- 时间与状态的一致性（SCHEDULED 无时间、执行中无结束时间、终态必须有结束
  时间）由领域实体强制；数据库只保证列类型与 ``ended_at >= started_at``。
  SQLite 的 CHECK 与触发器都不重建表，因此这里不引入需要重建的约束。
- 状态迁移由触发器兜底：初始必须 ``SCHEDULED``，迁移必须落在
  ``session_transition_pairs()`` 白名单内，且记录不可删除——与任务/批次
  （迁移 0007）的做法一致。

回滚
----
``downgrade`` 先删触发器与 PG 函数，再删索引与表。会话是执行记录，
回滚会丢失升级后写入的全部会话；本迁移不回填任何历史任务，升级前后
``collection_tasks`` 行为完全不变。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.collection_guards import (
    drop_capture_session_state_guards,
    install_capture_session_state_guards,
)

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

_TABLE = "capture_sessions"
_STATUS_VALUES = "'SCHEDULED', 'RUNNING', 'PAUSED', 'COMPLETED', 'ABORTED'"


def _audit_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("robot_id", sa.Uuid(), nullable=True),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_capture_sessions"),
        sa.UniqueConstraint("project_id", "id", name="uq_capture_sessions_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_capture_sessions_project_code"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_capture_sessions_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_capture_sessions_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_capture_sessions_updated_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["users.id"],
            name="fk_capture_sessions_operator_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_capture_sessions_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_capture_sessions_project_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_capture_sessions_project_robot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "location_id"],
            ["locations.project_id", "locations.id"],
            name="fk_capture_sessions_project_location",
            ondelete="NO ACTION",
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUS_VALUES})",
            name=op.f("ck_capture_sessions_status_allowed"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_capture_sessions_version_positive"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_capture_sessions_session_range_valid"),
        ),
    )
    op.create_index(
        "ix_capture_sessions_project_status_created_at",
        _TABLE,
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_capture_sessions_project_task_created_at",
        _TABLE,
        ["project_id", "task_id", "created_at"],
        unique=False,
    )
    install_capture_session_state_guards(op.get_bind())


def downgrade() -> None:
    drop_capture_session_state_guards(op.get_bind())
    op.drop_index("ix_capture_sessions_project_task_created_at", table_name=_TABLE)
    op.drop_index("ix_capture_sessions_project_status_created_at", table_name=_TABLE)
    op.drop_table(_TABLE)
