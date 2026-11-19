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
    CaptureSessionStatus,
    CollectionBatchStatus,
    CollectionTaskStatus,
    TaskPriority,
)
from app.domain.collection.errors import DomainValidationError
from app.domain.collection.state_machines import (
    action_requires_reason,
    assert_batch_action,
    assert_session_action,
    assert_task_action,
    session_action_requires_reason,
    validate_action_name,
    validate_session_action_name,
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


@dataclass(frozen=True, slots=True)
class CaptureSession:
    """一次真实的采集执行会话。

    与 :class:`CollectionTask` 的边界见 ADR-0037：

    - 任务回答"要采什么、按什么方案、谁来审"；会话回答"这段执行在什么时候、
      由谁、在哪台机器人/哪个场地上真正开录，何时暂停/恢复/结束"。
    - 一个任务可以有多次会话（设备切换、暂停后换人、失败重来），因此会话
      **不是**任务的字段扩展，而是独立的执行记录聚合。
    - 会话不承载审核流、不改变任务状态、不写素材版本；它只记录执行事实。

    状态与时间字段的一致性在实体层强制：非法组合（例如 ``RUNNING`` 却没有
    ``started_at``、终态没有 ``ended_at``）一律拒绝，避免脏数据靠下游猜。
    """

    session_id: UUID
    project_id: UUID
    code: str
    task_id: UUID | None = None
    batch_id: UUID | None = None
    operator_id: UUID | None = None
    robot_id: UUID | None = None
    location_id: UUID | None = None
    status: CaptureSessionStatus | str = CaptureSessionStatus.SCHEDULED
    started_at: datetime | None = None
    ended_at: datetime | None = None
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
            coerce_enum(self.status, CaptureSessionStatus, "status"),
        )
        for name in ("task_id", "batch_id", "operator_id", "robot_id", "location_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "started_at",
            optional_aware_datetime(self.started_at, "started_at"),
        )
        object.__setattr__(
            self,
            "ended_at",
            optional_aware_datetime(self.ended_at, "ended_at"),
        )
        if self.started_at is not None and self.ended_at is not None:
            if self.ended_at < self.started_at:
                raise DomainValidationError("ended_at 不得早于 started_at")
        object.__setattr__(
            self,
            "status_reason",
            optional_text(
                self.status_reason,
                "status_reason",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        self._check_status_time_consistency()

    def _check_status_time_consistency(self) -> None:
        status = self.status
        assert isinstance(status, CaptureSessionStatus)
        if status is CaptureSessionStatus.SCHEDULED:
            if self.started_at is not None or self.ended_at is not None:
                raise DomainValidationError("SCHEDULED 会话不得带开始或结束时间")
            return
        if status in {CaptureSessionStatus.RUNNING, CaptureSessionStatus.PAUSED}:
            if self.started_at is None or self.ended_at is not None:
                raise DomainValidationError("执行中的会话必须有开始时间且不得有结束时间")
            return
        if self.ended_at is None:
            raise DomainValidationError("终态会话必须有结束时间")
        if status is CaptureSessionStatus.COMPLETED and self.started_at is None:
            raise DomainValidationError("COMPLETED 会话必须有开始时间")
        # ABORTED 允许 started_at 为空：未开录就放弃也是一次合法中止。

    def apply_action(
        self,
        action: object,
        data: Mapping[str, object] | None = None,
        *,
        now: datetime | None = None,
    ) -> CaptureSession:
        """按具名动作迁移状态；未知动作/非法迁移一律拒绝。"""
        action_name = validate_session_action_name(action)
        target = assert_session_action(self.status, action_name)
        payload = dict(data or {})
        occurred_at = now or _now()
        reason = optional_text(
            payload.get("reason"),
            "reason",
            max_length=REASON_MAX_LENGTH,
        )
        if session_action_requires_reason(action_name) and reason is None:
            raise DomainValidationError("该动作必须填写原因")

        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if action_name == "start":
            changes["started_at"] = self.started_at or occurred_at
        if action_name in {"complete", "abort"}:
            changes["ended_at"] = self.ended_at or occurred_at
        if action_name in {"pause", "resume", "abort"}:
            changes["status_reason"] = reason
        return replace(self, **changes)
