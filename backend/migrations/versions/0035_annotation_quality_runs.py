"""annotation quality runs and findings

Revision ID: 0035
Revises: 0032
Create Date: 2026-11-16

N4：质量规则可配置且可版本化。

背景：``LabelDefinition.quality_rules`` 此前只是随 Schema JSON 存下的字符串
元数据，**没有任何代码读取执行**，却出现在 API 输出里，造成"有质量控制"的
误读。本迁移为可执行质量规则建立执行记录：

- ``annotation_quality_runs``：一次检查一条记录，``rule_set_version`` 是规则集
  内容摘要（``sha256:`` 前缀），可回答"当时用的是哪一版规则"；
- ``annotation_quality_findings``：每条记录 × 每条规则一行，
  ``passed IS NULL`` 表示该规则对该记录**没有结论**（缺 Episode 边界、缺策略
  阈值），与"判定通过"严格区分。

两张表都只追加：结论不可变，重跑即新增 run。

写法说明
--------
全部使用显式 ``op.create_table`` + ``op.create_index``，**不使用**
``Model.__table__.create()``。本仓库刚因此在全新库上升级失败：
``Base.metadata.create_all`` 里 ``after_create`` 事件会安装触发器，而
``__table__.create()`` 绕过 metadata 级事件，导致"迁移过的库"与"create_all
建出的库"结构不一致。显式 DDL 是唯一可审查、可回滚、跨 SQLite/PostgreSQL
一致的路径。

回滚
----
``downgrade`` 严格逆序删除两张表（先删子表再删父表，且先删索引）。
两表均为新建、无历史数据依赖，回滚不丢失任何既有业务数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_RUNS = "annotation_quality_runs"
_FINDINGS = "annotation_quality_findings"
_STATUS_VALUES = "'PASSED', 'FAILED'"
_SEVERITY_VALUES = "'ERROR', 'WARN'"
_RULE_SET_VERSION_CHECK = (
    "length(rule_set_version) = 71 AND substr(rule_set_version, 1, 7) = 'sha256:'"
)


def upgrade() -> None:
    op.create_table(
        _RUNS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("rule_set_version", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("warn_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_quality_runs"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_annotation_quality_runs_project_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotation_quality_runs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_annotation_quality_runs_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_quality_runs_project_submission",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUS_VALUES})",
            name=op.f("ck_annotation_quality_runs_status_allowed"),
        ),
        sa.CheckConstraint(
            "error_count >= 0",
            name=op.f("ck_annotation_quality_runs_error_count_non_negative"),
        ),
        sa.CheckConstraint(
            "warn_count >= 0",
            name=op.f("ck_annotation_quality_runs_warn_count_non_negative"),
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name=op.f("ck_annotation_quality_runs_finished_after_started"),
        ),
        sa.CheckConstraint(
            _RULE_SET_VERSION_CHECK,
            name=op.f("ck_annotation_quality_runs_rule_set_version_format"),
        ),
    )
    op.create_index(
        "ix_annotation_quality_runs_project_submission_created",
        _RUNS,
        ["project_id", "submission_id", "created_at"],
    )

    op.create_table(
        _FINDINGS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("annotation_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_quality_findings"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_annotation_quality_findings_project_id_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "run_id",
            "annotation_id",
            "rule_id",
            name="uq_annotation_quality_findings_run_record_rule",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotation_quality_findings_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            [_RUNS + ".project_id", _RUNS + ".id"],
            name="fk_annotation_quality_findings_project_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "annotation_id"],
            ["annotation_records.project_id", "annotation_records.id"],
            name="fk_annotation_quality_findings_project_annotation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"severity IN ({_SEVERITY_VALUES})",
            name=op.f("ck_annotation_quality_findings_severity_allowed"),
        ),
        sa.CheckConstraint(
            "rule_version >= 1",
            name=op.f("ck_annotation_quality_findings_rule_version_positive"),
        ),
    )
    op.create_index(
        "ix_annotation_quality_findings_project_run",
        _FINDINGS,
        ["project_id", "run_id"],
    )
    op.create_index(
        "ix_annotation_quality_findings_project_annotation",
        _FINDINGS,
        ["project_id", "annotation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_annotation_quality_findings_project_annotation",
        table_name=_FINDINGS,
    )
    op.drop_index(
        "ix_annotation_quality_findings_project_run",
        table_name=_FINDINGS,
    )
    op.drop_table(_FINDINGS)
    op.drop_index(
        "ix_annotation_quality_runs_project_submission_created",
        table_name=_RUNS,
    )
    op.drop_table(_RUNS)
