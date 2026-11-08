"""质检抽检用例：策略化抽样、分配复核、通过/不通过/转仲裁/返工闭环。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewQualitySample
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationRecordRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    AuditRepository,
    IdempotencyRepository,
    MemberDirectory,
    OutboxRepository,
    QualitySampleRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import (
    QualitySampleAction,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    ForbiddenOperationError,
    QualitySampleNotReviewableError,
    QualitySamplePolicyMissingError,
    QualitySampleSelfReviewForbiddenError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.annotation.quality_sample import (
    QualityCandidate,
    QualitySample,
    select_quality_candidates,
)
from app.domain.annotation.state_machines import (
    allowed_quality_sample_actions,
    assert_quality_sample_action,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "QUALITY_SAMPLE"
_SAMPLEABLE_SUBMISSION_STATUSES = ("SUBMITTED", "IN_REVIEW", "APPROVED")


class QualitySampleUseCases:
    def __init__(
        self,
        samples: QualitySampleRepository,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        tasks: AnnotationTaskRepository,
        members: MemberDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._samples = samples
        self._submissions = submissions
        self._records = records
        self._tasks = tasks
        self._members = members
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, sample_id: UUID) -> QualitySample:
        sample = self._samples.get(scope, sample_id)
        if sample is None:
            raise ResourceNotFoundError("质检抽检", sample_id)
        return sample

    def allowed_actions(self, scope: ProjectScope, sample_id: UUID) -> frozenset[str]:
        return allowed_quality_sample_actions(self.get(scope, sample_id).status)

    def list_for_task(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[QualitySample]:
        return self._samples.list_for_task(scope, task_id=task_id, status=status, limit=limit)

    def plan(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        candidates: tuple[QualityCandidate, ...],
        sampling_policy_version: str,
        sample_type: str,
        sample_size: int,
        sample_reason: str,
        stratum: str | None = None,
        risk_threshold: float | None = None,
        due_at: datetime | None = None,
        request_id: str | None = None,
    ) -> list[QualitySample]:
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        if not sampling_policy_version.strip():
            raise QualitySamplePolicyMissingError("缺少有效抽样策略版本")
        selected = select_quality_candidates(
            candidates,
            sample_type=sample_type,
            sample_size=sample_size,
            stratum=stratum,
            risk_threshold=risk_threshold,
        )
        if any(item.annotator_id == scope.actor_id for item in selected):
            raise QualitySampleSelfReviewForbiddenError("标注员不得自行选择抽检样本")
        now = _utcnow()
        created: list[QualitySample] = []
        for candidate in selected:
            submission = self._submissions.get(scope, candidate.submission_id)
            if submission is None or submission.task_id != task_id:
                raise ResourceNotFoundError("标注提交", candidate.submission_id)
            if submission.status not in _SAMPLEABLE_SUBMISSION_STATUSES:
                raise QualitySampleNotReviewableError("提交状态不允许抽检")
            item_refs = tuple(
                record.annotation_id
                for record in self._records.list_for_submission(scope, submission.submission_id)
            )
            sample = QualitySample(
                quality_sample_id=uuid4(),
                project_id=scope.project_id,
                task_id=task_id,
                submission_id=submission.submission_id,
                annotator_id=submission.annotator_id,
                sampling_policy_version=sampling_policy_version,
                sample_type=sample_type,
                sample_reason=sample_reason,
                stratum=candidate.stratum,
                item_refs=item_refs,
                due_at=due_at,
                metadata={"risk_score": candidate.risk_score},
                created_at=now,
                updated_at=now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
            saved = self._samples.add(scope, sample)
            created.append(saved)
            self._record(
                scope,
                saved,
                "plan",
                None,
                saved.status.value,
                None,
                request_id,
                now,
            )
        self._uow.commit()
        return created

    def create_one(
        self,
        scope: ProjectScope,
        command: NewQualitySample,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str = "",
        request_id: str | None = None,
    ) -> QualitySample:
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
            return self.get(scope, _as_uuid(replay.response_ref, "质检抽检"))
        task = self._tasks.get(scope, command.task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", command.task_id)
        submission = self._submissions.get(scope, command.submission_id)
        if submission is None or submission.task_id != command.task_id:
            raise ResourceNotFoundError("标注提交", command.submission_id)
        if submission.annotator_id != command.annotator_id:
            raise DomainValidationError("抽检标注人与提交不一致")
        if command.annotator_id == scope.actor_id:
            raise QualitySampleSelfReviewForbiddenError("标注员不得自行选择抽检样本")
        sample = QualitySample(
            quality_sample_id=uuid4(),
            project_id=scope.project_id,
            task_id=command.task_id,
            submission_id=command.submission_id,
            annotator_id=command.annotator_id,
            sampling_policy_version=command.sampling_policy_version,
            sample_type=command.sample_type,
            sample_reason=command.sample_reason,
            item_refs=command.item_refs,
            stratum=command.stratum,
            due_at=command.due_at,
            metadata=command.metadata,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._samples.add(scope, sample)
        self._record(scope, created, "plan", None, created.status.value, None, request_id, now)
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.quality_sample_id),
            now=now,
        )
        self._uow.commit()
        return created

    def plan_for_submissions(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        submission_ids: tuple[UUID, ...],
        sampling_policy_version: str,
        sample_type: str,
        sample_size: int,
        sample_reason: str,
        stratum: str | None = None,
        risk_scores: Mapping[UUID, float] | None = None,
        risk_threshold: float | None = None,
        due_at: datetime | None = None,
        request_id: str | None = None,
    ) -> list[QualitySample]:
        """根据任务内真实提交构造抽检候选，再交给策略化抽样。"""
        scores = risk_scores or {}
        candidates: list[QualityCandidate] = []
        for submission_id in submission_ids:
            submission = self._submissions.get(scope, submission_id)
            if submission is None or submission.task_id != task_id:
                raise ResourceNotFoundError("标注提交", submission_id)
            candidates.append(
                QualityCandidate(
                    submission_id=submission.submission_id,
                    annotator_id=submission.annotator_id,
                    stratum=stratum,
                    risk_score=float(scores.get(submission_id, 0.0)),
                )
            )
        return self.plan(
            scope,
            task_id,
            candidates=tuple(candidates),
            sampling_policy_version=sampling_policy_version,
            sample_type=sample_type,
            sample_size=sample_size,
            sample_reason=sample_reason,
            stratum=stratum,
            risk_threshold=risk_threshold,
            due_at=due_at,
            request_id=request_id,
        )

    def apply_action(
        self,
        scope: ProjectScope,
        sample_id: UUID,
        action: str,
        *,
        expected_version: int,
        reviewer_id: UUID | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str = "",
        request_id: str | None = None,
    ) -> QualitySample:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reviewer_id": reviewer_id,
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
            return self.get(scope, _as_uuid(replay.response_ref, "质检抽检"))
        action_name = validate_action_name(action, family="quality_sample")
        current = self.get(scope, sample_id)
        if current.version != expected_version:
            raise VersionConflictError("质检抽检")
        assert_quality_sample_action(current.status, action_name)
        if action_name == QualitySampleAction.ASSIGN.value:
            chosen = reviewer_id
            if chosen is None:
                raise DomainValidationError("分配抽检必须指定复核人")
            if not self._members.user_is_member(scope, chosen):
                raise ResourceNotFoundError("项目成员", chosen)
            if chosen == current.annotator_id:
                raise QualitySampleSelfReviewForbiddenError("抽检复核人不得是标注人")
        if (
            action_name == QualitySampleAction.SAMPLE.value
            and current.annotator_id == scope.actor_id
        ):
            raise QualitySampleSelfReviewForbiddenError("标注员不得自行抽样")
        if action_name in {
            QualitySampleAction.START.value,
            QualitySampleAction.PASS.value,
            QualitySampleAction.FAIL.value,
            QualitySampleAction.ESCALATE.value,
        }:
            if current.reviewer_id is None or current.reviewer_id != scope.actor_id:
                raise ForbiddenOperationError("只有抽检复核人可以操作该抽检")
        now = _utcnow()
        updated = replace(
            current.apply_action(
                action_name,
                reviewer_id=reviewer_id,
                reason=reason,
                now=now,
            ),
            updated_by=scope.actor_id,
        )
        saved = self._samples.save(scope, updated, expected_version=expected_version)
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
            response_ref=str(saved.quality_sample_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _record(
        self,
        scope: ProjectScope,
        sample: QualitySample,
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
            resource_id=sample.quality_sample_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            details={
                "task_id": str(sample.task_id),
                "submission_id": str(sample.submission_id),
                "sampling_policy_version": sample.sampling_policy_version,
                "sample_type": sample.sample_type.value,
                "reviewer_id": str(sample.reviewer_id) if sample.reviewer_id else None,
            },
            event_type="annotation.quality_sample.status_changed",
            event_payload={
                "quality_sample_id": str(sample.quality_sample_id),
                "task_id": str(sample.task_id),
                "submission_id": str(sample.submission_id),
                "from_status": from_status,
                "to_status": to_status,
                "action": action,
                "version": sample.version,
            },
            occurred_at=now,
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_uuid(value: object, resource: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{resource} ID 非法") from exc
    raise DomainValidationError(f"{resource} ID 非法")


__all__ = ["QualitySampleUseCases"]
