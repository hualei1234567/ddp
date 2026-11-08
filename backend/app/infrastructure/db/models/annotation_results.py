"""阶段 3 提交、记录、复核与仲裁 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.annotation.enums import (
    AnnotationArbitrationStatus,
    AnnotationRecordStatus,
    AnnotationReviewStatus,
    AnnotationSubmissionStatus,
    ArbitrationDecision,
    ArbitrationTrigger,
    DataSource,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class AnnotationSubmission(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_submissions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    annotator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    submission_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    records_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)
    record_refs: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    review_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    arbitration_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_submissions_project_id_id"),
        UniqueConstraint(
            "project_id",
            "assignment_id",
            "submission_version",
            name="uq_annotation_submissions_assignment_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_submissions_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id", "batch_id"],
            [
                "annotation_batches.project_id",
                "annotation_batches.task_id",
                "annotation_batches.id",
            ],
            name="fk_annotation_submissions_project_task_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "assignment_id"],
            ["annotation_assignments.project_id", "annotation_assignments.id"],
            name="fk_annotation_submissions_project_assignment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_submissions_project_annotator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_submissions_project_schema",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationSubmissionStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            f"data_source IN ({_values(DataSource)})",
            name="data_source_allowed",
        ),
        CheckConstraint(_SHA_CHECK.format(column="schema_sha256"), name="schema_sha256_format"),
        CheckConstraint(
            _SHA_CHECK.format(column="records_digest"),
            name="records_digest_format",
        ),
        CheckConstraint("submission_version >= 1", name="submission_version_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("item_count >= 0", name="item_count_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_submissions_project_task_status",
            "project_id",
            "task_id",
            "status",
        ),
        Index(
            "ix_annotation_submissions_project_assignment",
            "project_id",
            "assignment_id",
        ),
    )


class AnnotationRecord(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_records"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_version: Mapped[int] = mapped_column(Integer, nullable=False)
    annotation_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    annotation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    time_range: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    labels: Mapped[list[dict[str, object]]] = mapped_column(_JSON_TYPE, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    record_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_records_project_id_id"),
        UniqueConstraint(
            "project_id",
            "annotation_lineage_id",
            "annotation_version",
            name="uq_annotation_records_project_lineage_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_records_project_submission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_records_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_annotation_records_project_episode",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_records_project_schema",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["annotation_records.project_id", "annotation_records.id"],
            name="fk_annotation_records_project_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationRecordStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(_SHA_CHECK.format(column="record_sha256"), name="record_sha256_format"),
        CheckConstraint("episode_version >= 1", name="episode_version_positive"),
        CheckConstraint("annotation_version >= 1", name="annotation_version_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_records_project_submission",
            "project_id",
            "submission_id",
        ),
        Index(
            "ix_annotation_records_project_task_status",
            "project_id",
            "task_id",
            "status",
        ),
        Index(
            "ix_annotation_records_project_episode",
            "project_id",
            "episode_id",
        ),
    )


class AnnotationReview(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_reviews"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    review_round: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    issues: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    checklist: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    review_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_reviews_project_id_id"),
        UniqueConstraint(
            "project_id",
            "submission_id",
            "review_round",
            name="uq_annotation_reviews_submission_round",
        ),
        ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_reviews_project_submission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_reviews_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "reviewer_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_reviews_project_reviewer",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationReviewStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('APPROVE', 'REJECT', 'ESCALATE')",
            name="decision_allowed",
        ),
        CheckConstraint("review_round >= 1", name="review_round_positive"),
        CheckConstraint(
            "review_duration_sec IS NULL OR review_duration_sec >= 0",
            name="review_duration_non_negative",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_reviews_project_submission",
            "project_id",
            "submission_id",
        ),
    )


class AnnotationArbitration(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_arbitrations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    review_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    arbiter_ids: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    quorum: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    votes: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    final_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_arbitrations_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_arbitrations_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_arbitrations_project_submission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "review_id"],
            ["annotation_reviews.project_id", "annotation_reviews.id"],
            name="fk_annotation_arbitrations_project_review",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationArbitrationStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            f"trigger IN ({_values(ArbitrationTrigger)})",
            name="trigger_allowed",
        ),
        CheckConstraint(
            "final_decision IS NULL OR final_decision IN " f"({_values(ArbitrationDecision)})",
            name="final_decision_allowed",
        ),
        CheckConstraint("quorum >= 1", name="quorum_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_arbitrations_project_submission",
            "project_id",
            "submission_id",
        ),
    )
