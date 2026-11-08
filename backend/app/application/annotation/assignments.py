"""标注分配用例：分配、领取、开始、释放、撤销、过期、提交、完成与工作量。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationAssignment
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationAssignmentRepository,
    AnnotationBatchRepository,
    AnnotationTaskRepository,
    AuditRepository,
    IdempotencyRepository,
    MemberDirectory,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.assignment import AnnotationAssignment
from app.domain.annotation.enums import (
    AnnotationAssignmentAction,
    AnnotationAssignmentStatus,
    AnnotationBatchStatus,
    AnnotationTaskStatus,
    AssignmentRole,
)
from app.domain.annotation.errors import (
    AssignmentCapacityExceededError,
    AssignmentNotOwnedError,
    BatchNotCompatibleError,
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.annotation.state_machines import (
    allowed_assignment_actions,
    assert_assignment_action,
    validate_action_name,
)
from app.domain.annotation.workload import CapacityPolicy, WorkloadSummary
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "ANNOTATION_ASSIGNMENT"
_CREATABLE_TASK_STATUSES = {
    AnnotationTaskStatus.READY,
    AnnotationTaskStatus.ASSIGNED,
    AnnotationTaskStatus.IN_PROGRESS,
}
_CREATABLE_BATCH_STATUSES = {
    AnnotationBatchStatus.PLANNED,
    AnnotationBatchStatus.READY,
    AnnotationBatchStatus.ASSIGNED,
    AnnotationBatchStatus.IN_PROGRESS,
}
_ASSIGNEE_ONLY_ACTIONS = {
    AnnotationAssignmentAction.CLAIM.value,
    AnnotationAssignmentAction.START.value,
    AnnotationAssignmentAction.RELEASE.value,
    AnnotationAssignmentAction.SUBMIT.value,
}


class AnnotationAssignmentUseCases:
    def __init__(
        self,
        tasks: AnnotationTaskRepository,
        batches: AnnotationBatchRepository,
        assignments: AnnotationAssignmentRepository,
        members: MemberDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._tasks = tasks
        self._batches = batches
        self._assignments = assignments
        self._members = members
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, assignment_id: UUID) -> AnnotationAssignment:
        assignment = self._assignments.get(scope, assignment_id)
        if assignment is None:
            raise ResourceNotFoundError("标注分配", assignment_id)
        return assignment

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        assignee_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AnnotationAssignment]:
        if status is not None:
            try:
                AnnotationAssignmentStatus(status)
            except ValueError as exc:
                raise DomainValidationError("status 取值非法") from exc
        return self._assignments.list(
            scope,
            task_id=task_id,
            batch_id=batch_id,
            assignee_id=assignee_id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def allowed_actions(
        self,
        scope: ProjectScope,
        assignment_id: UUID,
    ) -> frozenset[str]:
        return allowed_assignment_actions(self.get(scope, assignment_id).status)

    def create(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        command: NewAnnotationAssignment,
        *,
        capacity: CapacityPolicy | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationAssignment:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        batch = self._get_batch(scope, batch_id)
        task = self._get_task(scope, batch.task_id)
        if task.status not in _CREATABLE_TASK_STATUSES:
            raise BatchNotCompatibleError("任务状态不允许分配")
        if batch.status not in _CREATABLE_BATCH_STATUSES:
            raise BatchNotCompatibleError("批次状态不允许分配")
        role = AssignmentRole(command.assignment_role)
        self._assert_member(scope, command.assignee_id)
        if self._assignments.has_conflicting_role(
            scope,
            batch_id=batch_id,
            assignee_id=command.assignee_id,
            assignment_role=role.value,
        ):
            raise DomainValidationError("同一批次同一人不得承担冲突角色")
        self._assert_capacity(
            scope,
            command.assignee_id,
            planned_items=command.planned_item_count,
            planned_minutes=command.planned_minutes,
            capacity=capacity,
        )
        assignment = AnnotationAssignment(
            assignment_id=uuid4(),
            project_id=scope.project_id,
            task_id=task.task_id,
            batch_id=batch.batch_id,
            assignee_id=command.assignee_id,
            assignment_role=role,
            planned_item_count=command.planned_item_count,
            planned_minutes=command.planned_minutes,
            lease_expires_at=command.lease_expires_at,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._assignments.add(scope, assignment)
        self._record(scope, created, "assign", None, None, now, request_id=request_id)
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.assignment_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        assignment_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        actual_minutes: int | None = None,
        submitted_item_count: int | None = None,
        approved_item_count: int | None = None,
        capacity: CapacityPolicy | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationAssignment:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
            "actual_minutes": actual_minutes,
            "submitted_item_count": submitted_item_count,
            "approved_item_count": approved_item_count,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        action_name = validate_action_name(action, family="assignment")
        current = self.get(scope, assignment_id)
        if current.version != expected_version:
            raise VersionConflictError("标注分配")
        assert_assignment_action(current.status, action_name)
        if action_name in _ASSIGNEE_ONLY_ACTIONS and current.assignee_id != scope.actor_id:
            raise AssignmentNotOwnedError("该分配不属于当前用户")
        self._assert_member(scope, current.assignee_id)
        if action_name in {
            AnnotationAssignmentAction.START.value,
            AnnotationAssignmentAction.SUBMIT.value,
        }:
            self._assert_work_state(scope, current, action_name)
        if action_name == AnnotationAssignmentAction.CLAIM.value:
            self._assert_capacity(
                scope,
                current.assignee_id,
                planned_items=current.planned_item_count,
                planned_minutes=current.planned_minutes,
                capacity=capacity,
                exclude_assignment_id=current.assignment_id,
            )
        minutes = 0
        if action_name == AnnotationAssignmentAction.COMPLETE.value:
            minutes = actual_minutes or current.actual_minutes or 0
        updated = replace(
            current.apply_action(
                action_name,
                reason=reason,
                now=now,
                actual_minutes=actual_minutes,
                submitted_item_count=submitted_item_count,
                approved_item_count=approved_item_count,
            ),
            updated_by=scope.actor_id,
        )
        saved = self._assignments.save(scope, updated, expected_version=expected_version)
        self._record(
            scope,
            saved,
            action_name,
            current.status.value,
            reason,
            now,
            request_id,
            minutes,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.assignment_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def expire_due(
        self,
        scope: ProjectScope,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
    ) -> int:
        """系统任务入口：把到租约的分配标记 EXPIRED；不占用 HTTP 线程。"""
        current_time = now or _utcnow()
        expired = 0
        for assignment in self._assignments.list(
            scope,
            status=AnnotationAssignmentStatus.ASSIGNED.value,
            limit=500,
        ):
            if assignment.lease_expires_at is None or assignment.lease_expires_at > current_time:
                continue
            updated = replace(
                assignment.apply_action("system-expire", now=current_time),
                updated_by=scope.actor_id,
            )
            saved = self._assignments.save(
                scope,
                updated,
                expected_version=assignment.version,
            )
            self._record(
                scope,
                saved,
                "system-expire",
                assignment.status.value,
                None,
                current_time,
                request_id,
                0,
            )
            expired += 1
        for assignment in self._assignments.list(
            scope,
            status=AnnotationAssignmentStatus.CLAIMED.value,
            limit=500,
        ):
            if assignment.lease_expires_at is None or assignment.lease_expires_at > current_time:
                continue
            updated = replace(
                assignment.apply_action("system-expire", now=current_time),
                updated_by=scope.actor_id,
            )
            saved = self._assignments.save(
                scope,
                updated,
                expected_version=assignment.version,
            )
            self._record(
                scope,
                saved,
                "system-expire",
                assignment.status.value,
                None,
                current_time,
                request_id,
                0,
            )
            expired += 1
        if expired:
            self._uow.commit()
        return expired

    def workload(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        period_start: datetime,
        period_end: datetime,
        capacity: CapacityPolicy | None = None,
    ) -> WorkloadSummary:
        if period_end < period_start:
            raise DomainValidationError("period_end 不得早于 period_start")
        if not self._members.user_is_member(scope, assignee_id):
            raise ResourceNotFoundError("项目成员", assignee_id)
        aggregate = self._assignments.aggregate_workload(
            scope,
            assignee_id=assignee_id,
            period_start=period_start,
            period_end=period_end,
        )
        summary = WorkloadSummary(
            project_id=scope.project_id,
            user_id=assignee_id,
            period_start=period_start,
            period_end=period_end,
            assigned_item_count=aggregate.assigned_item_count,
            claimed_item_count=aggregate.claimed_item_count,
            submitted_item_count=aggregate.submitted_item_count,
            approved_item_count=aggregate.approved_item_count,
            rework_item_count=aggregate.rework_item_count,
            total_minutes=aggregate.total_minutes,
            capacity_minutes=capacity.capacity_minutes if capacity else None,
        )
        return replace(summary, status=summary.effective_status)

    def _assert_work_state(
        self,
        scope: ProjectScope,
        assignment: AnnotationAssignment,
        action: str,
    ) -> None:
        task = self._get_task(scope, assignment.task_id)
        batch = self._get_batch(scope, assignment.batch_id)
        if action == "start":
            allowed_task = {
                AnnotationTaskStatus.IN_PROGRESS,
                AnnotationTaskStatus.REWORK,
            }
            allowed_batch = {
                AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchStatus.REWORK,
            }
        else:
            allowed_task = {
                AnnotationTaskStatus.IN_PROGRESS,
                AnnotationTaskStatus.PAUSED,
                AnnotationTaskStatus.BLOCKED,
                AnnotationTaskStatus.SUBMITTED,
                AnnotationTaskStatus.REVIEWING,
            }
            allowed_batch = {
                AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchStatus.PAUSED,
                AnnotationBatchStatus.BLOCKED,
                AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchStatus.REVIEWING,
            }
        if task.status not in allowed_task or batch.status not in allowed_batch:
            raise BatchNotCompatibleError("任务或批次状态不允许该分配动作")

    def _assert_capacity(
        self,
        scope: ProjectScope,
        assignee_id: UUID,
        *,
        planned_items: int,
        planned_minutes: int,
        capacity: CapacityPolicy | None,
        exclude_assignment_id: UUID | None = None,
    ) -> None:
        if capacity is None:
            return
        if capacity.max_open_items is not None:
            used = self._assignments.sum_open_planned_items(
                scope,
                assignee_id=assignee_id,
                exclude_assignment_id=exclude_assignment_id,
            )
            if used + planned_items > capacity.max_open_items:
                raise AssignmentCapacityExceededError("分配超过人员容量，需 AM 审批")
        if capacity.max_planned_minutes is not None:
            used_minutes = self._assignments.sum_open_planned_minutes(
                scope,
                assignee_id=assignee_id,
                exclude_assignment_id=exclude_assignment_id,
            )
            if used_minutes + planned_minutes > capacity.max_planned_minutes:
                raise AssignmentCapacityExceededError("分配工时超过人员容量，需 AM 审批")

    def _assert_member(self, scope: ProjectScope, user_id: UUID) -> None:
        if not self._members.user_is_member(scope, user_id):
            raise ResourceNotFoundError("项目成员", user_id)

    def _record(
        self,
        scope: ProjectScope,
        assignment: AnnotationAssignment,
        action: str,
        from_status: str | None,
        reason: str | None,
        now: datetime,
        request_id: str | None = None,
        minutes: int = 0,
    ) -> None:
        self._assignments.add_event(
            scope,
            event_id=uuid4(),
            assignment=assignment,
            action=action,
            from_status=from_status,
            actor_id=scope.actor_id,
            reason=reason,
            minutes=minutes,
            occurred_at=now,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=assignment.assignment_id,
            action=action,
            from_status=from_status,
            to_status=assignment.status.value,
            reason=reason,
            request_id=request_id,
            details={
                "task_id": str(assignment.task_id),
                "batch_id": str(assignment.batch_id),
                "assignee_id": str(assignment.assignee_id),
                "assignment_role": assignment.assignment_role.value,
            },
            event_type="annotation.assignment.status_changed",
            event_payload={
                "assignment_id": str(assignment.assignment_id),
                "task_id": str(assignment.task_id),
                "batch_id": str(assignment.batch_id),
                "from_status": from_status,
                "to_status": assignment.status.value,
                "action": action,
                "version": assignment.version,
            },
            occurred_at=now,
        )

    def _get_task(self, scope: ProjectScope, task_id: UUID):
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        return task

    def _get_batch(self, scope: ProjectScope, batch_id: UUID):
        batch = self._batches.get(scope, batch_id)
        if batch is None:
            raise ResourceNotFoundError("标注批次", batch_id)
        return batch

    def _replay(
        self,
        scope: ProjectScope,
        response_ref: str | None,
    ) -> AnnotationAssignment:
        if not response_ref:
            raise ResourceNotFoundError("标注分配", response_ref)
        try:
            assignment_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("标注分配", response_ref) from exc
        return self.get(scope, assignment_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "AnnotationAssignmentUseCases",
    "NewAnnotationAssignment",
]
