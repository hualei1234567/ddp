"""采集任务/批次 API DTO。

请求模型 ``extra="forbid"``，客户端不能提交 status、project_id、version、
审计字段或计数汇总等服务端权威字段。响应字段与阶段 2 OpenAPI 草案一致。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.collection.enums import (
    PUBLIC_BATCH_ACTIONS,
    PUBLIC_TASK_ACTIONS,
    CollectionBatchStatus,
    CollectionTaskStatus,
)
from app.domain.collection.models import CollectionBatch, CollectionTask
from app.domain.collection.state_machines import (
    allowed_batch_actions,
    allowed_task_actions,
)


class CollectionTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    priority: str = Field(pattern="^P[012]$")
    target_episode_count: int = Field(ge=0)
    target_duration_sec: int = Field(ge=0)
    requirement_id: UUID | None = None
    plan_id: UUID | None = None
    sop_id: UUID | None = None
    sop_version: str | None = Field(default=None, max_length=64)
    scenario_id: UUID | None = None
    action_spec_id: UUID | None = None
    action_spec_version: str | None = Field(default=None, max_length=64)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    location_id: UUID | None = None


class CollectionTaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    priority: str | None = Field(default=None, pattern="^P[012]$")
    sop_version: str | None = Field(default=None, max_length=64)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    target_episode_count: int | None = Field(default=None, ge=0)
    target_duration_sec: int | None = Field(default=None, ge=0)
    location_id: UUID | None = None


class CollectionTaskActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    assigned_to: UUID | None = None
    assigned_robot_id: UUID | None = None
    assigned_device_ids: list[UUID] | None = None
    assigned_sensor_ids: list[UUID] | None = None
    location_id: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    waiver_reason: str | None = Field(default=None, max_length=1000)


class CollectionBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_count: int = Field(ge=0)
    code: str | None = Field(default=None, max_length=64)
    sequence_no: int | None = Field(default=None, ge=1)
    assigned_to: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None


class CollectionBatchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_count: int | None = Field(default=None, ge=0)
    assigned_to: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None


class CollectionBatchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    assigned_to: UUID | None = None


class CollectionTaskOut(BaseModel):
    task_id: UUID
    project_id: UUID
    code: str
    name: str
    requirement_id: UUID | None
    plan_id: UUID | None
    sop_id: UUID | None
    sop_version: str | None
    scenario_id: UUID | None
    action_spec_id: UUID | None
    action_spec_version: str | None
    status: str
    priority: str
    assigned_to: UUID | None
    assigned_robot_id: UUID | None
    assigned_device_ids: list[UUID]
    assigned_sensor_ids: list[UUID]
    location_id: UUID | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    target_episode_count: int
    target_duration_sec: int
    submitted_episode_count: int
    accepted_episode_count: int
    # 服务端算好的可执行动作。前端按规范只渲染这里列出的按钮，不再自己
    # 推断状态机——两边各写一份迁移表迟早会分叉。
    allowed_actions: list[str]
    quality_summary: dict[str, object] | None
    status_reason: str | None
    blocked_reason: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, task: CollectionTask) -> CollectionTaskOut:
        return cls(
            task_id=task.task_id,
            project_id=task.project_id,
            code=task.code,
            name=task.name,
            requirement_id=task.requirement_id,
            plan_id=task.plan_id,
            sop_id=task.sop_id,
            sop_version=task.sop_version,
            scenario_id=task.scenario_id,
            action_spec_id=task.action_spec_id,
            action_spec_version=task.action_spec_version,
            status=_value(task.status),
            priority=_value(task.priority),
            assigned_to=task.assigned_to,
            assigned_robot_id=task.assigned_robot_id,
            assigned_device_ids=list(task.assigned_device_ids),
            assigned_sensor_ids=list(task.assigned_sensor_ids),
            location_id=task.location_id,
            scheduled_start=task.scheduled_start,
            scheduled_end=task.scheduled_end,
            actual_start=task.actual_start,
            actual_end=task.actual_end,
            target_episode_count=task.target_episode_count,
            target_duration_sec=task.target_duration_sec,
            submitted_episode_count=task.submitted_episode_count,
            accepted_episode_count=task.accepted_episode_count,
            quality_summary=dict(task.quality_summary) or None,
            status_reason=task.status_reason,
            blocked_reason=task.blocked_reason,
            version=task.version,
            created_at=task.created_at,
            updated_at=task.updated_at,
            created_by=task.created_by,
            updated_by=task.updated_by,
            allowed_actions=sorted(
                allowed_task_actions(CollectionTaskStatus(task.status)) & PUBLIC_TASK_ACTIONS
            ),
        )


class CollectionBatchOut(BaseModel):
    batch_id: UUID
    project_id: UUID
    task_id: UUID
    code: str
    sequence_no: int
    status: str
    assigned_to: UUID | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    expected_count: int
    submitted_count: int
    accepted_count: int
    allowed_actions: list[str]
    status_reason: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, batch: CollectionBatch) -> CollectionBatchOut:
        return cls(
            batch_id=batch.batch_id,
            project_id=batch.project_id,
            task_id=batch.task_id,
            code=batch.code,
            sequence_no=batch.sequence_no,
            status=_value(batch.status),
            assigned_to=batch.assigned_to,
            scheduled_start=batch.scheduled_start,
            scheduled_end=batch.scheduled_end,
            expected_count=batch.expected_count,
            submitted_count=batch.submitted_count,
            accepted_count=batch.accepted_count,
            allowed_actions=sorted(
                allowed_batch_actions(CollectionBatchStatus(_value(batch.status)))
                & PUBLIC_BATCH_ACTIONS
            ),
            status_reason=batch.status_reason,
            version=batch.version,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
