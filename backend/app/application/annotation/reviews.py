"""标注复核用例：领取、通过、驳回、升级仲裁与职责分离。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationReview
from app.application.annotation.episode_status_projection import (
    project_submission_status,
)
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationRecordRepository,
    AnnotationReviewRepository,
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
    AnnotationRecordStatus,
    AnnotationReviewAction,
    AnnotationReviewStatus,
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    ForbiddenOperationError,
    ResourceNotFoundError,
    ReviewNotPendingError,
    ReviewSelfForbiddenError,
    SubmissionAlreadyDecidedError,
    SubmissionNotSubmittedError,
    VersionConflictError,
)
from app.domain.annotation.review import AnnotationReview
from app.domain.annotation.state_machines import (
    allowed_review_actions,
    assert_review_action,
    validate_action_name,
)
from app.domain.annotation.submission import submission_already_decided
from app.domain.identity.models import ProjectScope

if TYPE_CHECKING:
    from app.application.annotation.progress import AnnotationProgressRefresher

_RESOURCE_TYPE = "ANNOTATION_REVIEW"


class AnnotationReviewUseCases:
    def __init__(
        self,
        reviews: AnnotationReviewRepository,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        tasks: AnnotationTaskRepository,
        members: MemberDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        *,
        episodes: EpisodeDirectory | None = None,
        progress: AnnotationProgressRefresher | None = None,
    ) -> None:
        self._reviews = reviews
        self._submissions = submissions
        self._records = records
        self._tasks = tasks
        self._members = members
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        self._episodes = episodes
        self._progress = progress

    def get(self, scope: ProjectScope, review_id: UUID) -> AnnotationReview:
        review = self._reviews.get(scope, review_id)
        if review is None:
            raise ResourceNotFoundError("标注复核", review_id)
        return review

    def allowed_actions(self, scope: ProjectScope, review_id: UUID) -> frozenset[str]:
        return allowed_review_actions(self.get(scope, review_id).status)

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationReview]:
        return self._reviews.list_for_submission(scope, submission_id)

    def create(
        self,
        scope: ProjectScope,
        command: NewAnnotationReview,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationReview:
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
            return self.get(scope, _as_uuid(replay.response_ref, "标注复核"))

        submission = self._get_submission(scope, command.submission_id)
        if submission.status is not AnnotationSubmissionStatus.SUBMITTED:
            # 已作出终态结论 vs 还没提交：契约给了两个不同码。前者说明"结论已定、
            # 要重开必须走新版本"，后者说明"标注人还没交"。混成一个码会让调用方
            # 分不清该去找标注员还是复核人。
            if submission_already_decided(submission.status):
                raise SubmissionAlreadyDecidedError(
                    "提交已作出终态结论，不能重复复核",
                    details={
                        "submission_id": str(submission.submission_id),
                        "status": getattr(submission.status, "value", submission.status),
                    },
                )
            raise SubmissionNotSubmittedError("只有 SUBMITTED 提交可以创建复核")
        if command.reviewer_id == submission.annotator_id:
            raise ReviewSelfForbiddenError("复核人不得是标注人")
        if not self._members.user_is_member(scope, command.reviewer_id):
            raise ResourceNotFoundError("项目成员", command.reviewer_id)
        review = AnnotationReview(
            review_id=uuid4(),
            project_id=scope.project_id,
            submission_id=submission.submission_id,
            task_id=submission.task_id,
            reviewer_id=command.reviewer_id,
            review_round=self._reviews.next_round(scope, submission.submission_id),
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._reviews.add(scope, review)
        self._record(scope, created, "create", None, created.status.value, None, request_id, now)
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.review_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        review_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        issues: object = None,
        checklist: object = None,
        review_duration_sec: int | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationReview:
        key = validate_key(idempotency_key)
        body = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
            "issues": issues,
            "checklist": checklist,
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
            return self.get(scope, _as_uuid(replay.response_ref, "标注复核"))
        action_name = validate_action_name(action, family="review")
        current = self.get(scope, review_id)
        if current.version != expected_version:
            raise VersionConflictError("标注复核")
        assert_review_action(current.status, action_name)
        if scope.actor_id != current.reviewer_id:
            raise ForbiddenOperationError("只有复核人可以操作该复核")
        if (
            action_name
            in {
                AnnotationReviewAction.APPROVE.value,
                AnnotationReviewAction.REJECT.value,
                AnnotationReviewAction.ESCALATE.value,
            }
            and current.status is not AnnotationReviewStatus.IN_REVIEW
        ):
            raise ReviewNotPendingError("复核不在 IN_REVIEW，不能作出结论")
        updated = replace(
            current.apply_action(
                action_name,
                reason=reason,
                issues=issues,
                checklist=checklist,
                review_duration_sec=review_duration_sec,
                now=now,
            ),
            updated_by=scope.actor_id,
        )
        saved = self._reviews.save(scope, updated, expected_version=expected_version)
        submission = self._get_submission(scope, current.submission_id)
        saved.assert_not_self_review(submission.annotator_id)
        if action_name == AnnotationReviewAction.CLAIM.value:
            # 双人复核时会有第二条复核指向同一提交。提交只该被推进一次
            # （SUBMITTED → IN_REVIEW），第二条复核 claim 时它已经在
            # IN_REVIEW，再推进会被状态机拒绝，导致第二条复核永远领不了。
            # 这里按提交当前状态决定是否推进：不推进不是错误，第一条复核
            # 已经完成了这次迁移。
            if submission.status is AnnotationSubmissionStatus.SUBMITTED:
                self._transition_submission(
                    scope,
                    submission,
                    AnnotationSubmissionAction.SYSTEM_REVIEW_START.value,
                    now=now,
                )
        elif action_name in {
            AnnotationReviewAction.APPROVE.value,
            AnnotationReviewAction.REJECT.value,
            AnnotationReviewAction.ESCALATE.value,
        }:
            self._apply_submission_decision(
                scope,
                submission,
                action_name,
                reason=reason,
                now=now,
            )
        if self._progress is not None:
            self._progress.refresh_for_submission(scope, submission, now=now)
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
            response_ref=str(saved.review_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _apply_submission_decision(
        self,
        scope: ProjectScope,
        submission,
        action: str,
        *,
        reason: str | None,
        now: datetime,
    ) -> None:
        action_map = {
            AnnotationReviewAction.APPROVE.value: AnnotationSubmissionAction.APPROVE.value,
            AnnotationReviewAction.REJECT.value: AnnotationSubmissionAction.REJECT.value,
            AnnotationReviewAction.ESCALATE.value: AnnotationSubmissionAction.ESCALATE.value,
        }
        submission_action = action_map[action]
        updated = replace(
            submission.apply_action(submission_action, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        self._submissions.save(scope, updated, expected_version=submission.version)
        self._project(scope, submission, updated.status, now=now)
        target = {
            AnnotationSubmissionAction.APPROVE.value: AnnotationRecordStatus.APPROVED,
            AnnotationSubmissionAction.REJECT.value: AnnotationRecordStatus.REJECTED,
            AnnotationSubmissionAction.ESCALATE.value: None,
        }[submission_action]
        if target is not None:
            self._mark_records(scope, submission, target, reason=reason, now=now)

    def _transition_submission(
        self,
        scope: ProjectScope,
        submission,
        action: str,
        *,
        now: datetime,
    ) -> None:
        updated = replace(
            submission.apply_action(action, now=now),
            updated_by=scope.actor_id,
        )
        self._submissions.save(scope, updated, expected_version=submission.version)
        self._project(scope, submission, updated.status, now=now)

    def _project(self, scope: ProjectScope, submission, status, *, now: datetime) -> None:
        """把提交状态投影到 Episode（规则见 episode_status_projection）。"""
        if self._episodes is None:
            return
        project_submission_status(
            self._episodes,
            scope,
            submission_status=status,
            episode_ids=self._episode_ids_of(scope, submission.submission_id),
            now=now,
        )

    def _episode_ids_of(self, scope: ProjectScope, submission_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            dict.fromkeys(
                record.episode_id
                for record in self._records.list_for_submission(scope, submission_id)
            )
        )

    def _mark_records(
        self,
        scope: ProjectScope,
        submission,
        target: AnnotationRecordStatus,
        *,
        reason: str | None,
        now: datetime,
    ) -> None:
        action = {
            AnnotationRecordStatus.APPROVED: "approve-with-submission",
            AnnotationRecordStatus.REJECTED: "reject-with-submission",
        }[target]
        for record in self._records.list_for_submission(scope, submission.submission_id):
            if record.status is not AnnotationRecordStatus.SUBMITTED:
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

    def _get_submission(self, scope: ProjectScope, submission_id: UUID):
        submission = self._submissions.get(scope, submission_id)
        if submission is None:
            raise ResourceNotFoundError("标注提交", submission_id)
        return submission

    def _record(
        self,
        scope: ProjectScope,
        review: AnnotationReview,
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
            resource_id=review.review_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            details={
                "submission_id": str(review.submission_id),
                "reviewer_id": str(review.reviewer_id),
                "review_round": review.review_round,
            },
            event_type="annotation.review.status_changed",
            event_payload={
                "review_id": str(review.review_id),
                "submission_id": str(review.submission_id),
                "from_status": from_status,
                "to_status": to_status,
                "action": action,
                "version": review.version,
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


__all__ = ["AnnotationReviewUseCases"]
