"""stage3 annotation gold sets, quality samples and IAA reports

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09

阶段 3 第二批（下）：黄金集、曝光/评估、质检抽检与 IAA 报告。
- 黄金集答案只在授权视图中返回，曝光与评估只追加；
- 抽检策略版本化，结论闭环；
- IAA 报告由 Worker 状态机推进，终态不可回退。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.annotation_quality_guards import (
    drop_quality_guards,
    install_quality_guards,
)

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


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


def _audit_constraints(table: str) -> list[sa.schema.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{table}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{table}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{table}_updated_by_users",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "annotation_gold_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("schema_sha256", sa.String(length=71), nullable=False),
        sa.Column("blind", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("exposure_policy", sa.String(length=16), nullable=False),
        sa.Column("max_exposure_per_annotator", sa.Integer(), nullable=False),
        sa.Column("rotation_policy", sa.String(length=16), nullable=False),
        sa.Column("usage_policy", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_gold_sets"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_gold_sets_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_annotation_gold_sets_project_code"),
        sa.ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_gold_sets_project_schema",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PENDING_REVIEW', 'ACTIVE', 'RETIRED', 'REVOKED')",
            name=op.f("ck_annotation_gold_sets_status_allowed"),
        ),
        sa.CheckConstraint("blind = TRUE", name=op.f("ck_annotation_gold_sets_blind_required")),
        sa.CheckConstraint(
            "exposure_policy IN ('HIDDEN', 'ROTATING', 'CONTROLLED')",
            name=op.f("ck_annotation_gold_sets_exposure_policy_allowed"),
        ),
        sa.CheckConstraint(
            "rotation_policy IN ('NONE', 'PER_TASK', 'PER_PERIOD', 'PER_ANNOTATOR')",
            name=op.f("ck_annotation_gold_sets_rotation_policy_allowed"),
        ),
        sa.CheckConstraint(
            _SHA_CHECK.format(column="schema_sha256"),
            name=op.f("ck_annotation_gold_sets_schema_sha256_format"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_annotation_gold_sets_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "max_exposure_per_annotator >= 1",
            name=op.f("ck_annotation_gold_sets_max_exposure_positive"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_gold_sets_version_positive"),
        ),
        *_audit_constraints("annotation_gold_sets"),
    )
    op.create_index(
        "ix_annotation_gold_sets_project_status_created_at",
        "annotation_gold_sets",
        ["project_id", "status", "created_at"],
    )

    op.create_table(
        "annotation_gold_set_exposures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gold_set_id", sa.Uuid(), nullable=False),
        sa.Column("gold_item_id", sa.Uuid(), nullable=False),
        sa.Column("annotator_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("rotation_marker", sa.String(length=64), nullable=False),
        sa.Column(
            "exposed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_gold_set_exposures"),
        sa.UniqueConstraint(
            "project_id",
            "gold_set_id",
            "gold_item_id",
            "annotator_id",
            "task_id",
            name="uq_annotation_gold_set_exposures_item_annotator_task",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotation_gold_set_exposures_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "gold_set_id"],
            ["annotation_gold_sets.project_id", "annotation_gold_sets.id"],
            name="fk_annotation_gold_set_exposures_project_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_gold_set_exposures_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_gold_set_exposures_project_annotator",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(rotation_marker) >= 1",
            name=op.f("ck_annotation_gold_set_exposures_rotation_marker_non_empty"),
        ),
    )
    op.create_index(
        "ix_annotation_gold_set_exposures_project_annotator",
        "annotation_gold_set_exposures",
        ["project_id", "annotator_id", "exposed_at"],
    )

    op.create_table(
        "annotation_gold_set_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gold_set_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("evaluated_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("accuracy", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("results", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_gold_set_evaluations"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotation_gold_set_evaluations_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "gold_set_id"],
            ["annotation_gold_sets.project_id", "annotation_gold_sets.id"],
            name="fk_annotation_gold_set_evaluations_project_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_gold_set_evaluations_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_gold_set_evaluations_project_submission",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "evaluated_count >= 0",
            name=op.f("ck_annotation_gold_set_evaluations_evaluated_count_non_negative"),
        ),
        sa.CheckConstraint(
            "passed_count >= 0",
            name=op.f("ck_annotation_gold_set_evaluations_passed_count_non_negative"),
        ),
        sa.CheckConstraint(
            "passed_count <= evaluated_count",
            name=op.f("ck_annotation_gold_set_evaluations_passed_not_greater"),
        ),
        sa.CheckConstraint(
            "accuracy IS NULL OR (accuracy >= 0 AND accuracy <= 1)",
            name=op.f("ck_annotation_gold_set_evaluations_accuracy_range"),
        ),
    )
    op.create_index(
        "ix_annotation_gold_set_evaluations_project_set",
        "annotation_gold_set_evaluations",
        ["project_id", "gold_set_id", "computed_at"],
    )

    op.create_table(
        "annotation_quality_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("annotator_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sampling_policy_version", sa.String(length=64), nullable=False),
        sa.Column("sample_type", sa.String(length=16), nullable=False),
        sa.Column("stratum", sa.String(length=100), nullable=True),
        sa.Column("sample_reason", sa.String(length=500), nullable=False),
        sa.Column("item_refs", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("arbitration_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_quality_samples"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_annotation_quality_samples_project_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_quality_samples_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_quality_samples_project_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_quality_samples_project_annotator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "reviewer_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_quality_samples_project_reviewer",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'SAMPLED', 'ASSIGNED', 'IN_REVIEW', 'PASSED', "
            "'FAILED', 'ESCALATED', 'CLOSED', 'CANCELLED')",
            name=op.f("ck_annotation_quality_samples_status_allowed"),
        ),
        sa.CheckConstraint(
            "sample_type IN ('RANDOM', 'STRATIFIED', 'RISK_BASED', 'GOLD_SET', " "'IAA_OVERLAP')",
            name=op.f("ck_annotation_quality_samples_sample_type_allowed"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('PASS', 'FAIL', 'ESCALATE')",
            name=op.f("ck_annotation_quality_samples_outcome_allowed"),
        ),
        sa.CheckConstraint(
            "length(sampling_policy_version) >= 1",
            name=op.f("ck_annotation_quality_samples_policy_version_non_empty"),
        ),
        sa.CheckConstraint(
            "length(sample_reason) >= 1",
            name=op.f("ck_annotation_quality_samples_sample_reason_non_empty"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_quality_samples_version_positive"),
        ),
        *_audit_constraints("annotation_quality_samples"),
    )
    op.create_index(
        "ix_annotation_quality_samples_project_task_status",
        "annotation_quality_samples",
        ["project_id", "task_id", "status"],
    )

    op.create_table(
        "annotation_iaa_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("schema_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metric_definitions_version", sa.String(length=64), nullable=False),
        sa.Column("sample_item_count", sa.Integer(), nullable=False),
        sa.Column("overlap_item_count", sa.Integer(), nullable=False),
        sa.Column("annotator_count", sa.Integer(), nullable=False),
        sa.Column("excluded_items", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("metrics", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_iaa_reports"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_iaa_reports_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_iaa_reports_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_iaa_reports_project_schema",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', "
            "'INSUFFICIENT_DATA', 'CANCELLED')",
            name=op.f("ck_annotation_iaa_reports_status_allowed"),
        ),
        sa.CheckConstraint(
            "length(metric_definitions_version) >= 1",
            name=op.f("ck_annotation_iaa_reports_metric_version_non_empty"),
        ),
        sa.CheckConstraint(
            "sample_item_count >= 0",
            name=op.f("ck_annotation_iaa_reports_sample_item_count_non_negative"),
        ),
        sa.CheckConstraint(
            "overlap_item_count >= 0",
            name=op.f("ck_annotation_iaa_reports_overlap_count_non_negative"),
        ),
        sa.CheckConstraint(
            "annotator_count >= 0",
            name=op.f("ck_annotation_iaa_reports_annotator_count_non_negative"),
        ),
        sa.CheckConstraint(
            "overlap_item_count <= sample_item_count",
            name=op.f("ck_annotation_iaa_reports_overlap_not_greater"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_annotation_iaa_reports_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_iaa_reports_version_positive"),
        ),
        *_audit_constraints("annotation_iaa_reports"),
    )
    op.create_index(
        "ix_annotation_iaa_reports_project_task_status",
        "annotation_iaa_reports",
        ["project_id", "task_id", "status"],
    )

    install_quality_guards(op.get_bind())


def downgrade() -> None:
    drop_quality_guards(op.get_bind())
    op.drop_index(
        "ix_annotation_iaa_reports_project_task_status",
        table_name="annotation_iaa_reports",
    )
    op.drop_table("annotation_iaa_reports")
    op.drop_index(
        "ix_annotation_quality_samples_project_task_status",
        table_name="annotation_quality_samples",
    )
    op.drop_table("annotation_quality_samples")
    op.drop_index(
        "ix_annotation_gold_set_evaluations_project_set",
        table_name="annotation_gold_set_evaluations",
    )
    op.drop_table("annotation_gold_set_evaluations")
    op.drop_index(
        "ix_annotation_gold_set_exposures_project_annotator",
        table_name="annotation_gold_set_exposures",
    )
    op.drop_table("annotation_gold_set_exposures")
    op.drop_index(
        "ix_annotation_gold_sets_project_status_created_at",
        table_name="annotation_gold_sets",
    )
    op.drop_table("annotation_gold_sets")
