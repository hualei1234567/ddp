"""标注提交用例：版本化草稿、完整性校验、提交冻结与撤回。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationSubmission
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationArbitrationRepository,
    AnnotationAssignmentRepository,
    AnnotationBatchRepository,
    AnnotationRecordRepository,
    AnnotationReviewRepository,
    AnnotationSampleSetRepository,
    AnnotationSchemaRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    AuditRepository,
    EpisodeDirectory,
    IdempotencyRepository,
    MemberDirectory,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import (
    AnnotationAssignmentAction,
    AnnotationAssignmentStatus,
    AnnotationBatchAction,
    AnnotationBatchStatus,
    AnnotationRecordStatus,
    AnnotationSchemaStatus,
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
    AnnotationTaskAction,
    AnnotationTaskStatus,
)
from app.domain.annotation.errors import (
    AssignmentNotOwnedError,
    DomainValidationError,
    ForbiddenOperationError,
    ResourceNotFoundError,
    ReworkLimitExceededError,
    SubmissionNotEditableError,
    SubmissionSchemaMismatchError,
    VersionConflictError,
)
from app.domain.annotation.schema import AnnotationSchema, compute_schema_sha256
from app.domain.annotation.state_machines import (
    allowed_submission_actions,
    assert_submission_action,
    submission_transition_pairs,
    validate_action_name,
)
from app.domain.annotation.submission import (
    AnnotationRecord,
    AnnotationSubmission,
    compute_records_digest,
    validate_record_labels,
)
from app.domain.identity.models import ProjectRole, ProjectScope

_RESOURCE_TYPE = "ANNOTATION_SUBMISSION"
_RECORD_TYPE = "ANNOTATION_RECORD"
_CREATABLE_TASK_STATUSES = {
    AnnotationTaskStatus.IN_PROGRESS,
    AnnotationTaskStatus.REWORK,
}
_CREATABLE_BATCH_STATUSES = {
    AnnotationBatchStatus.IN_PROGRESS,
    AnnotationBatchStatus.REWORK,
}
# 只有状态机允许 SYSTEM_SUPERSEDE 的状态才能被新版本替代。
_SUPERSEDABLE = frozenset(
    AnnotationSubmissionStatus(current)
    for current, target in submission_transition_pairs()
    if target == AnnotationSubmissionStatus.SUPERSEDED.value
)
# 任务未显式配置 ``quality_policy.max_rework_rounds`` 时的返工轮次上限。
DEFAULT_MAX_REWORK_ROUNDS = 3


class AnnotationSubmissionUseCases:
    def __init__(
        self,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        assignments: AnnotationAssignmentRepository,
        batches: AnnotationBatchRepository,
        tasks: AnnotationTaskRepository,
        reviews: AnnotationReviewRepository,
        arbitrations: AnnotationArbitrationRepository,
        members: MemberDirectory,
        sample_sets: AnnotationSampleSetRepository,
        schemas: AnnotationSchemaRepository,
        episodes: EpisodeDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._submissions = submissions
        self._records = records
        self._assignments = assignments
        self._batches = batches
        self._tasks = tasks
        self._reviews = reviews
        self._arbitrations = arbitrations
        self._members = members
        self._sample_sets = sample_sets
        self._schemas = schemas
        self._episodes = episodes
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, submission_id: UUID) -> AnnotationSubmission:
        submission = self._submissions.get(scope, submission_id)
        if submission is None:
            raise ResourceNotFoundError("标注提交", submission_id)
        records = self._records.list_for_submission(scope, submission_id)
        if records:
            return replace(submission, records=tuple(records))
        return submission

    def allowed_actions(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> frozenset[str]:
        return allowed_submission_actions(self.get(scope, submission_id).status)

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[AnnotationSubmission]:
        return self._submissions.list_for_task(
            scope,
            task_id=task_id,
            statuses=statuses,
            limit=limit,
        )

    def create_draft(
        self,
        scope: ProjectScope,
        command: NewAnnotationSubmission,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSubmission:
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
            return self.get(scope, _as_uuid(replay.response_ref, "标注提交"))

        assignment = self._get_assignment(scope, command.assignment_id)
        if assignment.assignee_id != scope.actor_id:
            raise AssignmentNotOwnedError("只能为自己的分配创建提交")
        if assignment.status is not AnnotationAssignmentStatus.IN_PROGRESS:
            raise SubmissionNotEditableError("分配未开始，不能创建提交")
        batch = self._get_batch(scope, assignment.batch_id)
        task = self._get_task(scope, assignment.task_id)
        if task.status not in _CREATABLE_TASK_STATUSES:
            raise SubmissionNotEditableError("任务状态不允许创建提交")
        if batch.status not in _CREATABLE_BATCH_STATUSES:
            raise SubmissionNotEditableError("批次状态不允许创建提交")
        schema = self._get_schema(scope, task.schema_id)
        self._assert_schema_snapshot(
            schema, task.schema_id, task.schema_version, task.schema_sha256
        )
        latest = self._submissions.latest_for_assignment(
            scope,
            assignment_id=assignment.assignment_id,
        )
        if latest is not None and latest.status not in _SUPERSEDABLE:
            raise SubmissionNotEditableError("存在未终结的提交版本，不能创建新版本")
        version = self._submissions.next_version(scope, assignment.assignment_id)
        submission_id = uuid4()
        records = self._build_records(
            scope,
            submission_id=submission_id,
            task=task,
            batch=batch,
            schema=schema,
            payload=command.records,
            now=now,
        )
        submission = AnnotationSubmission(
            submission_id=submission_id,
            project_id=scope.project_id,
            task_id=task.task_id,
            batch_id=batch.batch_id,
            assignment_id=assignment.assignment_id,
            annotator_id=assignment.assignee_id,
            submission_version=version,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            schema_sha256=schema.schema_sha256 or "",
            records_digest=compute_records_digest(records),
            item_count=len(records),
            data_source=command.data_source,
            records=records,
            supersedes_id=latest.submission_id if latest is not None else None,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        if latest is not None:
            superseded = replace(
                latest.apply_action(
                    AnnotationSubmissionAction.SYSTEM_SUPERSEDE.value,
                    reason="新提交版本替代旧版本",
                    now=now,
                ),
                updated_by=scope.actor_id,
            )
            self._submissions.save(
                scope,
                superseded,
                expected_version=latest.version,
            )
        created = self._submissions.add(scope, submission)
        self._records.add_many(scope, records)
        self._record(
            scope,
            created,
            "create-draft",
            None,
            created.status.value,
            None,
            request_id,
            now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.submission_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSubmission:
        key = validate_key(idempotency_key)
        body = {"action": action, "expected_version": expected_version, "reason": reason}
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
            return self.get(scope, _as_uuid(replay.response_ref, "标注提交"))
        action_name = validate_action_name(action, family="submission")
        current = self.get(scope, submission_id)
        if current.version != expected_version:
            raise VersionConflictError("标注提交")
        assert_submission_action(current.status, action_name)
        if (
            action_name
            in {
                AnnotationSubmissionAction.SUBMIT.value,
                AnnotationSubmissionAction.WITHDRAW.value,
            }
            and current.annotator_id != scope.actor_id
        ):
            raise AssignmentNotOwnedError("只能操作自己的提交")
        if action_name == AnnotationSubmissionAction.SUBMIT.value:
            self._validate_submission_snapshot(scope, current)
        if action_name == AnnotationSubmissionAction.REQUIRE_REWORK.value:
            self._assert_can_require_rework(scope, current)
            self._assert_rework_round_available(scope, current)
        updated = replace(
            current.apply_action(action_name, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._submissions.save(scope, updated, expected_version=expected_version)
        if action_name == AnnotationSubmissionAction.SUBMIT.value:
            self._mark_records(scope, current, AnnotationRecordStatus.SUBMITTED, now=now)
            self._submit_assignment(scope, current, now=now)
        elif action_name == AnnotationSubmissionAction.REQUIRE_REWORK.value:
            self._mark_records(
                scope,
                current,
                AnnotationRecordStatus.REJECTED,
                reason=reason,
                now=now,
            )
            self._return_task_and_batch_for_rework(scope, current, reason=reason, now=now)
            self._return_assignment_for_rework(scope, current, reason=reason, now=now)
            self._bump_rework_count(scope, current, now=now)
        self._record(
            scope,
            saved,
            action_name,
            current.status.value,
            saved.status.value,
            reason,
            request_id,
            now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.submission_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def require_rework(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        *,
        expected_version: int,
        reason: str,
        request_id: str | None = None,
    ) -> AnnotationSubmission:
        return self.apply_action(
            scope,
            submission_id,
            AnnotationSubmissionAction.REQUIRE_REWORK.value,
            expected_version=expected_version,
            reason=reason,
            path=f"/annotation-submissions/{submission_id}/actions/require-rework",
            request_id=request_id,
        )

    def _assert_can_require_rework(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> None:
        """返工要求只能由项目负责人、驳回的复核人或仲裁人发起。"""
        if submission.annotator_id == scope.actor_id:
            raise ForbiddenOperationError("标注人不得自行发起返工要求")
        if self._members.user_role(scope, scope.actor_id) == ProjectRole.OWNER.value:
            return
        reviews = self._reviews.list_for_submission(scope, submission.submission_id)
        if any(
            review.reviewer_id == scope.actor_id
            and getattr(review.decision, "value", review.decision) == "REJECT"
            for review in reviews
        ):
            return
        arbitrations = self._arbitrations.list_for_submission(scope, submission.submission_id)
        if any(scope.actor_id in arbitration.arbiter_ids for arbitration in arbitrations):
            return
        raise ForbiddenOperationError("只有复核人、仲裁人或项目负责人可以发起返工")

    def _assert_rework_round_available(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> None:
        task = self._get_task(scope, submission.task_id)
        current_version = submission.submission_version
        if current_version - 1 >= _rework_limit(task.quality_policy):
            raise ReworkLimitExceededError("返工次数已达上限，请升级项目负责人处理")

    def _return_task_and_batch_for_rework(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        reason: str | None,
        now: datetime,
    ) -> None:
        """任务/批次若已进入反馈审，先退回 REWORK，保证分配可回到 IN_PROGRESS。"""
        task = self._get_task(scope, submission.task_id)
        task = self._transition_task_for_rework(scope, task, reason=reason, now=now)
        batch = self._get_batch(scope, submission.batch_id)
        self._transition_batch_for_rework(scope, batch, reason=reason, now=now)
        del task

    def _transition_task_for_rework(self, scope, task, *, reason, now):
        status = task.status
        if status in {AnnotationTaskStatus.IN_PROGRESS, AnnotationTaskStatus.REWORK}:
            return task
        steps: list[str] = []
        if status is AnnotationTaskStatus.SUBMITTED:
            steps.append(AnnotationTaskAction.SYSTEM_REVIEW.value)
            status = AnnotationTaskStatus.REVIEWING
        if status is AnnotationTaskStatus.REVIEWING:
            steps.extend(
                [
                    AnnotationTaskAction.REJECT.value,
                    AnnotationTaskAction.REWORK.value,
                ]
            )
        elif status is AnnotationTaskStatus.PAUSED:
            steps.append(AnnotationTaskAction.RESUME.value)
        elif status is AnnotationTaskStatus.BLOCKED:
            steps.append(AnnotationTaskAction.UNBLOCK.value)
        elif status is not AnnotationTaskStatus.REWORK:
            raise SubmissionNotEditableError("任务状态不允许返工")
        for step in steps:
            updated = replace(
                task.apply_action(step, reason=reason, now=now),
                updated_by=scope.actor_id,
            )
            task = self._tasks.save(scope, updated, expected_version=task.version)
        return task

    def _transition_batch_for_rework(self, scope, batch, *, reason, now):
        status = batch.status
        if status in {AnnotationBatchStatus.IN_PROGRESS, AnnotationBatchStatus.REWORK}:
            return batch
        steps: list[str] = []
        if status is AnnotationBatchStatus.SUBMITTED:
            steps.append(AnnotationBatchAction.SYSTEM_REVIEW.value)
            status = AnnotationBatchStatus.REVIEWING
        if status is AnnotationBatchStatus.REVIEWING:
            steps.extend(
                [
                    AnnotationBatchAction.REJECT.value,
                    AnnotationBatchAction.REWORK.value,
                ]
            )
        elif status is AnnotationBatchStatus.PAUSED:
            steps.append(AnnotationBatchAction.RESUME.value)
        elif status is AnnotationBatchStatus.BLOCKED:
            steps.append(AnnotationBatchAction.UNBLOCK.value)
        elif status is not AnnotationBatchStatus.REWORK:
            raise SubmissionNotEditableError("批次状态不允许返工")
        for step in steps:
            updated = replace(
                batch.apply_action(step, reason=reason, now=now),
                updated_by=scope.actor_id,
            )
            batch = self._batches.save(scope, updated, expected_version=batch.version)
        return batch

    def _return_assignment_for_rework(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        reason: str | None,
        now: datetime,
    ) -> None:
        """返工要求生效后，分配回到 IN_PROGRESS，允许标注人提交新版本。"""
        assignment = self._get_assignment(scope, submission.assignment_id)
        if assignment.status is not AnnotationAssignmentStatus.SUBMITTED:
            return
        previous_status = assignment.status
        updated = replace(
            assignment.apply_action(
                AnnotationAssignmentAction.REWORK.value,
                reason=reason,
                now=now,
            ),
            updated_by=scope.actor_id,
        )
        updated = self._assignments.save(scope, updated, expected_version=assignment.version)
        self._assignments.add_event(
            scope,
            event_id=uuid4(),
            assignment=updated,
            action=AnnotationAssignmentAction.REWORK.value,
            from_status=previous_status.value,
            actor_id=scope.actor_id,
            reason=reason,
            minutes=0,
            occurred_at=now,
        )

    def _bump_rework_count(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        now: datetime,
    ) -> None:
        batch = self._get_batch(scope, submission.batch_id)
        updated = replace(
            batch,
            rework_count=batch.rework_count + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )
        self._batches.save(scope, updated, expected_version=batch.version)

    def _validate_submission_snapshot(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> None:
        schema = self._get_schema(scope, submission.schema_id)
        task = self._get_task(scope, submission.task_id)
        self._assert_schema_snapshot(
            schema,
            task.schema_id,
            task.schema_version,
            task.schema_sha256,
        )
        if (
            submission.schema_id != task.schema_id
            or submission.schema_version != schema.schema_version
            or submission.schema_sha256 != schema.schema_sha256
        ):
            raise SubmissionSchemaMismatchError("提交 Schema 快照与任务冻结快照不一致")
        records = self._records.list_for_submission(scope, submission.submission_id)
        if not records:
            raise DomainValidationError("提交没有任何标注记录")
        if compute_records_digest(tuple(records)) != submission.records_digest:
            raise DomainValidationError("提交 records_digest 校验失败")
        definitions = tuple(schema.labels)
        for record in records:
            validate_record_labels(record, definitions)

    def _assert_schema_snapshot(
        self,
        schema: AnnotationSchema,
        schema_id: UUID,
        schema_version: int,
        schema_sha256: str,
    ) -> None:
        if schema.status is not AnnotationSchemaStatus.PUBLISHED:
            raise SubmissionSchemaMismatchError("Schema 已不可用于提交")
        if (
            schema.schema_id != schema_id
            or schema.schema_version != schema_version
            or compute_schema_sha256(schema) != schema_sha256
        ):
            raise SubmissionSchemaMismatchError("Schema 快照 Hash 不可复算或已漂移")

    def _submit_assignment(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        now: datetime,
    ) -> None:
        assignment = self._get_assignment(scope, submission.assignment_id)
        if assignment.status is not AnnotationAssignmentStatus.IN_PROGRESS:
            raise SubmissionNotEditableError("分配不在进行中，不能提交")
        updated = replace(
            assignment.apply_action(
                AnnotationAssignmentAction.SUBMIT.value,
                submitted_item_count=submission.item_count,
                now=now,
            ),
            updated_by=scope.actor_id,
        )
        self._assignments.save(scope, updated, expected_version=assignment.version)

    def _mark_records(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        target: AnnotationRecordStatus,
        *,
        reason: str | None = None,
        now: datetime,
    ) -> None:
        action = {
            AnnotationRecordStatus.SUBMITTED: "submit-with-submission",
            AnnotationRecordStatus.APPROVED: "approve-with-submission",
            AnnotationRecordStatus.REJECTED: "reject-with-submission",
        }[target]
        records = self._records.list_for_submission(scope, submission.submission_id)
        for record in records:
            if target is AnnotationRecordStatus.SUBMITTED and (
                record.status is not AnnotationRecordStatus.DRAFT
            ):
                continue
            if (
                target
                in {
                    AnnotationRecordStatus.APPROVED,
                    AnnotationRecordStatus.REJECTED,
                }
                and record.status is not AnnotationRecordStatus.SUBMITTED
            ):
                continue
            updated = replace(
                record.apply_action(
                    action,
                    actor_id=scope.actor_id,
                    reason=reason,
                    now=now,
                ),
                updated_by=scope.actor_id,
            )
            self._records.save(scope, updated, expected_version=record.version)

    def _build_records(
        self,
        scope: ProjectScope,
        *,
        submission_id: UUID,
        task,
        batch,
        schema: AnnotationSchema,
        payload: tuple[Mapping[str, object], ...],
        now: datetime,
    ) -> tuple[AnnotationRecord, ...]:
        if not payload:
            raise DomainValidationError("提交至少需要一条标注记录")
        episode_ids = tuple(_as_uuid(item.get("episode_id"), "episode_id") for item in payload)
        bounds = self._episodes.episode_bounds(scope, episode_ids=episode_ids)
        allowed_items = set(batch.item_refs)
        records: list[AnnotationRecord] = []
        definitions = tuple(schema.labels)
        for item, episode_id in zip(payload, episode_ids, strict=True):
            if allowed_items and episode_id not in allowed_items:
                raise DomainValidationError("标注 Episode 不属于当前批次")
            if bounds and episode_id not in bounds:
                raise ResourceNotFoundError("Episode", episode_id)
            record = AnnotationRecord(
                annotation_id=uuid4(),
                annotation_lineage_id=_as_uuid(
                    item.get("annotation_lineage_id") or uuid4(),
                    "annotation_lineage_id",
                ),
                annotation_version=item.get("annotation_version", 1),
                project_id=scope.project_id,
                submission_id=submission_id,
                episode_id=episode_id,
                episode_version=item.get("episode_version", 1),
                task_id=task.task_id,
                schema_id=schema.schema_id,
                schema_version=schema.schema_version,
                status=AnnotationRecordStatus.DRAFT,
                time_range=item.get("time_range"),
                labels=tuple(item.get("labels") or ()),
                evidence_refs=tuple(item.get("evidence_refs") or ()),
                supersedes_id=item.get("supersedes_id"),
                created_at=now,
                updated_at=now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
            self._validate_record_bounds(record, bounds.get(episode_id))
            validate_record_labels(record, definitions)
            records.append(record)
        return tuple(records)

    def _validate_record_bounds(
        self,
        record: AnnotationRecord,
        bounds: tuple[int, int] | None,
    ) -> None:
        if bounds is None or record.time_range is None:
            return
        start = int(record.time_range["start_time_ns"])
        end = int(record.time_range["end_time_ns"])
        if start < bounds[0] or end > bounds[1]:
            raise DomainValidationError("标注时间片段超出 Episode 范围")

    def _get_assignment(self, scope: ProjectScope, assignment_id: UUID):
        assignment = self._assignments.get(scope, assignment_id)
        if assignment is None:
            raise ResourceNotFoundError("标注分配", assignment_id)
        return assignment

    def _get_batch(self, scope: ProjectScope, batch_id: UUID):
        batch = self._batches.get(scope, batch_id)
        if batch is None:
            raise ResourceNotFoundError("标注批次", batch_id)
        return batch

    def _get_task(self, scope: ProjectScope, task_id: UUID):
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        return task

    def _get_schema(self, scope: ProjectScope, schema_id: UUID):
        schema = self._schemas.get(scope, schema_id)
        if schema is None:
            raise ResourceNotFoundError("标注 Schema", schema_id)
        return schema

    def _record(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        action: str,
        from_status: str | None,
        to_status: str,
        reason: str | None,
        request_id: str | None,
        now: datetime,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=submission.submission_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            details={
                "task_id": str(submission.task_id),
                "assignment_id": str(submission.assignment_id),
                "submission_version": submission.submission_version,
                "records_digest": submission.records_digest,
            },
            event_type="annotation.submission.status_changed",
            event_payload={
                "submission_id": str(submission.submission_id),
                "task_id": str(submission.task_id),
                "from_status": from_status,
                "to_status": to_status,
                "action": action,
                "submission_version": submission.submission_version,
                "version": submission.version,
            },
            occurred_at=now,
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_uuid(value: object, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 不是合法 UUID") from exc
    raise DomainValidationError(f"{field_name} 必须是 UUID")


def _rework_limit(policy: Mapping[str, object]) -> int:
    """从任务质量策略读取返工上限；非法或缺省时回退默认值。"""
    value = policy.get("max_rework_rounds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_MAX_REWORK_ROUNDS


__all__ = ["AnnotationSubmissionUseCases"]
