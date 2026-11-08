"""阶段 3 黄金集、曝光、评估、质检抽检与 IAA 报告 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.annotation.enums import (
    GoldRotationPolicy,
    GoldSetExposurePolicy,
    GoldSetStatus,
    IAAReportStatus,
    QualitySampleOutcome,
    QualitySampleStatus,
    QualitySampleType,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class GoldSet(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_gold_sets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    blind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    items: Mapped[list[dict[str, object]]] = mapped_column(_JSON_TYPE, nullable=False)
    exposure_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    max_exposure_per_annotator: Mapped[int] = mapped_column(Integer, nullable=False)
    rotation_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    usage_policy: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_gold_sets_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_annotation_gold_sets_project_code"),
        ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_gold_sets_project_schema",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(GoldSetStatus)})", name="status_allowed"),
        CheckConstraint("blind = TRUE", name="blind_required"),
        CheckConstraint(
            f"exposure_policy IN ({_values(GoldSetExposurePolicy)})",
            name="exposure_policy_allowed",
        ),
        CheckConstraint(
            f"rotation_policy IN ({_values(GoldRotationPolicy)})",
            name="rotation_policy_allowed",
        ),
        CheckConstraint(
            _SHA_CHECK.format(column="schema_sha256"),
            name="schema_sha256_format",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "max_exposure_per_annotator >= 1",
            name="max_exposure_positive",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_gold_sets_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
    )


class GoldExposure(Base):
    __tablename__ = "annotation_gold_set_exposures"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    gold_set_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    gold_item_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    annotator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    rotation_marker: Mapped[str] = mapped_column(String(64), nullable=False)
    exposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "gold_set_id",
            "gold_item_id",
            "annotator_id",
            "task_id",
            name="uq_annotation_gold_set_exposures_item_annotator_task",
        ),
        ForeignKeyConstraint(
            ["project_id", "gold_set_id"],
            ["annotation_gold_sets.project_id", "annotation_gold_sets.id"],
            name="fk_annotation_gold_set_exposures_project_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_gold_set_exposures_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_gold_set_exposures_project_annotator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(rotation_marker) >= 1", name="rotation_marker_non_empty"),
        Index(
            "ix_annotation_gold_set_exposures_project_annotator",
            "project_id",
            "annotator_id",
            "exposed_at",
        ),
    )


class GoldEvaluation(Base):
    __tablename__ = "annotation_gold_set_evaluations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    gold_set_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    evaluated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accuracy: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    results: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "gold_set_id"],
            ["annotation_gold_sets.project_id", "annotation_gold_sets.id"],
            name="fk_annotation_gold_set_evaluations_project_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_gold_set_evaluations_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_gold_set_evaluations_project_submission",
            ondelete="RESTRICT",
        ),
        CheckConstraint("evaluated_count >= 0", name="evaluated_count_non_negative"),
        CheckConstraint("passed_count >= 0", name="passed_count_non_negative"),
        CheckConstraint(
            "passed_count <= evaluated_count",
            name="passed_not_greater_than_evaluated",
        ),
        CheckConstraint(
            "accuracy IS NULL OR (accuracy >= 0 AND accuracy <= 1)",
            name="accuracy_range",
        ),
        Index(
            "ix_annotation_gold_set_evaluations_project_set",
            "project_id",
            "gold_set_id",
            "computed_at",
        ),
    )


class QualitySample(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_quality_samples"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    annotator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sampling_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_type: Mapped[str] = mapped_column(String(16), nullable=False)
    stratum: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sample_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    item_refs: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    review_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    arbitration_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sample_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_quality_samples_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_quality_samples_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_quality_samples_project_submission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_quality_samples_project_annotator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "reviewer_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_quality_samples_project_reviewer",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(QualitySampleStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            f"sample_type IN ({_values(QualitySampleType)})",
            name="sample_type_allowed",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN " f"({_values(QualitySampleOutcome)})",
            name="outcome_allowed",
        ),
        CheckConstraint(
            "length(sampling_policy_version) >= 1",
            name="policy_version_non_empty",
        ),
        CheckConstraint("length(sample_reason) >= 1", name="sample_reason_non_empty"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_quality_samples_project_task_status",
            "project_id",
            "task_id",
            "status",
        ),
    )


class IAAReport(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_iaa_reports"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_definitions_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    overlap_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    annotator_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_items: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    metrics: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_iaa_reports_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_iaa_reports_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_iaa_reports_project_schema",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(IAAReportStatus)})", name="status_allowed"),
        CheckConstraint(
            "length(metric_definitions_version) >= 1",
            name="metric_version_non_empty",
        ),
        CheckConstraint("sample_item_count >= 0", name="sample_item_count_non_negative"),
        CheckConstraint("overlap_item_count >= 0", name="overlap_count_non_negative"),
        CheckConstraint("annotator_count >= 0", name="annotator_count_non_negative"),
        CheckConstraint(
            "overlap_item_count <= sample_item_count",
            name="overlap_not_greater_than_sample",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_iaa_reports_project_task_status",
            "project_id",
            "task_id",
            "status",
        ),
    )
