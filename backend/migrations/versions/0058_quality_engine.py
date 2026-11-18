"""N4-004：建立统一质量运行 quality_runs 与结论 quality_findings

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-18

为什么需要这两张表
------------------
现有三套质量规则各自产出结论：导入质量报告是即时计算、标注质量运行落在
``annotation_quality_runs``、数据集质量报告落在 ``dataset_quality_reports``。
它们回答不了「同一个 Episode 的完整性/时序/视觉/元数据/标注分别怎样」，
也无法用同一套口径比较历史。0058 提供统一的运行与结论存储，规则实现仍留在
原处，本迁移只落"编排结果"。

口径
----
- 运行与结论都**只追加**：改结论 = 重跑新 run；
- ``rule_set_version`` 是适用规则声明的内容 SHA（``sha256:<64 hex>``），
  运行创建时固化，后续规则升级不回写；
- ``category`` 六类闭集，``OVERALL`` 是派生类别，由应用层聚合，不落 finding；
- ``status``：PASS / FAIL / NOT_APPLICABLE / ERROR；后两者 ``score`` 必须为
  NULL——"没判"不能参与平均，更不能被当成通过；
- ``subject_type`` / ``subject_id`` 是多态引用（Episode / 导入任务 / 数据集
  版本），不建单表外键；项目隔离由 ``project_id`` 与仓储层双重强制；
- findings 到 runs 的复合外键带 ``project_id``，跨项目拼 ID 在数据库层失败。

索引
----
- ``(project_id, subject_type, subject_id, created_at)``：按对象查历史运行；
- ``(project_id, run_id, category/status)``：结论明细按类别/状态筛选。

回滚
----
``downgrade`` 只删除本迁移新建的表与索引；0058 之前两表不存在，回滚不丢失
升级前的数据。既有三套质量表不受影响。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

_SUBJECT_TYPES = "'EPISODE', 'DATASOURCE_IMPORT', 'DATASET_VERSION'"
_RUN_STATUSES = "'PASSED', 'FAILED', 'INCOMPLETE'"
_CATEGORIES = "'INTEGRITY', 'TEMPORAL', 'VISUAL', 'METADATA', 'ANNOTATION', 'OVERALL'"
_SEVERITIES = "'ERROR', 'WARN'"
_FINDING_STATUSES = "'PASS', 'FAIL', 'NOT_APPLICABLE', 'ERROR'"
# 与 ORM 模型一致：SQLite 用 JSON，PostgreSQL 用 JSONB。
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "quality_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("rule_set_version", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("warn_count", sa.Integer(), nullable=False),
        sa.Column("category_summary", _JSON, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_quality_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_quality_runs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_quality_runs_created_by_users",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_quality_runs_project_id_id"),
        sa.CheckConstraint(
            f"subject_type IN ({_SUBJECT_TYPES})",
            name="subject_type_allowed",
        ),
        sa.CheckConstraint(f"status IN ({_RUN_STATUSES})", name="status_allowed"),
        sa.CheckConstraint(
            "length(rule_set_version) = 71 AND substr(rule_set_version, 1, 7) = 'sha256:'",
            name="rule_set_version_format",
        ),
        sa.CheckConstraint("finding_count >= 0", name="finding_count_non_negative"),
        sa.CheckConstraint("error_count >= 0", name="error_count_non_negative"),
        sa.CheckConstraint("warn_count >= 0", name="warn_count_non_negative"),
        sa.CheckConstraint(
            "error_count + warn_count <= finding_count",
            name="fail_counts_within_findings",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="score_range",
        ),
        sa.CheckConstraint("finished_at >= started_at", name="finished_after_started"),
    )
    op.create_index(
        "ix_quality_runs_project_subject_created",
        "quality_runs",
        ["project_id", "subject_type", "subject_id", "created_at"],
    )
    op.create_index(
        "ix_quality_runs_project_created",
        "quality_runs",
        ["project_id", "created_at"],
    )

    op.create_table(
        "quality_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "subject_ref",
            sa.String(length=128),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("evidence", _JSON, nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quality_findings"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_quality_findings_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["quality_runs.project_id", "quality_runs.id"],
            name="fk_quality_findings_project_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_quality_findings_project_id_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "run_id",
            "subject_ref",
            "category",
            "rule_id",
            name="uq_quality_findings_run_subject_rule",
        ),
        sa.CheckConstraint(f"category IN ({_CATEGORIES})", name="category_allowed"),
        sa.CheckConstraint("category <> 'OVERALL'", name="category_not_overall"),
        sa.CheckConstraint(f"severity IN ({_SEVERITIES})", name="severity_allowed"),
        sa.CheckConstraint(f"status IN ({_FINDING_STATUSES})", name="status_allowed"),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="score_range",
        ),
        sa.CheckConstraint(
            "(status IN ('NOT_APPLICABLE', 'ERROR') AND score IS NULL) OR "
            "(status IN ('PASS', 'FAIL') AND score IS NOT NULL)",
            name="score_matches_status",
        ),
    )
    op.create_index(
        "ix_quality_findings_project_run_category",
        "quality_findings",
        ["project_id", "run_id", "category"],
    )
    op.create_index(
        "ix_quality_findings_project_run_status",
        "quality_findings",
        ["project_id", "run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quality_findings_project_run_status",
        table_name="quality_findings",
    )
    op.drop_index(
        "ix_quality_findings_project_run_category",
        table_name="quality_findings",
    )
    op.drop_table("quality_findings")
    op.drop_index("ix_quality_runs_project_created", table_name="quality_runs")
    op.drop_index(
        "ix_quality_runs_project_subject_created",
        table_name="quality_runs",
    )
    op.drop_table("quality_runs")
