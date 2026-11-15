"""标注分配聚合根。

分配表达人员工作量；领取/开始/提交/返工受状态机与租约约束。
对象级权限（本人领取、职责分离、容量）由应用层校验，数据库兜底跨项目与
非法迁移。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationAssignmentAction,
    AnnotationAssignmentStatus,
    AssignmentRole,
)
from app.domain.annotation.errors import (
    AssignmentExpiredError,
    AssignmentRevokedError,
    DomainValidationError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    assignment_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    REASON_MAX_LENGTH,
    coerce_enum,
    non_negative_int,
    optional_aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AnnotationAssignment:
    assignment_id: UUID
    project_id: UUID
    task_id: UUID
    batch_id: UUID
    assignee_id: UUID
    status: AnnotationAssignmentStatus | str = AnnotationAssignmentStatus.ASSIGNED
    assignment_role: AssignmentRole | str = AssignmentRole.ANNOTATOR
    planned_item_count: int = 0
    claimed_item_count: int = 0
    submitted_item_count: int = 0
    approved_item_count: int = 0
    planned_minutes: int = 0
    actual_minutes: int | None = None
    lease_expires_at: datetime | None = None
    claimed_at: datetime | None = None
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    revoked_reason: str | None = None
    status_reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("assignment_id", "project_id", "task_id", "batch_id", "assignee_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationAssignmentStatus, "status"),
        )
        object.__setattr__(
            self,
            "assignment_role",
            coerce_enum(self.assignment_role, AssignmentRole, "assignment_role"),
        )
        for name in (
            "planned_item_count",
            "claimed_item_count",
            "submitted_item_count",
            "approved_item_count",
            "planned_minutes",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        if self.actual_minutes is not None:
            object.__setattr__(
                self,
                "actual_minutes",
                non_negative_int(self.actual_minutes, "actual_minutes"),
            )
        for name in (
            "lease_expires_at",
            "claimed_at",
            "submitted_at",
            "completed_at",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "revoked_reason",
            optional_text(self.revoked_reason, "revoked_reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "status_reason",
            optional_text(self.status_reason, "status_reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        if self.status is AnnotationAssignmentStatus.EXPIRED and self.lease_expires_at is None:
            raise DomainValidationError("EXPIRED 分配必须保留 lease_expires_at")

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            AnnotationAssignmentStatus.COMPLETED,
            AnnotationAssignmentStatus.REVOKED,
            AnnotationAssignmentStatus.EXPIRED,
        }

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
        actual_minutes: int | None = None,
        submitted_item_count: int | None = None,
        approved_item_count: int | None = None,
        lease_expires_at: datetime | None = None,
    ) -> AnnotationAssignment:
        action_name = validate_action_name(action, family="assignment")
        # 撤销是终态：契约给它独立的 410 ``annotation_assignment_revoked``，
        # 而不是笼统的 ``state_transition_invalid``。客户端据此提示"请重新分配"。
        if self.status is AnnotationAssignmentStatus.REVOKED:
            raise AssignmentRevokedError(action_name, assignment_id=self.assignment_id)
        target = assignment_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("assignment", action_name) and cleaned_reason is None:
            raise DomainValidationError("该动作必须填写原因")

        if action_name == AnnotationAssignmentAction.CLAIM.value:
            if self.lease_expires_at is not None and self.lease_expires_at <= occurred_at:
                raise AssignmentExpiredError("分配已过期，需重新分配")
        if action_name == AnnotationAssignmentAction.SYSTEM_EXPIRE.value:
            if self.lease_expires_at is None or self.lease_expires_at > occurred_at:
                raise DomainValidationError("分配租约尚未到期，不能标记过期")

        changes: dict[str, object] = {
            "status": target,
            "updated_at": occurred_at,
        }
        if action_name == AnnotationAssignmentAction.CLAIM.value:
            changes["claimed_at"] = occurred_at
            if lease_expires_at is not None:
                changes["lease_expires_at"] = lease_expires_at
        if action_name == AnnotationAssignmentAction.RELEASE.value:
            changes["claimed_at"] = None
            changes["lease_expires_at"] = None
            changes["status_reason"] = cleaned_reason
        if action_name == AnnotationAssignmentAction.SUBMIT.value:
            changes["submitted_at"] = occurred_at
            if submitted_item_count is not None:
                changes["submitted_item_count"] = non_negative_int(
                    submitted_item_count,
                    "submitted_item_count",
                )
        if action_name == AnnotationAssignmentAction.COMPLETE.value:
            changes["completed_at"] = occurred_at
            if approved_item_count is not None:
                changes["approved_item_count"] = non_negative_int(
                    approved_item_count,
                    "approved_item_count",
                )
            if actual_minutes is not None:
                changes["actual_minutes"] = non_negative_int(actual_minutes, "actual_minutes")
        if action_name in {
            AnnotationAssignmentAction.REVOKE.value,
            AnnotationAssignmentAction.REWORK.value,
        }:
            changes["status_reason"] = cleaned_reason
        if action_name == AnnotationAssignmentAction.REVOKE.value:
            changes["revoked_reason"] = cleaned_reason
        return replace(self, **changes)


__all__ = ["AnnotationAssignment"]
