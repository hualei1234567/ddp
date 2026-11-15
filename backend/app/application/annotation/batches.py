"""标注批次用例：拆分、样本集约束、状态机与任务兼容校验。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationBatch
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationAssignmentRepository,
    AnnotationBatchRepository,
    AnnotationSampleSetRepository,
    AnnotationTaskRepository,
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.batch import AnnotationBatch
from app.domain.annotation.enums import (
    AnnotationAssignmentStatus,
    AnnotationBatchStatus,
    AnnotationSampleSetStatus,
    AnnotationTaskStatus,
)
from app.domain.annotation.errors import (
    BatchNotCompatibleError,
    BatchOverCapacityError,
    ConflictError,
    DomainValidationError,
    ResourceImmutableError,
    ResourceNotFoundError,
    TaskNotApprovedError,
    TaskOpenWorkError,
    VersionConflictError,
)
from app.domain.annotation.state_machines import (
    assert_batch_action,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "ANNOTATION_BATCH"
_CREATABLE_TASK_STATUSES = {
    AnnotationTaskStatus.APPROVED,
    AnnotationTaskStatus.READY,
    AnnotationTaskStatus.ASSIGNED,
    AnnotationTaskStatus.IN_PROGRESS,
}


class AnnotationBatchUseCases:
    def __init__(
        self,
        tasks: AnnotationTaskRepository,
        batches: AnnotationBatchRepository,
        assignments: AnnotationAssignmentRepository,
        sample_sets: AnnotationSampleSetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._tasks = tasks
        self._batches = batches
        self._assignments = assignments
        self._sample_sets = sample_sets
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, batch_id: UUID) -> AnnotationBatch:
        batch = self._batches.get(scope, batch_id)
        if batch is None:
            raise ResourceNotFoundError("标注批次", batch_id)
        return batch

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationBatch]:
        self._get_task(scope, task_id)
        if status is not None:
            try:
                AnnotationBatchStatus(status)
            except ValueError as exc:
                raise DomainValidationError("status 取值非法") from exc
        return self._batches.list(
            scope,
            task_id=task_id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def create(
        self,
        scope: ProjectScope,
        task_id: UUID,
        command: NewAnnotationBatch,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationBatch:
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

        task = self._get_task(scope, task_id)
        if task.status not in _CREATABLE_TASK_STATUSES:
            raise TaskNotApprovedError("任务未批准，不能创建批次")
        sample_set = self._sample_sets.get(scope, task.sample_set_id)
        if sample_set is None or sample_set.status not in {
            AnnotationSampleSetStatus.SNAPSHOTTED,
            AnnotationSampleSetStatus.CONSUMED,
        }:
            raise BatchNotCompatibleError("任务样本集快照不可用")
        allowed_episode_ids = {ref.episode_id for ref in sample_set.episode_refs}
        if not set(command.item_refs).issubset(allowed_episode_ids):
            raise BatchNotCompatibleError("批次样本必须来自任务已快照样本集")
        planned_total = (
            self._batches.sum_planned_item_count(scope, task_id) + command.planned_item_count
        )
        if planned_total > task.target_item_count:
            raise BatchOverCapacityError("批次计划量之和不得超过任务目标")
        sequence_no = command.sequence_no or self._batches.next_sequence_no(scope, task_id)
        # code 必须在**项目内**唯一（唯一键是 (project_id, code)），而
        # sequence_no 只在单个任务内递增。此前 code 只含 sequence_no，导致
        # 同一项目里第二个任务建第一个批次时必然撞
        # uq_annotation_batches_project_code / uq_collection_batches_project_code
        # ——"多任务并行标注/采集"这条常规路径直接不可用。带上 task 短标识
        # 即可同时满足"项目内唯一"与"人类可读"。
        code = command.code or f"B-{task_id.hex[:8]}-{sequence_no:04d}"
        batch = AnnotationBatch(
            batch_id=uuid4(),
            project_id=scope.project_id,
            task_id=task_id,
            code=code,
            name=command.name,
            sequence_no=sequence_no,
            stratum=command.stratum,
            item_refs=command.item_refs,
            planned_item_count=command.planned_item_count,
            due_at=command.due_at,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._batches.add(scope, batch)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.batch_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "task_id": str(task_id),
                "planned_item_count": created.planned_item_count,
            },
            event_type="annotation.batch.created",
            event_payload={
                "batch_id": str(created.batch_id),
                "task_id": str(task_id),
                "code": created.code,
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.batch_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> AnnotationBatch:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        unknown = set(changes) - {
            "name",
            "stratum",
            "item_refs",
            "planned_item_count",
            "due_at",
        }
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        current = self.get(scope, batch_id)
        if current.version != expected_version:
            raise VersionConflictError("标注批次")
        if current.status is not AnnotationBatchStatus.PLANNED:
            raise ResourceImmutableError("仅 PLANNED 批次允许修改核心字段")
        task = self._get_task(scope, current.task_id)
        sample_set = self._sample_sets.get(scope, task.sample_set_id)
        if sample_set is None:
            raise ResourceNotFoundError("标注样本集", task.sample_set_id)
        item_refs = tuple(changes.get("item_refs", current.item_refs))
        if not set(item_refs).issubset({ref.episode_id for ref in sample_set.episode_refs}):
            raise BatchNotCompatibleError("批次样本必须来自任务已快照样本集")
        planned_item_count = int(changes.get("planned_item_count", current.planned_item_count))
        total = (
            self._batches.sum_planned_item_count(scope, current.task_id)
            - current.planned_item_count
            + planned_item_count
        )
        if total > task.target_item_count:
            raise BatchOverCapacityError("批次计划量之和不得超过任务目标")
        now = _utcnow()
        updated = replace(
            current,
            **dict(changes),
            updated_at=now,
            updated_by=scope.actor_id,
        )
        saved = self._batches.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.batch_id,
            action="update",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes)},
            event_type="annotation.batch.updated",
            event_payload={
                "batch_id": str(saved.batch_id),
                "fields": sorted(changes),
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationBatch:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
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

        action_name = validate_action_name(action, family="batch")
        current = self.get(scope, batch_id)
        if current.version != expected_version:
            raise VersionConflictError("标注批次")
        assert_batch_action(current.status, action_name)
        task = self._get_task(scope, current.task_id)
        self._check_task_compatible(current, action_name, task.status)
        assignments = self._assignments.list_for_batch(scope, batch_id=batch_id)
        if action_name == "start":
            if not any(
                item.status
                in {
                    AnnotationAssignmentStatus.CLAIMED,
                    AnnotationAssignmentStatus.IN_PROGRESS,
                }
                for item in assignments
            ):
                raise ConflictError("批次开始前至少需要一个已领取的分配")
        if action_name == "submit":
            active = [
                item
                for item in assignments
                if item.status
                not in {
                    AnnotationAssignmentStatus.REVOKED,
                    AnnotationAssignmentStatus.EXPIRED,
                }
            ]
            if not active:
                raise TaskOpenWorkError("批次提交前必须存在有效分配")
            if any(
                item.status
                not in {
                    AnnotationAssignmentStatus.SUBMITTED,
                    AnnotationAssignmentStatus.COMPLETED,
                }
                for item in active
            ):
                raise TaskOpenWorkError("存在未提交分配，批次不能提交")
        if action_name == "cancel" and any(
            item.status is AnnotationAssignmentStatus.SUBMITTED for item in assignments
        ):
            raise TaskOpenWorkError("存在未结提交，批次不能取消")

        updated = replace(
            current.apply_action(action_name, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._batches.save(scope, updated, expected_version=expected_version)
        if action_name == "cancel":
            self._revoke_assignments(
                scope,
                assignments,
                reason=reason or "批次取消",
                request_id=request_id,
                now=now,
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.batch_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={"task_id": str(current.task_id)},
            event_type="annotation.batch.status_changed",
            event_payload={
                "batch_id": str(saved.batch_id),
                "task_id": str(current.task_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.batch_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _check_task_compatible(
        self,
        batch: AnnotationBatch,
        action: str,
        task_status: AnnotationTaskStatus,
    ) -> None:
        allowed: set[AnnotationTaskStatus] | None = None
        if action == "ready":
            allowed = {
                AnnotationTaskStatus.READY,
                AnnotationTaskStatus.ASSIGNED,
                AnnotationTaskStatus.IN_PROGRESS,
            }
        elif action == "assign":
            allowed = {
                AnnotationTaskStatus.READY,
                AnnotationTaskStatus.ASSIGNED,
                AnnotationTaskStatus.IN_PROGRESS,
            }
        elif action == "start":
            allowed = {AnnotationTaskStatus.IN_PROGRESS}
        elif action in {"pause", "resume", "block"}:
            allowed = {AnnotationTaskStatus.IN_PROGRESS}
        elif action == "unblock":
            allowed = {AnnotationTaskStatus.IN_PROGRESS, AnnotationTaskStatus.BLOCKED}
        elif action == "submit":
            allowed = {
                AnnotationTaskStatus.IN_PROGRESS,
                AnnotationTaskStatus.PAUSED,
                AnnotationTaskStatus.BLOCKED,
                AnnotationTaskStatus.SUBMITTED,
                AnnotationTaskStatus.REVIEWING,
                AnnotationTaskStatus.ACCEPTED,
            }
        elif action == "system-review":
            allowed = {
                AnnotationTaskStatus.SUBMITTED,
                AnnotationTaskStatus.REVIEWING,
            }
        elif action == "accept":
            allowed = {
                AnnotationTaskStatus.REVIEWING,
                AnnotationTaskStatus.ACCEPTED,
                AnnotationTaskStatus.COMPLETED,
            }
        elif action == "reject":
            allowed = {
                AnnotationTaskStatus.REVIEWING,
                AnnotationTaskStatus.REJECTED,
                AnnotationTaskStatus.REWORK,
            }
        elif action == "escalate":
            allowed = {
                AnnotationTaskStatus.REVIEWING,
                AnnotationTaskStatus.ARBITRATION,
            }
        elif action in {"resolve-accepted", "resolve-rejected"}:
            allowed = {
                AnnotationTaskStatus.REVIEWING,
                AnnotationTaskStatus.ARBITRATION,
            }
        elif action == "rework":
            allowed = {
                AnnotationTaskStatus.REJECTED,
                AnnotationTaskStatus.REWORK,
            }
        elif action == "resume-work":
            allowed = {
                AnnotationTaskStatus.REWORK,
                AnnotationTaskStatus.IN_PROGRESS,
            }
        if allowed is not None and task_status not in allowed:
            raise BatchNotCompatibleError(
                f"批次动作 {action} 与任务状态 {task_status.value} 不兼容"
            )

    def _revoke_assignments(
        self,
        scope: ProjectScope,
        assignments,
        *,
        reason: str,
        request_id: str | None,
        now: datetime,
    ) -> None:
        for assignment in assignments:
            if assignment.status not in {
                AnnotationAssignmentStatus.ASSIGNED,
                AnnotationAssignmentStatus.CLAIMED,
                AnnotationAssignmentStatus.IN_PROGRESS,
            }:
                continue
            updated = replace(
                assignment.apply_action("revoke", reason=reason, now=now),
                updated_by=scope.actor_id,
            )
            saved = self._assignments.save(
                scope,
                updated,
                expected_version=assignment.version,
            )
            self._assignments.add_event(
                scope,
                event_id=uuid4(),
                assignment=saved,
                action="revoke",
                from_status=assignment.status.value,
                actor_id=scope.actor_id,
                reason=reason,
                minutes=0,
                occurred_at=now,
            )

    def _get_task(self, scope: ProjectScope, task_id: UUID):
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        return task

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> AnnotationBatch:
        if not response_ref:
            raise ResourceNotFoundError("标注批次", response_ref)
        try:
            batch_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("标注批次", response_ref) from exc
        return self.get(scope, batch_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)
