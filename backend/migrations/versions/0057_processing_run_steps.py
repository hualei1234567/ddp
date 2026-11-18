"""N4-002：建立处理运行阶段步骤 processing_run_steps

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-18

为什么需要这张表
----------------
0056 的 ``processing_runs`` 只有运行级状态与"当前阶段"，回答不了
「八个阶段里哪个跑过、哪个跳过、为什么失败、重试了几次」。N4-002 的编排器
每推进一个阶段就写一行步骤记录，让"可观察、可重试、可追踪"落到数据上，
而不是只存在于日志里。

口径
----
- ``(run_id, stage, attempt)`` 唯一：同一运行、同一阶段、同一尝试只有一行；
  重试会换 ``attempt``，历史失败原因不被覆盖；
- ``status``：RUNNING / SUCCEEDED / FAILED / SKIPPED；
- ``SKIPPED`` 必须有非空 ``log_summary``（跳过原因），``FAILED`` 必须同时有
  ``error_code`` / ``error_summary``，其余状态不得携带错误——与运行实体同口径；
- ``project_id`` 冗余存储并加索引：项目隔离在仓储层直接下推，不必每次 join；
- ``started_at`` / ``finished_at`` 为带时区时间，``finished_at >= started_at``。

索引
----
``(project_id, run_id, id)`` 对应"按项目 + 运行列步骤、按 ID 游标分页"的主查询。

回滚
----
``downgrade`` 只删除本迁移新建的表与索引；0057 之前该表不存在，回滚不丢失
升级前的任何数据。运行表本身不受影响。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_STEP_STATUS_VALUES = "'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED'"
_STAGE_VALUES = (
    "'FILE_VALIDATION', 'METADATA_EXTRACTION', 'FORMAT_PARSING', 'TIME_ALIGNMENT', "
    "'CALIBRATION_VALIDATION', 'EPISODE_SEGMENTATION', 'DATA_CLEANING', "
    "'QUALITY_PRE_CHECK'"
)


def upgrade() -> None:
    op.create_table(
        "processing_run_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'RUNNING'"),
            nullable=False,
        ),
        sa.Column("log_summary", sa.String(length=1000), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_processing_run_steps"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_processing_run_steps_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["processing_runs.id"],
            name="fk_processing_run_steps_run_id_processing_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_processing_run_steps_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_processing_run_steps_updated_by_users",
        ),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "attempt",
            name="uq_processing_run_steps_run_stage_attempt",
        ),
        sa.CheckConstraint(f"stage IN ({_STAGE_VALUES})", name="stage_allowed"),
        sa.CheckConstraint(
            f"status IN ({_STEP_STATUS_VALUES})",
            name="status_allowed",
        ),
        sa.CheckConstraint("attempt >= 1", name="attempt_positive"),
        sa.CheckConstraint(
            "status <> 'RUNNING' OR finished_at IS NULL",
            name="running_without_finished_at",
        ),
        sa.CheckConstraint(
            "status = 'RUNNING' OR finished_at IS NOT NULL",
            name="terminal_requires_finished_at",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR (error_code IS NOT NULL AND error_summary IS NOT NULL)",
            name="failed_requires_error",
        ),
        sa.CheckConstraint(
            "status = 'FAILED' OR (error_code IS NULL AND error_summary IS NULL)",
            name="error_only_when_failed",
        ),
        sa.CheckConstraint(
            "status <> 'SKIPPED' OR (log_summary IS NOT NULL AND length(log_summary) > 0)",
            name="skipped_requires_reason",
        ),
    )
    op.create_index(
        "ix_processing_run_steps_project_run_id",
        "processing_run_steps",
        ["project_id", "run_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processing_run_steps_project_run_id",
        table_name="processing_run_steps",
    )
    op.drop_table("processing_run_steps")
