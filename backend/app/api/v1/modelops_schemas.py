"""阶段 5 HTTP 请求模型与响应序列化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.modelops.gaps import GapDimension, GapSummaryItem
from app.domain.modelops.models import (
    DataRequirement,
    Evaluation,
    InferenceService,
    InferenceTask,
    Model,
    ModelFeedback,
    ModelVersion,
)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, object] | None = None


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    task_type: str
    framework: str = Field(min_length=1, max_length=100)
    owner_id: UUID
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    framework: str | None = Field(default=None, min_length=1, max_length=100)
    tags: list[str] | None = None


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_key: str = Field(min_length=1, max_length=1024)
    sha256: str
    size_bytes: int = Field(ge=1)
    format: str


class RuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str = Field(min_length=1, max_length=100)
    framework_version: str = Field(min_length=1, max_length=100)
    python_version: str | None = Field(default=None, max_length=32)
    cuda_version: str | None = Field(default=None, max_length=32)
    image_ref: str | None = Field(default=None, max_length=512)


class ModelVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactInput
    runtime: RuntimeInput
    training_request_id: UUID | None = None
    training_delivery_id: UUID | None = None
    external_ref_id: UUID | None = None
    dataset_version_ids: list[UUID] = Field(default_factory=list)
    metrics_summary: dict[str, float] = Field(default_factory=dict)
    release_notes: str | None = Field(default=None, max_length=4000)


class EvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    dataset_version_id: UUID
    metrics: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, dict[str, float | None]]
    baseline_evaluation_id: UUID | None = None
    adapter_id: str | None = Field(default=None, min_length=1, max_length=100)


class InferenceServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    environment: str
    endpoint_url: str = Field(min_length=1, max_length=1024)
    resource_ref: str | None = Field(default=None, max_length=512)
    config: dict[str, object] = Field(default_factory=dict)


class InferenceTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    service_id: UUID | None = None
    dataset_version_id: UUID | None = None
    episode_ids: list[UUID] = Field(default_factory=list)
    adapter_id: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, object] = Field(default_factory=dict)


class GapDimensionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_type: str
    dimension_value: str = Field(min_length=1, max_length=200)
    current_count: int | None = Field(default=None, ge=0)
    target_count: int | None = Field(default=None, ge=0)

    def to_domain(self) -> GapDimension:
        return GapDimension(
            dimension_type=self.dimension_type,
            dimension_value=self.dimension_value,
            current_count=self.current_count,
            target_count=self.target_count,
        )


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    evaluation_id: UUID | None = None
    inference_task_id: UUID | None = None
    issue_type: str
    severity: str
    description: str = Field(min_length=1, max_length=4000)
    dimensions: list[GapDimensionInput] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class FeedbackActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    resolution_note: str | None = Field(default=None, max_length=2000)
    requirement_code: str | None = Field(default=None, max_length=64)
    requirement_title: str | None = Field(default=None, max_length=200)
    target_episode_count: int | None = Field(default=None, ge=1)
    due_at: datetime | None = None
    priority: str | None = None


class RequirementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_type: str
    source_feedback_ids: list[UUID] = Field(default_factory=list)
    source_evaluation_ids: list[UUID] = Field(default_factory=list)
    dimensions: list[GapDimensionInput] = Field(min_length=1)
    target_episode_count: int = Field(default=1, ge=1)
    quality_thresholds: dict[str, object] = Field(default_factory=dict)
    priority: str = "P1"
    due_at: datetime | None = None
    owner_id: UUID


class RequirementActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    dataset_version_ids: list[UUID] = Field(default_factory=list)
    evaluation_id: UUID | None = None
    satisfaction_evidence_ref: str | None = Field(default=None, max_length=1024)


class ModelAdapterCallbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=100)
    event_type: str
    project_id: UUID
    evaluation_id: UUID | None = None
    task_id: UUID | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    verdict: str | None = None
    report_ref: dict[str, object] | None = None
    result_ref: dict[str, object] | None = None
    log_ref: str | None = Field(default=None, max_length=1024)
    error_code: str | None = Field(default=None, max_length=100)
    error_summary: str | None = Field(default=None, max_length=2000)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def model_out(model: Model) -> dict[str, Any]:
    return {
        "model_id": model.model_id,
        "project_id": model.project_id,
        "code": model.code,
        "name": model.name,
        "description": model.description,
        "task_type": model.task_type,
        "framework": model.framework,
        "owner_id": model.owner_id,
        "status": model.status,
        "current_version_id": model.current_version_id,
        "tags": list(model.tags or []),
        "allowed_actions": sorted(model.allowed_actions),
        "version": model.row_version,
        "created_at": _iso(model.created_at),
        "updated_at": _iso(model.updated_at),
    }


def version_out(version: ModelVersion) -> dict[str, Any]:
    return {
        "model_version_id": version.model_version_id,
        "model_id": version.model_id,
        "project_id": version.project_id,
        "version_no": version.version_no,
        "status": version.status,
        "artifact": version.artifact.to_payload(),
        "runtime": version.runtime.to_payload(),
        "training_request_id": version.training_request_id,
        "training_delivery_id": version.training_delivery_id,
        "external_ref_id": version.external_ref_id,
        "dataset_version_ids": [str(item) for item in version.dataset_version_ids or []],
        "metrics_summary": dict(version.metrics_summary or {}),
        "release_notes": version.release_notes,
        "validated_at": _iso(version.validated_at),
        "released_by": version.released_by,
        "released_at": _iso(version.released_at),
        "deprecated_at": _iso(version.deprecated_at),
        "deprecation_reason": version.deprecation_reason,
        "allowed_actions": sorted(version.allowed_actions),
        "version": version.row_version,
        "created_at": _iso(version.created_at),
        "updated_at": _iso(version.updated_at),
    }


def evaluation_out(evaluation: Evaluation) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation.evaluation_id,
        "project_id": evaluation.project_id,
        "model_version_id": evaluation.model_version_id,
        "dataset_version_id": evaluation.dataset_version_id,
        "baseline_evaluation_id": evaluation.baseline_evaluation_id,
        "status": evaluation.status,
        "metrics": dict(evaluation.metrics or {}),
        "thresholds": evaluation.thresholds,
        "verdict": evaluation.verdict,
        "report_ref": evaluation.report_ref,
        "error_code": evaluation.error_code,
        "error_summary": evaluation.error_summary,
        "started_at": _iso(evaluation.started_at),
        "finished_at": _iso(evaluation.finished_at),
        "allowed_actions": sorted(evaluation.allowed_actions),
        "version": evaluation.row_version,
        "created_at": _iso(evaluation.created_at),
        "updated_at": _iso(evaluation.updated_at),
    }


def service_out(service: InferenceService) -> dict[str, Any]:
    return {
        "service_id": service.service_id,
        "project_id": service.project_id,
        "model_version_id": service.model_version_id,
        "environment": service.environment,
        "endpoint_url": service.endpoint_url,
        "resource_ref": service.resource_ref,
        "config": dict(service.config or {}),
        "status": service.status,
        "health_status": service.health_status,
        "deployed_at": _iso(service.deployed_at),
        "stopped_at": _iso(service.stopped_at),
        "allowed_actions": sorted(service.allowed_actions),
        "version": service.row_version,
        "created_at": _iso(service.created_at),
        "updated_at": _iso(service.updated_at),
    }


def task_out(task: InferenceTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "project_id": task.project_id,
        "model_version_id": task.model_version_id,
        "service_id": task.service_id,
        "dataset_version_id": task.dataset_version_id,
        "episode_ids": [str(item) for item in task.episode_ids or []],
        "status": task.status,
        "result_ref": task.result_ref,
        "metrics": dict(task.metrics or {}),
        "log_ref": task.log_ref,
        "error_code": task.error_code,
        "error_summary": task.error_summary,
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "allowed_actions": sorted(task.allowed_actions),
        "version": task.row_version,
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
    }


def feedback_out(feedback: ModelFeedback) -> dict[str, Any]:
    return {
        "feedback_id": feedback.feedback_id,
        "project_id": feedback.project_id,
        "model_version_id": feedback.model_version_id,
        "evaluation_id": feedback.evaluation_id,
        "inference_task_id": feedback.inference_task_id,
        "issue_type": feedback.issue_type,
        "severity": feedback.severity,
        "description": feedback.description,
        "dimensions": [item.to_payload() for item in feedback.dimensions],
        "evidence_refs": list(feedback.evidence_refs or []),
        "status": feedback.status,
        "converted_requirement_id": feedback.converted_requirement_id,
        "resolution_note": feedback.resolution_note,
        "allowed_actions": sorted(feedback.allowed_actions),
        "version": feedback.row_version,
        "created_at": _iso(feedback.created_at),
        "updated_at": _iso(feedback.updated_at),
    }


def requirement_out(requirement: DataRequirement) -> dict[str, Any]:
    return {
        "requirement_id": requirement.requirement_id,
        "project_id": requirement.project_id,
        "code": requirement.code,
        "title": requirement.title,
        "description": requirement.description,
        "source_type": requirement.source_type,
        "source_feedback_ids": [str(item) for item in requirement.source_feedback_ids or []],
        "source_evaluation_ids": [str(item) for item in requirement.source_evaluation_ids or []],
        "dimensions": [item.to_payload() for item in requirement.dimensions],
        "target_episode_count": requirement.target_episode_count,
        "quality_thresholds": dict(requirement.quality_thresholds or {}),
        "priority": requirement.priority,
        "due_at": _iso(requirement.due_at),
        "owner_id": requirement.owner_id,
        "status": requirement.status,
        "linked_dataset_version_ids": [
            str(item) for item in requirement.linked_dataset_version_ids or []
        ],
        "retest_evaluation_ids": [str(item) for item in requirement.retest_evaluation_ids or []],
        "satisfied_at": _iso(requirement.satisfied_at),
        "satisfaction_evidence_ref": requirement.satisfaction_evidence_ref,
        "allowed_actions": sorted(requirement.allowed_actions),
        "version": requirement.row_version,
        "created_at": _iso(requirement.created_at),
        "updated_at": _iso(requirement.updated_at),
    }


def gap_summary_out(items: list[GapSummaryItem]) -> dict[str, Any]:
    return {
        "items": [item.to_payload() for item in items],
        "freshness": "FRESH",
    }
