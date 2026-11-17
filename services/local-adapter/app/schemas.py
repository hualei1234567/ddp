"""入站请求模型：字段与 DDP AdapterGateway 的 submit 参数一一对应。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TrainingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    correlation_id: str = Field(min_length=1, max_length=128)
    # DDP 出站网关实际字段是 training_delivery_id；delivery_id 为兼容旧调用保留。
    training_delivery_id: UUID | None = None
    delivery_id: UUID | None = None
    dataset_version_id: UUID
    config: dict[str, object] = Field(default_factory=dict)


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    evaluation_id: UUID
    model_version_id: UUID
    dataset_version_id: UUID
    config: dict[str, object] = Field(default_factory=dict)


class InferenceRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    task_id: UUID
    model_version_id: UUID
    service_id: UUID | None = None
    dataset_version_id: UUID | None = None
    episode_ids: list[UUID] = Field(default_factory=list)
    config: dict[str, object] = Field(default_factory=dict)


__all__ = ["EvaluationRunRequest", "InferenceRunRequest", "TrainingRunRequest"]
