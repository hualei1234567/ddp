"""阶段 5 ORM：模型、版本、评测、推理、反馈与数据需求。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.modelops.enums import (
    EvaluationStatus,
    EvaluationVerdict,
    FeedbackIssueType,
    FeedbackSeverity,
    FeedbackStatus,
    HealthStatus,
    InferenceEnvironment,
    InferenceServiceStatus,
    InferenceTaskStatus,
    ModelStatus,
    ModelTaskType,
    ModelVersionStatus,
    RequirementPriority,
    RequirementSourceType,
    RequirementStatus,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


def _enum_check(column: str, enum_type: type, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({_values(enum_type)})", name=name)


def _sha_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(_SHA.format(column=column), name=name)


class ModelRow(Base):
    __tablename__ = "models"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    framework: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    tags: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_models_project_code"),
        _enum_check("status", ModelStatus, "ck_models_status"),
        _enum_check("task_type", ModelTaskType, "ck_models_task_type"),
        Index("ix_models_project_status", "project_id", "status"),
    )


class ModelVersionRow(Base):
    __tablename__ = "model_versions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("models.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    artifact: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)
    runtime: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    training_request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    training_delivery_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    external_ref_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    dataset_version_ids: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    metrics_summary: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    release_notes: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deprecation_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "model_id", "version_no", name="uq_model_versions_no"),
        _enum_check("status", ModelVersionStatus, "ck_model_versions_status"),
        _sha_check("artifact_sha256", "ck_model_versions_artifact_sha"),
        CheckConstraint("version_no >= 1", name="ck_model_versions_positive"),
        Index("ix_model_versions_model_status", "project_id", "model_id", "status"),
    )


class EvaluationRow(Base):
    __tablename__ = "evaluations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False
    )
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    baseline_evaluation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    metrics: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    report_ref: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        _enum_check("status", EvaluationStatus, "ck_evaluations_status"),
        _enum_check("verdict", EvaluationVerdict, "ck_evaluations_verdict"),
        Index("ix_evaluations_project_status", "project_id", "status"),
        Index("ix_evaluations_dataset", "project_id", "dataset_version_id"),
    )


class InferenceServiceRow(Base):
    __tablename__ = "inference_services"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False
    )
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    resource_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    config: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        _enum_check("status", InferenceServiceStatus, "ck_inference_services_status"),
        _enum_check("environment", InferenceEnvironment, "ck_inference_services_env"),
        _enum_check("health_status", HealthStatus, "ck_inference_services_health"),
        Index("ix_inference_services_project_status", "project_id", "status"),
    )


class InferenceTaskRow(Base):
    __tablename__ = "inference_tasks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    dataset_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    episode_ids: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_ref: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    metrics: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    log_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        _enum_check("status", InferenceTaskStatus, "ck_inference_tasks_status"),
        Index("ix_inference_tasks_project_status", "project_id", "status"),
    )


class ModelFeedbackRow(Base):
    __tablename__ = "model_feedback"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    inference_task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    issue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(String(4000), nullable=False)
    dimensions: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    converted_requirement_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        _enum_check("status", FeedbackStatus, "ck_model_feedback_status"),
        _enum_check("issue_type", FeedbackIssueType, "ck_model_feedback_issue"),
        _enum_check("severity", FeedbackSeverity, "ck_model_feedback_severity"),
        Index("ix_model_feedback_project_status", "project_id", "status"),
    )


class DataRequirementRow(Base):
    __tablename__ = "data_requirements"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_feedback_ids: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    source_evaluation_ids: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    dimensions: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False)
    target_episode_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_thresholds: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    linked_dataset_version_ids: Mapped[list] = mapped_column(
        _JSON_TYPE, nullable=False, default=list
    )
    retest_evaluation_ids: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    satisfaction_evidence_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_data_requirements_project_code"),
        _enum_check("status", RequirementStatus, "ck_data_requirements_status"),
        _enum_check("source_type", RequirementSourceType, "ck_data_requirements_source"),
        _enum_check("priority", RequirementPriority, "ck_data_requirements_priority"),
        CheckConstraint("target_episode_count >= 1", name="ck_data_requirements_target"),
        Index("ix_data_requirements_project_status", "project_id", "status"),
    )


class ModelCallbackRow(Base):
    __tablename__ = "model_callbacks"

    adapter_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("adapter_id", "nonce", name="uq_model_callbacks_nonce"),
        _sha_check("payload_sha256", "ck_model_callbacks_payload_sha"),
    )
