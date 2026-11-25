"""标注任务用例：创建、审批、就绪、分配、执行、验收、返工、取消。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationTask
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationAssignmentRepository,
    AnnotationBatchRepository,
    AnnotationSampleSetRepository,
    AnnotationSchemaRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import (
    AnnotationAssignmentStatus,
    AnnotationBatchStatus,
    AnnotationSampleSetAction,
    AnnotationSampleSetStatus,
    AnnotationSchemaStatus,
    AnnotationSubmissionStatus,
    AnnotationTaskAction,
    AnnotationTaskStatus,
)
from app.domain.annotation.errors import (
    BatchNotCompatibleError,
    ConflictError,
    DomainValidationError,
    ForbiddenOperationError,
    ResourceImmutableError,
    ResourceNotFoundError,
    ReviewRequiredError,
    SampleSetNotSnapshottedError,
    SchemaNotPublishedError,
    SchemaSnapshotMismatchError,
    TaskOpenWorkError,
    VersionConflictError,
)
from app.domain.annotation.state_machines import (
    allowed_task_actions,
    assert_task_action,
    validate_action_name,
)
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "ANNOTATION_TASK"
# 尚未作出结论、会拦住任务验收的提交状态：提交已完成但复核/仲裁没走完。
_UNDECIDED_SUBMISSION = frozenset(
    {
        AnnotationSubmissionStatus.SUBMITTED.value,
        AnnotationSubmissionStatus.IN_REVIEW.value,
        AnnotationSubmissionStatus.ESCALATED.value,
        AnnotationSubmissionStatus.ARBITRATING.value,
    }
)
_ACTIVE_BATCH_OK = {
    AnnotationBatchStatus.SUBMITTED.value,
    AnnotationBatchStatus.REVIEWING.value,
    AnnotationBatchStatus.ACCEPTED.value,
}
_PATCHABLE = frozenset(
    {
        "description",
        "target_item_count",
        "target_episode_count",
        "due_at",
        "priority",
        "assignment_mode",
        "iaa_policy",
        "quality_policy",
        "required_skills",
    }
)


class AnnotationTaskUseCases:
    def __init__(
        self,
        tasks: AnnotationTaskRepository,
        sample_sets: AnnotationSampleSetRepository,
        schemas: AnnotationSchemaRepository,
        batches: AnnotationBatchRepository,
        assignments: AnnotationAssignmentRepository,
        submissions: AnnotationSubmissionRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._tasks = tasks
        self._sample_sets = sample_sets
        self._schemas = schemas
        self._batches = batches
        self._assignments = assignments
        self._submissions = submissions
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, task_id: UUID) -> AnnotationTask:
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        return task

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        overdue: bool | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationTask]:
        if status is not None:
            try:
                AnnotationTaskStatus(status)
            except ValueError as exc:
                raise DomainValidationError("status 取值非法") from exc
        if overdue is not None and not isinstance(overdue, bool):
            raise DomainValidationError("overdue 必须是布尔值")
        return self._tasks.list(
            scope,
            status=status,
            overdue=overdue,
            now=_utcnow() if overdue is not None else None,
            after_id=after_id,
            limit=limit,
        )

    def allowed_actions(self, scope: ProjectScope, task_id: UUID) -> frozenset[str]:
        return allowed_task_actions(self.get(scope, task_id).status)

    def create(
        self,
        scope: ProjectScope,
        command: NewAnnotationTask,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationTask:
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

        sample_set = self._get_sample_set(scope, command.sample_set_id)
        sample_set.assert_usable_for_task()
        if sample_set.status is not AnnotationSampleSetStatus.SNAPSHOTTED:
            raise SampleSetNotSnapshottedError("任务必须引用已快照且未消费的样本集")
        schema = self._get_schema(scope, command.schema_id)
        if schema.status is not AnnotationSchemaStatus.PUBLISHED:
            raise SchemaNotPublishedError("任务只能引用已发布 Schema")
        if command.schema_version is not None and command.schema_version != schema.schema_version:
            raise SchemaSnapshotMismatchError("指定 Schema 版本与已发布版本不一致")
        if command.target_episode_count > sample_set.selected_count:
            raise DomainValidationError("目标 Episode 数不得超过样本集入选数")
        if command.target_item_count < command.target_episode_count:
            raise DomainValidationError("目标标注项数不得小于目标 Episode 数")

        task = AnnotationTask(
            task_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            description=command.description,
            sample_set_id=sample_set.sample_set_id,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            schema_sha256=schema.schema_sha256 or "",
            target_item_count=command.target_item_count,
            target_episode_count=command.target_episode_count,
            priority=command.priority,
            assignment_mode=command.assignment_mode,
            due_at=command.due_at,
            iaa_policy=command.iaa_policy,
            quality_policy=command.quality_policy,
            required_skills=command.required_skills,
            progress={"total_items": command.target_item_count},
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._tasks.add(scope, task)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.task_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "sample_set_id": str(created.sample_set_id),
                "schema_id": str(created.schema_id),
                "schema_sha256": created.schema_sha256,
            },
            event_type="annotation.task.created",
            event_payload={
                "task_id": str(created.task_id),
                "sample_set_id": str(created.sample_set_id),
                "schema_id": str(created.schema_id),
                "schema_version": created.schema_version,
                "schema_sha256": created.schema_sha256,
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
            response_ref=str(created.task_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> AnnotationTask:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        unknown = set(changes) - _PATCHABLE
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        current = self.get(scope, task_id)
        if current.version != expected_version:
            raise VersionConflictError("标注任务")
        if current.status is not AnnotationTaskStatus.DRAFT:
            raise ResourceImmutableError("仅 DRAFT 任务允许修改核心字段")
        sample_set = self._get_sample_set(scope, current.sample_set_id)
        target_episode_count = int(
            changes.get("target_episode_count", current.target_episode_count)
        )
        target_item_count = int(changes.get("target_item_count", current.target_item_count))
        if target_episode_count > sample_set.selected_count:
            raise DomainValidationError("目标 Episode 数不得超过样本集入选数")
        if target_item_count < target_episode_count:
            raise DomainValidationError("目标标注项数不得小于目标 Episode 数")
        now = _utcnow()
        updated = replace(
            current,
            **dict(changes),
            updated_at=now,
            updated_by=scope.actor_id,
        )
        saved = self._tasks.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.task_id,
            action="update",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes)},
            event_type="annotation.task.updated",
            event_payload={
                "task_id": str(saved.task_id),
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
        task_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationTask:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            **dict(payload or {}),
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

        unknown = set(payload or {}) - {"reason", "waiver_reason"}
        if unknown:
            raise DomainValidationError("请求包含不允许的动作字段")
        action_name = validate_action_name(action, family="task")
        current = self.get(scope, task_id)
        if current.version != expected_version:
            raise VersionConflictError("标注任务")
        assert_task_action(current.status, action_name)
        reason = _reason(payload)
        self._check_preconditions(scope, current, action_name, payload)

        updated = replace(
            current.apply_action(action_name, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._tasks.save(scope, updated, expected_version=expected_version)

        if action_name == AnnotationTaskAction.APPROVE.value:
            self._consume_sample_set(scope, saved, request_id=request_id, now=now)
        cascade = 0
        if action_name == AnnotationTaskAction.CANCEL.value:
            cascade = self._cascade_cancel(
                scope,
                saved,
                reason=reason,
                request_id=request_id,
                now=now,
            )

        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.task_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={"cascade_count": cascade},
            event_type="annotation.task.status_changed",
            event_payload={
                "task_id": str(saved.task_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
                "cascade_count": cascade,
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
            response_ref=str(saved.task_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _check_preconditions(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        action: str,
        payload: Mapping[str, object] | None,
    ) -> None:
        sample_set = self._get_sample_set(scope, task.sample_set_id)
        schema = self._get_schema(scope, task.schema_id)
        if schema.status is not AnnotationSchemaStatus.PUBLISHED and action in {
            AnnotationTaskAction.SUBMIT_REVIEW.value,
            AnnotationTaskAction.APPROVE.value,
            AnnotationTaskAction.READY.value,
        }:
            raise SchemaNotPublishedError("Schema 已不可用于新任务")
        # 契约把"样本集已取消"与"样本集未快照"拆成两个码；判定只在聚合根里一处，
        # 这里只决定哪些动作需要做这项前置校验。
        if action in {
            AnnotationTaskAction.SUBMIT_REVIEW.value,
            AnnotationTaskAction.APPROVE.value,
            AnnotationTaskAction.READY.value,
        }:
            sample_set.assert_usable_for_task()
        if (
            action
            in {
                AnnotationTaskAction.SUBMIT_REVIEW.value,
                AnnotationTaskAction.APPROVE.value,
            }
            and sample_set.status is not AnnotationSampleSetStatus.SNAPSHOTTED
        ):
            raise SampleSetNotSnapshottedError("任务引用的样本集快照已不可用")
        if (
            schema.schema_version != task.schema_version
            or schema.schema_sha256 != task.schema_sha256
        ):
            raise SchemaSnapshotMismatchError("任务冻结的 Schema 快照与当前版本不一致")
        if action == AnnotationTaskAction.APPROVE.value and task.created_by == scope.actor_id:
            raise ForbiddenOperationError("任务创建人不得审批自己的任务")
        if action == AnnotationTaskAction.ASSIGN.value:
            assignments = self._assignments.list_active_for_task(scope, task_id=task.task_id)
            if not assignments:
                raise ConflictError("任务分配动作要求至少一个有效分配")
        if action == AnnotationTaskAction.START.value:
            assignments = self._assignments.list_active_for_task(scope, task_id=task.task_id)
            if not any(
                item.status
                in {
                    AnnotationAssignmentStatus.CLAIMED,
                    AnnotationAssignmentStatus.IN_PROGRESS,
                }
                for item in assignments
            ):
                raise ConflictError("任务开始前至少需要一个已领取的分配")
        if action == AnnotationTaskAction.SUBMIT.value:
            batches = self._batches.list_non_terminal(scope, task_id=task.task_id)
            if not batches:
                raise BatchNotCompatibleError("任务提交前必须存在有效批次")
            if any(batch.status.value not in _ACTIVE_BATCH_OK for batch in batches):
                raise BatchNotCompatibleError("存在未提交批次，任务不能提交")
        if action in {
            AnnotationTaskAction.ACCEPT.value,
            AnnotationTaskAction.RESOLVE_ACCEPTED.value,
            AnnotationTaskAction.COMPLETE.value,
        }:
            self._assert_reviews_finished(scope, task)
        if action in {
            AnnotationTaskAction.ACCEPT.value,
            AnnotationTaskAction.RESOLVE_ACCEPTED.value,
        }:
            assignments = self._assignments.list_active_for_task(scope, task_id=task.task_id)
            if any(
                item.status
                not in {
                    AnnotationAssignmentStatus.SUBMITTED,
                    AnnotationAssignmentStatus.COMPLETED,
                }
                for item in assignments
            ):
                raise TaskOpenWorkError("存在未复核完成的分配")
        if action == AnnotationTaskAction.COMPLETE.value:
            open_batches = self._batches.list_non_terminal(scope, task_id=task.task_id)
            if open_batches:
                raise TaskOpenWorkError("存在未验收或未取消的批次")
            approved = int(task.progress.get("approved_items", 0))
            waiver = _waiver(payload)
            if approved < task.target_item_count and not waiver:
                raise DomainValidationError("验收数量未达标且未提供豁免原因")

    def _assert_reviews_finished(self, scope: ProjectScope, task: AnnotationTask) -> None:
        """验收/完成前，任务下每条已提交的提交都必须已作出结论。

        契约 ``annotation_review_required``（409："提交未完成复核，不能验收/完成"）。
        与 ``annotation_task_has_open_work`` 的区别：后者是"分配还没提交完"，
        前者是"提交已完成但结论没出"。两者都拦验收，但责任人不同：
        前者找标注员，后者找复核/仲裁人。

        判定用白名单 ``_UNDECIDED_SUBMISSION``：DRAFT/WITHDRAWN 不是"待复核"，
        已终结状态（APPROVED/REJECTED/RESOLVED_*/REWORK_REQUIRED/SUPERSEDED）
        也都放行，因此不需要再用黑名单兜一遍。
        """
        pending = [
            {
                "submission_id": str(submission.submission_id),
                "status": getattr(submission.status, "value", submission.status),
            }
            for submission in self._submissions.list_for_task(scope, task_id=task.task_id)
            if getattr(submission.status, "value", submission.status) in _UNDECIDED_SUBMISSION
        ]
        if pending:
            raise ReviewRequiredError(
                "存在未完成复核或仲裁的提交，任务不能验收",
                details={"task_id": str(task.task_id), "pending_submissions": pending},
            )

    def _consume_sample_set(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        *,
        request_id: str | None,
        now: datetime,
    ) -> None:
        sample_set = self._get_sample_set(scope, task.sample_set_id)
        if sample_set.status is not AnnotationSampleSetStatus.SNAPSHOTTED:
            return
        updated = replace(
            sample_set.apply_action(
                AnnotationSampleSetAction.CONSUME.value,
                now=now,
            ),
            updated_by=scope.actor_id,
        )
        saved = self._sample_sets.save(
            scope,
            updated,
            expected_version=sample_set.version,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="ANNOTATION_SAMPLE_SET",
            resource_id=saved.sample_set_id,
            action=AnnotationSampleSetAction.CONSUME.value,
            from_status=sample_set.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"task_id": str(task.task_id)},
            event_type="annotation.sample_set.status_changed",
            event_payload={
                "sample_set_id": str(saved.sample_set_id),
                "task_id": str(task.task_id),
                "from_status": sample_set.status.value,
                "to_status": saved.status.value,
            },
            occurred_at=now,
        )

    def _cascade_cancel(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        *,
        reason: str | None,
        request_id: str | None,
        now: datetime,
    ) -> int:
        assignments = self._assignments.list_active_for_task(scope, task_id=task.task_id)
        if any(item.status is AnnotationAssignmentStatus.SUBMITTED for item in assignments):
            raise TaskOpenWorkError("存在未结提交，任务不能取消")
        count = 0
        for assignment in assignments:
            if assignment.status in {
                AnnotationAssignmentStatus.ASSIGNED,
                AnnotationAssignmentStatus.CLAIMED,
                AnnotationAssignmentStatus.IN_PROGRESS,
            }:
                updated = replace(
                    assignment.apply_action(
                        "revoke",
                        reason=reason or "任务取消",
                        now=now,
                    ),
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
                count += 1
        for batch in self._batches.list_non_terminal(scope, task_id=task.task_id):
            updated = replace(
                batch.apply_action("cancel", reason=reason or "任务取消", now=now),
                updated_by=scope.actor_id,
            )
            self._batches.save(scope, updated, expected_version=batch.version)
            count += 1
        if count:
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_RESOURCE_TYPE,
                resource_id=task.task_id,
                action="cascade_cancel",
                from_status=task.status.value,
                to_status=task.status.value,
                reason=reason,
                request_id=request_id,
                details={"cascade_count": count},
                event_type="annotation.task.cascade_cancelled",
                event_payload={
                    "task_id": str(task.task_id),
                    "cascade_count": count,
                },
                occurred_at=now,
            )
        return count

    def _get_sample_set(self, scope: ProjectScope, sample_set_id: UUID):
        sample_set = self._sample_sets.get(scope, sample_set_id)
        if sample_set is None:
            raise ResourceNotFoundError("标注样本集", sample_set_id)
        return sample_set

    def _get_schema(self, scope: ProjectScope, schema_id: UUID):
        schema = self._schemas.get(scope, schema_id)
        if schema is None:
            raise ResourceNotFoundError("标注 Schema", schema_id)
        return schema

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> AnnotationTask:
        if not response_ref:
            raise ResourceNotFoundError("标注任务", response_ref)
        try:
            task_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("标注任务", response_ref) from exc
        return self.get(scope, task_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _reason(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("reason")
    return value if isinstance(value, str) else None


def _waiver(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("waiver_reason")
    return value if isinstance(value, str) and value.strip() else None
