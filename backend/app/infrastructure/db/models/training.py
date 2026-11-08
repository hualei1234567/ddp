"""阶段 4 第三批 ORM：TrainingRequest/Revision、Delivery、外部引用、Grant 与回调防重放。"""

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
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.datasets.training_enums import (
    DatasetGrantStatus,
    ExternalTrainingStatus,
    GrantScope,
    RevisionStatus,
    TrainingDeliveryStatus,
    TrainingPriority,
    TrainingRequestStatus,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


def _sha_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(_SHA.format(column=column), name=name)


class TrainingRequestModel(Base):
    __tablename__ = "training_requests"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_revision_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    current_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    blocked_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    blocked_from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_training_requests_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_training_requests_project_code"),
        CheckConstraint(f"status IN ({_values(TrainingRequestStatus)})", name="status_allowed"),
        CheckConstraint(f"priority IN ({_values(TrainingPriority)})", name="priority_allowed"),
        CheckConstraint("current_revision_no IS NULL OR current_revision_no >= 1", name="revision_no_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status <> 'BLOCKED' OR blocked_reason IS NOT NULL",
            name="blocked_requires_reason",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancel_reason IS NOT NULL",
            name="cancelled_requires_reason",
        ),
        CheckConstraint(
            "status <> 'CLOSED' OR closed_at IS NOT NULL",
            name="closed_requires_time",
        ),
        Index("ix_training_requests_project_status", "project_id", "status", "created_at"),
        Index("ix_training_requests_project_owner", "project_id", "owner_id"),
    )


class TrainingRequestRevisionModel(Base):
    __tablename__ = "training_request_revisions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    training_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    supersedes_revision_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    revision_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_training_request_revisions_project_id_id"),
        UniqueConstraint(
            "project_id",
            "training_request_id",
            "id",
            name="uq_training_request_revisions_project_request_id_id",
        ),
        UniqueConstraint(
            "project_id",
            "training_request_id",
            "revision_no",
            name="uq_training_request_revisions_project_request_no",
        ),
        ForeignKeyConstraint(
            ["project_id", "training_request_id"],
            ["training_requests.project_id", "training_requests.id"],
            name="fk_training_request_revisions_project_request",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_revision_id"],
            ["training_request_revisions.project_id", "training_request_revisions.id"],
            name="fk_training_request_revisions_project_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(RevisionStatus)})", name="status_allowed"),
        CheckConstraint("revision_no >= 1", name="revision_no_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        _sha_check("revision_sha256", "revision_sha256_format"),
        CheckConstraint(
            "status NOT IN ('SUBMITTED', 'PENDING_REVIEW') OR "
            "(submitted_by IS NOT NULL AND submitted_at IS NOT NULL)",
            name="submitted_requires_actor",
        ),
        CheckConstraint(
            "status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approved_requires_actor",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR (rejected_by IS NOT NULL AND rejected_at IS NOT NULL "
            "AND rejection_reason IS NOT NULL)",
            name="rejected_requires_reason",
        ),
        Index(
            "ix_training_request_revisions_project_request_no",
            "project_id",
            "training_request_id",
            "revision_no",
        ),
    )


class TrainingDeliveryModel(Base):
    __tablename__ = "training_deliveries"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    training_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    training_request_revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    export_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    delivery_note_no: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_team: Mapped[str | None] = mapped_column(String(200), nullable=True)
    artifact_manifest_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifact_manifest_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    aggregate_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    split_counts: Mapped[dict[str, int]] = mapped_column(_JSON_TYPE, nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    format_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_report_ref: Mapped[str | None] = mapped_column(String(71), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acceptance_opinion: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    rework_ref: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_training_deliveries_project_id_id"),
        UniqueConstraint(
            "project_id", "delivery_note_no", name="uq_training_deliveries_project_note"
        ),
        ForeignKeyConstraint(
            ["project_id", "training_request_id", "training_request_revision_id"],
            [
                "training_request_revisions.project_id",
                "training_request_revisions.training_request_id",
                "training_request_revisions.id",
            ],
            name="fk_training_deliveries_project_request_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_training_deliveries_project_dataset_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "export_job_id"],
            ["export_jobs.project_id", "export_jobs.id"],
            name="fk_training_deliveries_project_export_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "rework_ref"],
            ["training_deliveries.project_id", "training_deliveries.id"],
            name="fk_training_deliveries_project_rework",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(TrainingDeliveryStatus)})", name="status_allowed"),
        CheckConstraint("length(delivery_note_no) >= 1", name="delivery_note_non_empty"),
        CheckConstraint("item_count >= 0", name="item_count_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status NOT IN ('DELIVERED', 'ACCEPTED', 'REJECTED', 'REWORK') "
            "OR delivered_at IS NOT NULL",
            name="delivered_requires_time",
        ),
        CheckConstraint(
            "status <> 'ACCEPTED' OR (accepted_by IS NOT NULL AND accepted_at IS NOT NULL "
            "AND acceptance_opinion IS NOT NULL)",
            name="accepted_requires_reviewer",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="rejected_requires_reason",
        ),
        _sha_check("artifact_manifest_sha256", "artifact_manifest_sha256_format"),
        _sha_check("aggregate_sha256", "aggregate_sha256_format"),
        CheckConstraint(
            "quality_report_ref IS NULL OR ("
            "length(quality_report_ref) = 71 AND substr(quality_report_ref, 1, 7) = 'sha256:')",
            name="quality_report_ref_format",
        ),
        Index("ix_training_deliveries_project_status", "project_id", "status", "created_at"),
        Index(
            "ix_training_deliveries_project_request",
            "project_id",
            "training_request_id",
            "created_at",
        ),
        Index(
            "ix_training_deliveries_project_version",
            "project_id",
            "dataset_version_id",
        ),
    )


class ExternalTrainingReferenceModel(Base):
    __tablename__ = "external_training_references"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    adapter_id: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    training_delivery_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    training_request_revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_run_url: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    external_status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_ref: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    evaluation_ref: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_external_training_references_project_id_id"),
        UniqueConstraint(
            "project_id",
            "adapter_id",
            "idempotency_key",
            name="uq_external_training_references_project_adapter_idempotency",
        ),
        UniqueConstraint(
            "project_id",
            "adapter_id",
            "external_run_id",
            name="uq_external_training_references_project_adapter_run",
        ),
        ForeignKeyConstraint(
            ["project_id", "training_delivery_id"],
            ["training_deliveries.project_id", "training_deliveries.id"],
            name="fk_external_training_references_project_delivery",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "training_request_revision_id"],
            ["training_request_revisions.project_id", "training_request_revisions.id"],
            name="fk_external_training_references_project_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_external_training_references_project_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"external_status IN ({_values(ExternalTrainingStatus)})",
            name="external_status_allowed",
        ),
        CheckConstraint("length(adapter_id) >= 1", name="adapter_id_non_empty"),
        CheckConstraint("length(adapter_version) >= 1", name="adapter_version_non_empty"),
        CheckConstraint("length(external_run_id) >= 1", name="external_run_id_non_empty"),
        CheckConstraint(
            "length(idempotency_key) >= 8 AND length(idempotency_key) <= 128",
            name="idempotency_key_length",
        ),
        CheckConstraint("length(correlation_id) >= 1", name="correlation_id_non_empty"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "external_status <> 'SUCCEEDED' OR status_stale = false",
            name="succeeded_not_stale",
        ),
        Index(
            "ix_external_training_references_project_delivery",
            "project_id",
            "training_delivery_id",
        ),
        Index(
            "ix_external_training_references_adapter_run",
            "adapter_id",
            "external_run_id",
        ),
    )


class DatasetGrantModel(Base):
    __tablename__ = "dataset_grants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    dataset_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    grantee_project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    grantee_team: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_grants_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "dataset_id"],
            ["datasets.project_id", "datasets.id"],
            name="fk_dataset_grants_project_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_dataset_grants_project_dataset_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(DatasetGrantStatus)})", name="status_allowed"),
        CheckConstraint("length(reason) >= 1", name="reason_non_empty"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status <> 'ACTIVE' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="active_requires_approval",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="rejected_requires_reason",
        ),
        CheckConstraint(
            "status <> 'REVOKED' OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL "
            "AND revoke_reason IS NOT NULL)",
            name="revoked_requires_reason",
        ),
        Index("ix_dataset_grants_project_dataset_status", "project_id", "dataset_id", "status"),
        Index("ix_dataset_grants_grantee_project", "grantee_project_id", "status"),
        Index("ix_dataset_grants_expires_at", "expires_at"),
    )


class TrainingAdapterCallbackModel(Base):
    """回调防重放记录：同一 adapter 内 event_id 幂等、nonce 窗口内唯一。"""

    __tablename__ = "training_adapter_callbacks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    adapter_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("adapter_id", "event_id", name="uq_training_adapter_callbacks_event"),
        UniqueConstraint("adapter_id", "nonce", name="uq_training_adapter_callbacks_nonce"),
        CheckConstraint(
            "length(event_id) >= 1 AND length(event_id) <= 128",
            name="event_id_length",
        ),
        CheckConstraint(
            "length(nonce) >= 8 AND length(nonce) <= 128",
            name="nonce_length",
        ),
        _sha_check("payload_sha256", "payload_sha256_format"),
        Index("ix_training_adapter_callbacks_received_at", "received_at"),
    )


__all__ = [
    "DatasetGrantModel",
    "ExternalTrainingReferenceModel",
    "TrainingAdapterCallbackModel",
    "TrainingDeliveryModel",
    "TrainingRequestModel",
    "TrainingRequestRevisionModel",
]
