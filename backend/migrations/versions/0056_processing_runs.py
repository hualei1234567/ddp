"""N4-001：建立处理运行 processing_runs 表

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-18

为什么需要这张表
----------------
N4 的"可观察、可重试、可追踪"此前没有独立承载点：``ingestion_jobs`` 只覆盖
接入任务（校验/解析/切片），语义与状态机都不同，不能复用为处理流水线运行。
本迁移只建 ``processing_runs`` 一张表，不修改任何既有表。

字段口径
--------
- ``pipeline_version``：流水线版本，必填；
- ``input_refs`` / ``output_refs``：对象存储引用或稳定标识的 JSON 数组，
  **不存大对象本体**；
- ``status``：七态状态机（CREATED/QUEUED/RUNNING/SUCCEEDED/FAILED/
  CANCELLED/RETRYING），CHECK 兜底；
- ``stage``：八个执行阶段之一；未开始时为 NULL；
- ``attempt`` / ``retry_count``：执行尝试次数与重试次数，均非负且
  ``retry_count <= attempt``；
- ``error_code`` / ``error_summary``：只有 FAILED 允许非空；
- ``version``：乐观锁版本，``If-Match`` 使用。

索引
----
``(project_id, status, created_at)`` 对应"按项目 + 状态看最近运行"的主查询；
``project_id`` 前缀同时保证项目隔离。

回滚
----
``downgrade`` 只删除本迁移新建的表与索引，不触碰既有数据。该表在升级前
不存在，回滚不丢失升级前的任何数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_STATUS_VALUES = "'CREATED', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'RETRYING'"
_STAGE_VALUES = (
    "'FILE_VALIDATION', 'METADATA_EXTRACTION', 'FORMAT_PARSING', 'TIME_ALIGNMENT', "
    "'CALIBRATION_VALIDATION', 'EPISODE_SEGMENTATION', 'DATA_CLEANING', "
    "'QUALITY_PRE_CHECK'"
)


def upgrade() -> None:
    op.create_table(
        "processing_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("input_refs", _JSON, nullable=False),
        sa.Column("output_refs", _JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'CREATED'"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_processing_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_processing_runs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_processing_runs_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_processing_runs_updated_by_users",
        ),
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_allowed"),
        sa.CheckConstraint(
            f"stage IS NULL OR stage IN ({_STAGE_VALUES})",
            name="stage_allowed",
        ),
        sa.CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        sa.CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        sa.CheckConstraint("retry_count <= attempt", name="retry_within_attempt"),
        sa.CheckConstraint(
            "status NOT IN ('RUNNING', 'SUCCEEDED', 'FAILED') OR stage IS NOT NULL",
            name="active_requires_stage",
        ),
        sa.CheckConstraint(
            "status NOT IN ('RUNNING', 'SUCCEEDED', 'FAILED') OR started_at IS NOT NULL",
            name="active_requires_started_at",
        ),
        sa.CheckConstraint(
            "status NOT IN ('RUNNING', 'SUCCEEDED', 'FAILED') OR attempt >= 1",
            name="active_requires_attempt",
        ),
        sa.CheckConstraint(
            "status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED') OR finished_at IS NOT NULL",
            name="finished_requires_finished_at",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'CANCELLED') OR finished_at IS NULL",
            name="unfinished_without_finished_at",
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR (error_code IS NOT NULL AND error_summary IS NOT NULL)",
            name="failed_requires_error",
        ),
        sa.CheckConstraint(
            "status = 'FAILED' OR (error_code IS NULL AND error_summary IS NULL)",
            name="error_only_when_failed",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index(
        "ix_processing_runs_project_status_created_at",
        "processing_runs",
        ["project_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processing_runs_project_status_created_at",
        table_name="processing_runs",
    )
    op.drop_table("processing_runs")
