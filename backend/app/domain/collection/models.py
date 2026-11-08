"""采集任务与批次聚合根。

实体不可变；状态只能经具名动作迁移。实体只保证状态机与字段级规则，
跨对象前置条件（设备状态、批次与任务兼容、返工证据等）由应用层校验。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.collection.enums import (
    CollectionBatchStatus,
    CollectionTaskStatus,
    TaskPriority,
)
from app.domain.collection.errors import DomainValidationError
from app.domain.collection.state_machines import (
    action_requires_reason,
    assert_batch_action,
    assert_task_action,
    validate_action_name,
)
from app.domain.collection.validation import (
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    REASON_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    check_time_range,
    coerce_enum,
    frozen_mapping,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    uuid_tuple,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CollectionTask:
    task_id: UUID
    project_id: UUID
    code: str
    name: str
    priority: TaskPriority | str = TaskPriority.P1
    status: CollectionTaskStatus | str = CollectionTaskStatus.DRAFT
    requirement_id: UUID | None = None
    plan_id: UUID | None = None
    sop_id: UUID | None = None
    sop_version: str | None = None
    scenario_id: UUID | None = None
    action_spec_id: UUID | None = None
    action_spec_version: str | None = None
    assigned_to: UUID | None = None
    assigned_robot_id: UUID | None = None
    assigned_device_ids: tuple[UUID, ...] = ()
    assigned_sensor_ids: tuple[UUID, ...] = ()
    location_id: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    target_episode_count: int = 0
    target_duration_sec: int = 0
    submitted_episode_count: int = 0
    accepted_episode_count: int = 0
    quality_summary: Mapping[str, object] = field(default_factory=dict)
    status_reason: str | None = None
    blocked_reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(self, "status", coerce_enum(self.status, CollectionTaskStatus, "status"))
        object.__setattr__(
            self,
            "priority",
            coerce_enum(self.priority, TaskPriority, "priority"),
        )
        for name in (
            "requirement_id",
            "plan_id",
            "sop_id",
            "scenario_id",
            "action_spec_id",
            "assigned_to",
            "assigned_robot_id",
            "location_id",
        ):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        for name in ("sop_version", "action_spec_version"):
            object.__setattr__(
                self,
                name,
                optional_text(
                    getattr(self, name),
                    name,
                    max_length=VERSION_TEXT_MAX_LENGTH,
                ),
            )
        object.__setattr__(
            self,
            "status_reason",
            optional_text(
                self.status_reason,
                "status_reason",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "blocked_reason",
            optional_text(
                self.blocked_reason,
                "blocked_reason",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "assigned_device_ids",
            uuid_tuple(
                self.assigned_device_ids,
                "assigned_device_ids",
            ),
        )
        object.__setattr__(
            self,
            "assigned_sensor_ids",
            uuid_tuple(
                self.assigned_sensor_ids,
                "assigned_sensor_ids",
            ),
        )
        object.__setattr__(
            self,
            "quality_summary",
            frozen_mapping(
                self.quality_summary,
                "quality_summary",
            ),
        )
        for name in (
            "scheduled_start",
            "scheduled_end",
            "actual_start",
            "actual_end",
        ):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        check_time_range(self.scheduled_start, self.scheduled_end, "scheduled")
        check_time_range(self.actual_start, self.actual_end, "actual")
        for name in (
            "target_episode_count",
            "target_duration_sec",
            "submitted_episode_count",
            "accepted_episode_count",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    def apply_action(
        self,
        action: object,
        data: Mapping[str, object] | None = None,
        *,
        now: datetime | None = None,
    ) -> CollectionTask:
        """按具名动作迁移状态；未知动作/非法迁移一律拒绝。"""
        action_name = validate_action_name(action)
        target = assert_task_action(self.status, action_name)
        payload = dict(data or {})
        occurred_at = now or _now()
        reason = optional_text(
            payload.get("reason"),
            "reason",
            max_length=REASON_MAX_LENGTH,
        )
        if action_requires_reason(action_name) and reason is None:
            raise DomainValidationError("该动作必须填写原因")

        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        for field_name in (
            "assigned_to",
            "assigned_robot_id",
            "location_id",
        ):
            if field_name in payload:
                changes[field_name] = optional_uuid(payload[field_name], field_name)
        if "assigned_device_ids" in payload:
            changes["assigned_device_ids"] = uuid_tuple(
                payload["assigned_device_ids"],
                "assigned_device_ids",
            )
        if "assigned_sensor_ids" in payload:
            changes["assigned_sensor_ids"] = uuid_tuple(
                payload["assigned_sensor_ids"],
                "assigned_sensor_ids",
            )
        for field_name in ("scheduled_start", "scheduled_end"):
            if field_name in payload:
                changes[field_name] = optional_aware_datetime(
                    payload[field_name],
                    field_name,
                )

        if action_name == "start":
            changes["actual_start"] = self.actual_start or occurred_at
        if action_name in {"complete", "cancel"}:
            changes["actual_end"] = self.actual_end or occurred_at
        if action_name in {
            "return-draft",
            "pause",
            "resume",
            "reject",
            "rework",
            "cancel",
        }:
            changes["status_reason"] = reason
        if action_name == "block":
            changes["status_reason"] = reason
            changes["blocked_reason"] = reason
        if action_name == "unblock":
            changes["status_reason"] = reason
            changes["blocked_reason"] = None
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class CollectionBatch:
    batch_id: UUID
    project_id: UUID
    task_id: UUID
    code: str
    sequence_no: int
    status: CollectionBatchStatus | str = CollectionBatchStatus.PLANNED
    assigned_to: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    expected_count: int = 0
    submitted_count: int = 0
    accepted_count: int = 0
    status_reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, CollectionBatchStatus, "status"),
        )
        object.__setattr__(self, "task_id", optional_uuid(self.task_id, "task_id"))
        object.__setattr__(self, "assigned_to", optional_uuid(self.assigned_to, "assigned_to"))
        object.__setattr__(
            self,
            "sequence_no",
            positive_int(self.sequence_no, "sequence_no"),
        )
        object.__setattr__(
            self,
            "status_reason",
            optional_text(
                self.status_reason,
                "status_reason",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "scheduled_start",
            optional_aware_datetime(self.scheduled_start, "scheduled_start"),
        )
        object.__setattr__(
            self,
            "scheduled_end",
            optional_aware_datetime(self.scheduled_end, "scheduled_end"),
        )
        check_time_range(self.scheduled_start, self.scheduled_end, "scheduled")
        for name in ("expected_count", "submitted_count", "accepted_count"):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    def apply_action(
        self,
        action: object,
        data: Mapping[str, object] | None = None,
        *,
        now: datetime | None = None,
    ) -> CollectionBatch:
        action_name = validate_action_name(action, batch=True)
        target = assert_batch_action(self.status, action_name)
        payload = dict(data or {})
        occurred_at = now or _now()
        reason = optional_text(
            payload.get("reason"),
            "reason",
            max_length=REASON_MAX_LENGTH,
        )
        if action_requires_reason(action_name) and reason is None:
            raise DomainValidationError("该动作必须填写原因")

        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if "assigned_to" in payload:
            changes["assigned_to"] = optional_uuid(payload["assigned_to"], "assigned_to")
        if action_name in {
            "pause",
            "resume",
            "reject",
            "rework",
            "cancel",
        }:
            changes["status_reason"] = reason
        if action_name == "block":
            changes["status_reason"] = reason
        return replace(self, **changes)
