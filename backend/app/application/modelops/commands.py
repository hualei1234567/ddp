"""阶段 5 应用层命令对象。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.modelops.artifacts import ModelArtifact, ModelRuntime
from app.domain.modelops.gaps import GapDimension


@dataclass(frozen=True, slots=True)
class NewModel:
    code: str
    name: str
    task_type: str
    framework: str
    owner_id: UUID
    description: str | None = None
    tags: list[str] | None = None


@dataclass(frozen=True, slots=True)
class NewModelVersion:
    artifact: ModelArtifact
    runtime: ModelRuntime
    training_request_id: UUID | None = None
    training_delivery_id: UUID | None = None
    external_ref_id: UUID | None = None
    dataset_version_ids: list[UUID] | None = None
    metrics_summary: dict[str, float] | None = None
    release_notes: str | None = None


@dataclass(frozen=True, slots=True)
class NewEvaluation:
    model_version_id: UUID
    dataset_version_id: UUID
    metrics: dict[str, float]
    thresholds: dict[str, dict[str, float | None]]
    baseline_evaluation_id: UUID | None = None
    adapter_id: str | None = None
    config: dict[str, object] | None = None
    # 跨数据集版本对比必须显式声明；未声明时跨数据集直接拒绝。
    # 默认 False 保证既有调用方行为不变（同数据集基线照旧允许）。
    cross_dataset_comparison: bool = False


@dataclass(frozen=True, slots=True)
class NewInferenceService:
    model_version_id: UUID
    environment: str
    endpoint_url: str
    resource_ref: str | None = None
    config: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class NewInferenceTask:
    model_version_id: UUID
    service_id: UUID | None = None
    dataset_version_id: UUID | None = None
    episode_ids: list[UUID] | None = None
    adapter_id: str | None = None
    config: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class NewFeedback:
    model_version_id: UUID
    issue_type: str
    severity: str
    description: str
    dimensions: list[GapDimension]
    evaluation_id: UUID | None = None
    inference_task_id: UUID | None = None
    evidence_refs: list[str] | None = None


@dataclass(frozen=True, slots=True)
class NewRequirement:
    code: str
    title: str
    source_type: str
    dimensions: list[GapDimension]
    owner_id: UUID
    target_episode_count: int = 1
    description: str | None = None
    source_feedback_ids: list[UUID] | None = None
    source_evaluation_ids: list[UUID] | None = None
    quality_thresholds: dict[str, object] | None = None
    priority: str = "P1"
    due_at: datetime | None = None


__all__ = [
    "NewEvaluation",
    "NewFeedback",
    "NewInferenceService",
    "NewInferenceTask",
    "NewModel",
    "NewModelVersion",
    "NewRequirement",
]
