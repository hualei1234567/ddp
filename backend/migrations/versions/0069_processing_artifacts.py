"""V31-016：处理运行产物目录 ``processing_artifacts``

Revision ID: 0069
Revises: 0068
Create Date: 2026-11-22

为什么新建表而不是继续堆 ``processing_runs.output_refs``
-----------------------------------------------------
``output_refs`` 是 ``list[str]`` 的 JSON 数组，只有对象 key。它能回答
"这次运行产出了哪些对象"，但回答不了：

- 这个产物是**哪个阶段**、**哪次尝试**产出的（重试后历史产物无法区分）；
- 产物**类型**是什么（元数据 / Canonical / 时间对齐报告 / 质检引用 / 阶段引用）；
- 产物**多大**、**内容摘要**是否与登记时一致（无法做完整性校验）；
- 产物的**来源输入**是哪个对象（血缘断在 key 上）。

继续往 JSON 数组里塞结构化对象，会让"数组里既有字符串又有对象"成为常态，
读取方必须同时兼容两种形状——这正是本任务要消除的漂移。因此新建一等的
产物目录表，``output_refs`` 退化为兼容投影（既有调用方与契约不变）。

表设计
------
- ``(run_id, stage, attempt, kind, object_key)`` 唯一：同一阶段同一次尝试
  对同一对象只登记一次；重试换 ``attempt``，历史产物保留可查。
- ``project_id`` 列 + ``(project_id, run_id)`` 复合外键强制跨项目隔离：
  绕过应用层直接插行也写不出跨项目引用。
- ``size_bytes >= 0``、``checksum_sha256`` 为 NULL 或 64 位十六进制：
  摘要的全部价值在于可逐字节复算，短串或大小写不符即视为脏数据。
- ``ON DELETE CASCADE``：运行是产物的生命周期边界，删除运行即删除其产物
  记录（对象存储中的对象由保留策略处理，不在本表职责内）。

SQLite 与 PostgreSQL 的差异
---------------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，但本迁移是**新建表**，
两库都走 ``op.create_table``，无需 batch 重建；``JSON`` 在 SQLite 上退化为
``TEXT``，本表不含 JSON 列。

回滚
----
``downgrade`` 先删索引再删表。产物记录是运行产物的元数据，回滚会丢失
升级后登记的全部产物；``processing_runs.output_refs`` 不受影响，既有读取
路径仍可用。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

_TABLE = "processing_artifacts"
# 迁移是历史快照：取值冻结在 0069 时点，禁止引用运行时 Enum。
_STAGE_VALUES = (
    "'FILE_VALIDATION', 'METADATA_EXTRACTION', 'FORMAT_PARSING', "
    "'TIME_ALIGNMENT', 'CALIBRATION_VALIDATION', 'EPISODE_SEGMENTATION', "
    "'DATA_CLEANING', 'QUALITY_PRE_CHECK'"
)
_KIND_VALUES = "'METADATA', 'CANONICAL', 'TIME_ALIGNMENT_REPORT'"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("processing_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("source_ref", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "attempt",
            "kind",
            "object_key",
            name="uq_processing_artifacts_run_stage_attempt_kind_key",
        ),
        sa.CheckConstraint(f"stage IN ({_STAGE_VALUES})", name="stage_allowed"),
        sa.CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_allowed"),
        sa.CheckConstraint("attempt >= 1", name="attempt_positive"),
        sa.CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR length(checksum_sha256) = 64",
            name="checksum_sha256_length",
        ),
    )
    op.create_index(
        "ix_processing_artifacts_project_run_created_at",
        _TABLE,
        ["project_id", "run_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processing_artifacts_project_run_created_at",
        table_name=_TABLE,
    )
    op.drop_table(_TABLE)
