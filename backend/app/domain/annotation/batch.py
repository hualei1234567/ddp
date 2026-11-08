"""标注批次聚合根。

批次只能属于一个任务，状态不得领先任务；批次内样本必须来自任务已快照的
样本集（应用层与数据库外键/触发器双重校验）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationBatchAction,
    AnnotationBatchStatus,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.state_machines import (
    action_requires_reason,
    batch_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    REASON_MAX_LENGTH,
    coerce_enum,
    frozen_mapping,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
    uuid_tuple,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AnnotationBatch:
    batch_id: UUID
    project_id: UUID
    task_id: UUID
    code: str
    sequence_no: int
    status: AnnotationBatchStatus | str = AnnotationBatchStatus.PLANNED
    name: str | None = None
    stratum: str | None = None
    item_refs: tuple[UUID, ...] = ()
    planned_item_count: int = 0
    assignee_ids: tuple[UUID, ...] = ()
    reviewer_pool_ids: tuple[UUID, ...] = ()
    due_at: datetime | None = None
    submitted_item_count: int = 0
    approved_item_count: int = 0
    rejected_item_count: int = 0
    rework_count: int = 0
    quality_summary: Mapping[str, object] = field(default_factory=dict)
    status_reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("batch_id", "project_id", "task_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            optional_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationBatchStatus, "status"),
        )
        object.__setattr__(self, "sequence_no", positive_int(self.sequence_no, "sequence_no"))
        object.__setattr__(
            self,
            "stratum",
            optional_text(self.stratum, "stratum", max_length=100),
        )
        object.__setattr__(self, "item_refs", uuid_tuple(self.item_refs, "item_refs"))
        object.__setattr__(
            self,
            "assignee_ids",
            uuid_tuple(self.assignee_ids, "assignee_ids"),
        )
        object.__setattr__(
            self,
            "reviewer_pool_ids",
            uuid_tuple(self.reviewer_pool_ids, "reviewer_pool_ids"),
        )
        for name in (
            "planned_item_count",
            "submitted_item_count",
            "approved_item_count",
            "rejected_item_count",
            "rework_count",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        if self.planned_item_count < len(self.item_refs):
            raise DomainValidationError("planned_item_count 不得小于 item_refs 数量")
        object.__setattr__(
            self,
            "quality_summary",
            frozen_mapping(self.quality_summary, "quality_summary"),
        )
        object.__setattr__(
            self,
            "status_reason",
            optional_text(self.status_reason, "status_reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(self, "due_at", optional_aware_datetime(self.due_at, "due_at"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationBatch:
        action_name = validate_action_name(action, family="batch")
        target = batch_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("batch", action_name) and cleaned_reason is None:
            raise DomainValidationError("该动作必须填写原因")
        changes: dict[str, object] = {
            "status": target,
            "updated_at": occurred_at,
        }
        if action_name in {
            AnnotationBatchAction.PAUSE.value,
            AnnotationBatchAction.RESUME.value,
            AnnotationBatchAction.BLOCK.value,
            AnnotationBatchAction.UNBLOCK.value,
            AnnotationBatchAction.REJECT.value,
            AnnotationBatchAction.ESCALATE.value,
            AnnotationBatchAction.RESOLVE_REJECTED.value,
            AnnotationBatchAction.REWORK.value,
            AnnotationBatchAction.CANCEL.value,
        }:
            changes["status_reason"] = cleaned_reason
        return replace(self, **changes)


__all__ = ["AnnotationBatch"]
