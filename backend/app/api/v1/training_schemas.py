"""训练需求、交付、外部引用与授权 HTTP 模型与序列化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.datasets.dataset_grants import DatasetGrant
from app.domain.datasets.training_deliveries import TrainingDelivery
from app.domain.datasets.training_enums import ExecutionMode
from app.domain.datasets.training_references import ExternalTrainingReference
from app.domain.datasets.training_requests import TrainingRequest, TrainingRequestRevision
from app.domain.datasets.training_state_machines import allowed_external_actions


class RequirementSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_goal: str = Field(min_length=1, max_length=2000)
    model_type: str = Field(min_length=1, max_length=100)
    training_type: str
    data_modalities: list[str] = Field(min_length=1)
    scene_targets: list[str] = Field(default_factory=list)
    action_targets: list[str] = Field(default_factory=list)
    robot_targets: list[str] = Field(default_factory=list)
    device_targets: list[str] = Field(default_factory=list)
    required_labels: list[str] = Field(default_factory=list)
    required_schema_versions: list[dict[str, object]] = Field(default_factory=list)
    target_episode_count: int | None = Field(default=None, ge=1)
    target_duration_ns: int | None = Field(default=None, ge=1)
    quality_thresholds: dict[str, object]
    split_requirements: dict[str, object] | None = None
    leakage_policy_id: UUID | None = None
    format: str
    format_version: str
    delivery_due_at: datetime
    acceptance_method: str = Field(min_length=1, max_length=500)
    priority: str


class TrainingRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    owner_id: UUID
    priority: str
    snapshot: RequirementSnapshot


class TrainingRequestRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: RequirementSnapshot
    change_reason: str = Field(min_length=1, max_length=2000)


class TrainingRequestRevisionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: RequirementSnapshot | None = None
    change_reason: str | None = Field(default=None, max_length=2000)


class TrainingActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    restore_status: str | None = Field(default=None, max_length=64)
    rework_ref: UUID | None = None
    acceptance_opinion: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, object] | None = None


class TrainingDeliveryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_request_id: UUID
    training_request_revision_id: UUID
    dataset_version_id: UUID
    export_job_id: UUID
    recipient_id: UUID
    recipient_team: str | None = Field(default=None, max_length=200)
    delivery_note_no: str = Field(min_length=1, max_length=64)


class ExternalTrainingReferenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    correlation_id: str = Field(min_length=1, max_length=128)
    model_ref: dict[str, object] | None = None
    evaluation_ref: dict[str, object] | None = None
    config: dict[str, object] = Field(default_factory=dict)
    # 可选显式执行方式。Adapter 配置已声明时以配置为准且必须一致；两边都没有
    # 时服务端拒绝（422），不默认 REMOTE。
    #
    # 这里用 Literal 而不是 ExecutionMode：UNKNOWN 只描述存量数据，不是客户端
    # 可声明的执行方式，运行时 OpenAPI 也必须与契约一致地只暴露这四个值。
    execution_mode: Literal["LOCAL", "MOCK", "DRY_RUN", "REMOTE"] | None = None


class DatasetGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version_id: UUID | None = None
    grantee_project_id: UUID
    grantee_team: str | None = Field(default=None, max_length=200)
    scope: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    expires_at: datetime


class AdapterCallbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    external_run_id: str = Field(min_length=1, max_length=200)
    external_status: str
    occurred_at: datetime
    model_ref: dict[str, object] | None = None
    evaluation_ref: dict[str, object] | None = None
    error_code: str | None = Field(default=None, max_length=100)
    error_summary: str | None = Field(default=None, max_length=2000)
    payload: dict[str, object] | None = None


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def training_request_out(
    request: TrainingRequest,
    *,
    allowed_actions: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    return {
        "training_request_id": request.training_request_id,
        "project_id": request.project_id,
        "code": request.code,
        "title": request.title,
        "status": _value(request.status),
        "current_revision_id": request.current_revision_id,
        "current_revision_no": request.current_revision_no,
        "priority": _value(request.priority),
        "owner_id": request.owner_id,
        "blocked_reason": request.blocked_reason,
        "blocked_from_status": request.blocked_from_status,
        "cancel_reason": request.cancel_reason,
        "closed_at": _iso(request.closed_at),
        "allowed_actions": sorted(allowed_actions),
        "version": request.row_version,
        "created_at": _iso(request.created_at),
        "updated_at": _iso(request.updated_at),
        "created_by": request.created_by,
        "updated_by": request.updated_by,
    }


def training_revision_out(revision: TrainingRequestRevision) -> dict[str, Any]:
    return {
        "revision_id": revision.revision_id,
        "training_request_id": revision.training_request_id,
        "project_id": revision.project_id,
        "revision_no": revision.revision_no,
        "status": _value(revision.status),
        "snapshot": revision.snapshot,
        "change_reason": revision.change_reason,
        "supersedes_revision_id": revision.supersedes_revision_id,
        "submitted_by": revision.submitted_by,
        "submitted_at": _iso(revision.submitted_at),
        "reviewed_by": revision.reviewed_by,
        "reviewed_at": _iso(revision.reviewed_at),
        "approved_by": revision.approved_by,
        "approved_at": _iso(revision.approved_at),
        "rejected_by": revision.rejected_by,
        "rejected_at": _iso(revision.rejected_at),
        "rejection_reason": revision.rejection_reason,
        "revision_sha256": revision.revision_sha256,
        "allowed_actions": sorted(revision.allowed_actions),
        "version": revision.row_version,
        "created_at": _iso(revision.created_at),
        "created_by": revision.created_by,
    }


def training_delivery_out(
    delivery: TrainingDelivery, *, execution_mode: ExecutionMode
) -> dict[str, Any]:
    return {
        "training_delivery_id": delivery.training_delivery_id,
        "project_id": delivery.project_id,
        "training_request_id": delivery.training_request_id,
        "training_request_revision_id": delivery.training_request_revision_id,
        "dataset_version_id": delivery.dataset_version_id,
        "export_job_id": delivery.export_job_id,
        "delivery_note_no": delivery.delivery_note_no,
        "recipient_id": delivery.recipient_id,
        "recipient_team": delivery.recipient_team,
        "artifact_manifest_key": delivery.artifact_manifest_key,
        "artifact_manifest_sha256": delivery.artifact_manifest_sha256,
        "aggregate_sha256": delivery.aggregate_sha256,
        "item_count": delivery.item_count,
        "split_counts": delivery.split_counts,
        "format": delivery.format,
        "format_version": delivery.format_version,
        "quality_report_ref": delivery.quality_report_ref,
        # TRN-002 核对报告：``None`` 表示该交付建立于核对能力上线之前，
        # 客户端不得把 ``None`` 渲染成"已通过"。
        "requirement_match": delivery.requirement_match,
        # 交付级执行方式：最近一次外部训练引用的方式；尚未提交训练为 UNKNOWN。
        # 客户端不得把 UNKNOWN 渲染成"真实训练"。
        "execution_mode": execution_mode.value,
        "status": _value(delivery.status),
        "delivered_at": _iso(delivery.delivered_at),
        "accepted_by": delivery.accepted_by,
        "accepted_at": _iso(delivery.accepted_at),
        "acceptance_opinion": delivery.acceptance_opinion,
        "rejection_reason": delivery.rejection_reason,
        "rework_ref": delivery.rework_ref,
        "allowed_actions": sorted(delivery.allowed_actions),
        "version": delivery.row_version,
        "created_at": _iso(delivery.created_at),
        "updated_at": _iso(delivery.updated_at),
        "created_by": delivery.created_by,
    }


def external_reference_out(reference: ExternalTrainingReference) -> dict[str, Any]:
    return {
        "external_ref_id": reference.external_ref_id,
        "project_id": reference.project_id,
        "adapter_id": reference.adapter_id,
        "adapter_version": reference.adapter_version,
        "training_delivery_id": reference.training_delivery_id,
        "training_request_revision_id": reference.training_request_revision_id,
        "dataset_version_id": reference.dataset_version_id,
        "external_run_id": reference.external_run_id,
        "external_run_url": reference.external_run_url,
        "external_status": _value(reference.external_status),
        # 执行方式在提交时定死；MOCK/DRY_RUN/UNKNOWN 必须在界面上与真实训练区分。
        "execution_mode": _value(reference.execution_mode),
        "status_stale": reference.status_stale,
        "submitted_at": _iso(reference.submitted_at),
        "last_synced_at": _iso(reference.last_synced_at),
        "model_ref": reference.model_ref,
        "evaluation_ref": reference.evaluation_ref,
        # 产物元数据（object_key/sha256/size_bytes）；模型版本登记从这里取，
        # 不再需要回查 Adapter 的运维接口。
        "artifacts": reference.artifacts,
        "correlation_id": reference.correlation_id,
        "idempotency_key": reference.idempotency_key,
        "error_code": reference.error_code,
        "error_summary": reference.error_summary,
        "allowed_actions": sorted(allowed_external_actions(reference.effective_status)),
        "version": reference.row_version,
        "created_at": _iso(reference.created_at),
        "updated_at": _iso(reference.updated_at),
    }


def grant_out(grant: DatasetGrant, *, effective_status: object | None = None) -> dict[str, Any]:
    status = effective_status if effective_status is not None else grant.status
    return {
        "grant_id": grant.grant_id,
        "project_id": grant.project_id,
        "dataset_id": grant.dataset_id,
        "dataset_version_id": grant.dataset_version_id,
        "grantee_project_id": grant.grantee_project_id,
        "grantee_team": grant.grantee_team,
        "scope": list(grant.scope),
        "status": _value(status),
        "reason": grant.reason,
        "approved_by": grant.approved_by,
        "approved_at": _iso(grant.approved_at),
        "rejected_by": grant.rejected_by,
        "rejected_at": _iso(grant.rejected_at),
        "rejection_reason": grant.rejection_reason,
        "expires_at": _iso(grant.expires_at),
        "revoked_at": _iso(grant.revoked_at),
        "revoked_by": grant.revoked_by,
        "revoke_reason": grant.revoke_reason,
        "allowed_actions": sorted(grant.allowed_actions),
        "version": grant.row_version,
        "created_at": _iso(grant.created_at),
        "updated_at": _iso(grant.updated_at),
        "created_by": grant.created_by,
    }


__all__ = [
    "AdapterCallbackPayload",
    "DatasetGrantCreate",
    "ExternalTrainingReferenceCreate",
    "RequirementSnapshot",
    "TrainingActionRequest",
    "TrainingDeliveryCreate",
    "TrainingRequestCreate",
    "TrainingRequestRevisionCreate",
    "TrainingRequestRevisionPatch",
    "external_reference_out",
    "grant_out",
    "training_delivery_out",
    "training_revision_out",
    "training_request_out",
]
