"""stage3 annotation submissions, records, reviews and arbitrations

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-09

阶段 3 第二批（上）：提交/记录/复核/仲裁。
- 提交按 assignment 版本化，记录带稳定 Hash；
- 提交离开 DRAFT 后内容不可变，复核/仲裁终态不可变；
- 复核人不得是标注人；所有父引用使用 ``(project_id, id)`` 复合外键；
- 状态迁移、不可变与禁删由 SQLite/PostgreSQL 触发器兜底。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.annotation_result_guards import (
    drop_result_guards,
    install_result_guards,
)

revision = "0014"
down_revision = "0013"
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
        "annotation_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("annotator_id", sa.Uuid(), nullable=False),
        sa.Column("submission_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("schema_sha256", sa.String(length=71), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("records_digest", sa.String(length=71), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column("record_refs", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("arbitration_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_submissions"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_submissions_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "assignment_id",
            "submission_version",
            name="uq_annotation_submissions_assignment_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_submissions_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id", "batch_id"],
            [
                "annotation_batches.project_id",
                "annotation_batches.task_id",
                "annotation_batches.id",
            ],
            name="fk_annotation_submissions_project_task_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "assignment_id"],
            ["annotation_assignments.project_id", "annotation_assignments.id"],
            name="fk_annotation_submissions_project_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_submissions_project_annotator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_submissions_project_schema",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'REJECTED', "
            "'ESCALATED', 'ARBITRATING', 'RESOLVED_APPROVED', 'RESOLVED_REJECTED', "
            "'RESOLVED_REWORK', 'REWORK_REQUIRED', 'WITHDRAWN', 'SUPERSEDED')",
            name=op.f("ck_annotation_submissions_status_allowed"),
        ),
        sa.CheckConstraint(
            "data_source IN ('REAL', 'SYNTHETIC', 'MIXED', 'UNKNOWN')",
            name=op.f("ck_annotation_submissions_data_source_allowed"),
        ),
        sa.CheckConstraint(
            _SHA_CHECK.format(column="schema_sha256"),
            name=op.f("ck_annotation_submissions_schema_sha256_format"),
        ),
        sa.CheckConstraint(
            _SHA_CHECK.format(column="records_digest"),
            name=op.f("ck_annotation_submissions_records_digest_format"),
        ),
        sa.CheckConstraint(
            "submission_version >= 1",
            name=op.f("ck_annotation_submissions_submission_version_positive"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_annotation_submissions_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "item_count >= 0",
            name=op.f("ck_annotation_submissions_item_count_non_negative"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_submissions_version_positive"),
        ),
        *_audit_constraints("annotation_submissions"),
    )
    op.create_index(
        "ix_annotation_submissions_project_task_status",
        "annotation_submissions",
        ["project_id", "task_id", "status"],
    )
    op.create_index(
        "ix_annotation_submissions_project_assignment",
        "annotation_submissions",
        ["project_id", "assignment_id"],
    )

    op.create_table(
        "annotation_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("episode_version", sa.Integer(), nullable=False),
        sa.Column("annotation_lineage_id", sa.Uuid(), nullable=False),
        sa.Column("annotation_version", sa.Integer(), nullable=False),
        sa.Column("schema_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("time_range", sa.JSON(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("record_sha256", sa.String(length=71), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_records"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_records_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "annotation_lineage_id",
            "annotation_version",
            name="uq_annotation_records_project_lineage_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_records_project_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_records_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_annotation_records_project_episode",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_records_project_schema",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["annotation_records.project_id", "annotation_records.id"],
            name="fk_annotation_records_project_supersedes",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', "
            "'SUPERSEDED', 'INVALIDATED')",
            name=op.f("ck_annotation_records_status_allowed"),
        ),
        sa.CheckConstraint(
            _SHA_CHECK.format(column="record_sha256"),
            name=op.f("ck_annotation_records_record_sha256_format"),
        ),
        sa.CheckConstraint(
            "episode_version >= 1",
            name=op.f("ck_annotation_records_episode_version_positive"),
        ),
        sa.CheckConstraint(
            "annotation_version >= 1",
            name=op.f("ck_annotation_records_annotation_version_positive"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_annotation_records_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_records_version_positive"),
        ),
        *_audit_constraints("annotation_records"),
    )
    op.create_index(
        "ix_annotation_records_project_submission",
        "annotation_records",
        ["project_id", "submission_id"],
    )
    op.create_index(
        "ix_annotation_records_project_task_status",
        "annotation_records",
        ["project_id", "task_id", "status"],
    )
    op.create_index(
        "ix_annotation_records_project_episode",
        "annotation_records",
        ["project_id", "episode_id"],
    )

    op.create_table(
        "annotation_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("review_round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("issues", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("checklist", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("review_duration_sec", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_reviews"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_reviews_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "submission_id",
            "review_round",
            name="uq_annotation_reviews_submission_round",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_reviews_project_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_reviews_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "reviewer_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_reviews_project_reviewer",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED', "
            "'ESCALATED', 'CANCELLED')",
            name=op.f("ck_annotation_reviews_status_allowed"),
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('APPROVE', 'REJECT', 'ESCALATE')",
            name=op.f("ck_annotation_reviews_decision_allowed"),
        ),
        sa.CheckConstraint(
            "review_round >= 1",
            name=op.f("ck_annotation_reviews_review_round_positive"),
        ),
        sa.CheckConstraint(
            "review_duration_sec IS NULL OR review_duration_sec >= 0",
            name=op.f("ck_annotation_reviews_review_duration_non_negative"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_reviews_version_positive"),
        ),
        *_audit_constraints("annotation_reviews"),
    )
    op.create_index(
        "ix_annotation_reviews_project_submission",
        "annotation_reviews",
        ["project_id", "submission_id"],
    )

    op.create_table(
        "annotation_arbitrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("arbiter_ids", sa.JSON(), nullable=False),
        sa.Column("quorum", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("votes", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("final_decision", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_arbitrations"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_arbitrations_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_arbitrations_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_arbitrations_project_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "review_id"],
            ["annotation_reviews.project_id", "annotation_reviews.id"],
            name="fk_annotation_arbitrations_project_review",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_ARBITRATION', 'RESOLVED_APPROVED', "
            "'RESOLVED_REJECTED', 'RESOLVED_REWORK', 'CANCELLED')",
            name=op.f("ck_annotation_arbitrations_status_allowed"),
        ),
        sa.CheckConstraint(
            "trigger IN ('REVIEWER_DISAGREEMENT', 'ANNOTATOR_DISPUTE', "
            "'GOLD_SET_CONFLICT', 'IAA_CONFLICT', 'HIGH_RISK', 'POLICY')",
            name=op.f("ck_annotation_arbitrations_trigger_allowed"),
        ),
        sa.CheckConstraint(
            "final_decision IS NULL OR final_decision IN "
            "('APPROVE', 'REJECT', 'REWORK', 'ABSTAIN')",
            name=op.f("ck_annotation_arbitrations_final_decision_allowed"),
        ),
        sa.CheckConstraint(
            "quorum >= 1",
            name=op.f("ck_annotation_arbitrations_quorum_positive"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_arbitrations_version_positive"),
        ),
        *_audit_constraints("annotation_arbitrations"),
    )
    op.create_index(
        "ix_annotation_arbitrations_project_submission",
        "annotation_arbitrations",
        ["project_id", "submission_id"],
    )

    install_result_guards(op.get_bind())


def downgrade() -> None:
    drop_result_guards(op.get_bind())
    op.drop_index(
        "ix_annotation_arbitrations_project_submission",
        table_name="annotation_arbitrations",
    )
    op.drop_table("annotation_arbitrations")
    op.drop_index(
        "ix_annotation_reviews_project_submission",
        table_name="annotation_reviews",
    )
    op.drop_table("annotation_reviews")
    op.drop_index(
        "ix_annotation_records_project_episode",
        table_name="annotation_records",
    )
    op.drop_index(
        "ix_annotation_records_project_task_status",
        table_name="annotation_records",
    )
    op.drop_index(
        "ix_annotation_records_project_submission",
        table_name="annotation_records",
    )
    op.drop_table("annotation_records")
    op.drop_index(
        "ix_annotation_submissions_project_assignment",
        table_name="annotation_submissions",
    )
    op.drop_index(
        "ix_annotation_submissions_project_task_status",
        table_name="annotation_submissions",
    )
    op.drop_table("annotation_submissions")
