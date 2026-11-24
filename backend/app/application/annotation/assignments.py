"""标注分配用例：分配、领取、开始、释放、撤销、过期、提交、完成与工作量。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationAssignment
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    ZERO_OPEN_PLANNED,
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
    AssignmentRevokedError,
    BatchNotCompatibleError,
    DomainValidationError,
    ItemAlreadyAssignedError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.annotation.review_policy import ReviewMode
from app.domain.annotation.state_machines import (
    allowed_assignment_actions,
    assert_assignment_action,
    validate_action_name,
)
from app.domain.annotation.workload import (
    AssignmentCapacityPreview,
    CapacityPolicy,
    WorkloadSummary,
)
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
        # 契约区分"未批准"（annotation_task_not_approved）与"已批准但未就绪"
        # （annotation_task_not_ready）；这两个前置条件在任务聚合根里判定一次。
        # 其余状态（已在执行/已结束/已取消）属于"批次与任务不兼容"，仍按原码返回。
        task.assert_assignable()
        if task.status not in _CREATABLE_TASK_STATUSES:
            raise BatchNotCompatibleError("任务状态不允许分配")
        if batch.status not in _CREATABLE_BATCH_STATUSES:
            raise BatchNotCompatibleError("批次状态不允许分配")
        role = AssignmentRole(command.assignment_role)
        self._assert_member(scope, command.assignee_id)
        self._assert_not_already_assigned(
            scope,
            batch_id=batch_id,
            assignee_id=command.assignee_id,
            assignment_role=role,
        )
        if role is AssignmentRole.ANNOTATOR and task.review_mode is ReviewMode.DOUBLE:
            self._assert_double_annotation_independent(
                scope,
                task_id=task.task_id,
                batch=batch,
                assignee_id=command.assignee_id,
            )
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
        # 撤销先于状态机断言：终态在状态机里同样"不允许任何动作"，若先断言
        # 就只会得到笼统的 state_transition_invalid，客户端无法据此提示重新分配。
        if current.status is AnnotationAssignmentStatus.REVOKED:
            raise AssignmentRevokedError(action_name, assignment_id=current.assignment_id)
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

    def preview_assignment_capacity(
        self,
        scope: ProjectScope,
        *,
        assignee_ids: tuple[UUID, ...],
        planned_item_count: int,
        planned_minutes: int,
        capacity: CapacityPolicy | None = None,
    ) -> list[AssignmentCapacityPreview]:
        """分配前实时负载预览（只读，不落库、不写审计、不推进版本）。

        这是 :meth:`_assert_capacity` 的**同源只读视图**：两边都用
        :meth:`CapacityPolicy.exceeded` 判定，都按"未结束分配的计划项数/计划
        工时"取占用量，因此"预览说会超"与"提交被拒"不会各说各话。唯一的
        差别是预览一次算多人（批量聚合），且不抛错——它要回答"会不会超"，
        而不是替调用方阻断。

        人员资格照旧校验（非项目成员 404），与 :meth:`workload` 一致：
        预览不能成为成员枚举的旁路。
        """
        if capacity is None:
            capacity = CapacityPolicy()
        if not assignee_ids:
            raise DomainValidationError("assignee_ids 不得为空")
        # 去重但保持顺序：同一人重复传入不应被算两次，也不应悄悄改顺序
        # （前端按选择顺序展示）。
        ordered: list[UUID] = []
        seen: set[UUID] = set()
        for assignee_id in assignee_ids:
            if assignee_id in seen:
                continue
            seen.add(assignee_id)
            ordered.append(assignee_id)
            if not self._members.user_is_member(scope, assignee_id):
                raise ResourceNotFoundError("项目成员", assignee_id)
        totals = self._assignments.sum_open_planned_by_assignee(
            scope,
            assignee_ids=tuple(ordered),
        )
        previews: list[AssignmentCapacityPreview] = []
        for assignee_id in ordered:
            open_totals = totals.get(assignee_id, ZERO_OPEN_PLANNED)
            previews.append(
                AssignmentCapacityPreview(
                    assignee_id=assignee_id,
                    capacity=capacity,
                    open_item_count=open_totals.planned_item_count,
                    open_planned_minutes=open_totals.planned_minutes,
                    planned_item_count=planned_item_count,
                    planned_minutes=planned_minutes,
                )
            )
        return previews

    def _assert_not_already_assigned(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
        assignee_id: UUID,
        assignment_role: AssignmentRole,
    ) -> None:
        """同批次 + 同人 + 同角色重复分配必须报 ``annotation_item_already_assigned``。

        数据库唯一约束 ``uq_annotation_assignments_batch_assignee_role`` 会挡住这次
        插入，但落到仓储层只会得到笼统的 ``duplicate_resource``（"业务键重复"），
        调用方无法判断是"同一份活被派了两遍"。这里先把冲突翻译成契约码，并按
        现有记录的状态说明为什么不能直接复用（例如已 REVOKED 需走别的路径）。
        """
        for existing in self._assignments.list_for_batch(scope, batch_id=batch_id):
            if (
                existing.assignee_id != assignee_id
                or existing.assignment_role is not assignment_role
            ):
                continue
            raise ItemAlreadyAssignedError(
                "该批次已为该人员分配过同角色任务",
                details={
                    "assignment_id": str(existing.assignment_id),
                    "batch_id": str(batch_id),
                    "assignee_id": str(assignee_id),
                    "assignment_role": assignment_role.value,
                    "existing_status": existing.status.value,
                },
            )

    def _assert_double_annotation_independent(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        batch,
        assignee_id: UUID,
    ) -> None:
        """双标任务中，同一 Episode 不能被同一标注者在两个批次重复领取。

        同批次重复已由 :meth:`_assert_not_already_assigned` 挡住；这里补的是
        "跨批次同一 Episode 同一人"，否则所谓双标会退化成同一人提交两份。

        查**全部历史分配**（含已提交/已完成/已撤回），不是只看活动分配：
        已结束的分配同样证明该人已经标注过这批 Episode，漏掉终态会让"先完成
        一份、再领第二份"绕过独立性。引用的批次读不到时拒绝而不是跳过——
        数据不一致时无法证明独立性，fail-closed 比放行安全。
        """
        current_items = set(batch.item_refs)
        if not current_items:
            return
        for existing in self._assignments.list_for_task(scope, task_id=task_id):
            if (
                existing.assignee_id != assignee_id
                or existing.assignment_role is not AssignmentRole.ANNOTATOR
                or existing.batch_id == batch.batch_id
            ):
                continue
            other_batch = self._batches.get(scope, existing.batch_id)
            if other_batch is None:
                raise DomainValidationError("双标任务存在无法读取的历史批次，不能确认标注者独立性")
            overlap = current_items & set(other_batch.item_refs)
            if overlap:
                raise ItemAlreadyAssignedError(
                    "双标任务同一 Episode 不得由同一标注者重复标注",
                    details={
                        "task_id": str(task_id),
                        "assignee_id": str(assignee_id),
                        "episode_ids": sorted(str(item) for item in overlap),
                    },
                )

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
        """硬阻断：判定规则唯一来源是 :meth:`CapacityPolicy.exceeded`。

        分配前预览走同一个方法，因此不存在"预览一套、提交另一套"的漂移。
        按需取数（先看该上限是否配置）是为了不为一台只配了项数上限的部署
        多发一条分钟聚合查询。
        """
        if capacity is None:
            return
        # 逐项判定并保持原有的惰性取数：先看项数上限，未触发才去查工时聚合，
        # 避免为一次注定被拒的分配多发一条查询。
        if capacity.max_open_items is not None:
            open_items = self._assignments.sum_open_planned_items(
                scope,
                assignee_id=assignee_id,
                exclude_assignment_id=exclude_assignment_id,
            )
            if "max_open_items" in capacity.exceeded(
                open_item_count=open_items,
                planned_item_count=planned_items,
            ):
                raise AssignmentCapacityExceededError("分配超过人员容量，需 AM 审批")
        if capacity.max_planned_minutes is not None:
            open_minutes = self._assignments.sum_open_planned_minutes(
                scope,
                assignee_id=assignee_id,
                exclude_assignment_id=exclude_assignment_id,
            )
            if "max_planned_minutes" in capacity.exceeded(
                open_planned_minutes=open_minutes,
                planned_minutes=planned_minutes,
            ):
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
