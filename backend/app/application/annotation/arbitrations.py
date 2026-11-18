"""标注仲裁用例：升级受理、独立仲裁人、投票、法定人数与终态结论。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationArbitration
from app.application.annotation.episode_status_projection import (
    project_submission_status,
)
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationArbitrationRepository,
    AnnotationRecordRepository,
    AnnotationReviewRepository,
    AnnotationSubmissionRepository,
    AuditRepository,
    EpisodeDirectory,
    IdempotencyRepository,
    MemberDirectory,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.arbitration import AnnotationArbitration
from app.domain.annotation.enums import (
    AnnotationArbitrationAction,
    AnnotationArbitrationStatus,
    AnnotationRecordStatus,
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
)
from app.domain.annotation.errors import (
    ArbitrationNotPendingError,
    ArbitrationRequiredError,
    DomainValidationError,
    ResourceNotFoundError,
    SubmissionAlreadyDecidedError,
    VersionConflictError,
)
from app.domain.annotation.state_machines import (
    allowed_arbitration_actions,
    assert_arbitration_action,
    validate_action_name,
)
from app.domain.annotation.submission import submission_already_decided
from app.domain.identity.models import ProjectScope

if TYPE_CHECKING:
    from app.application.annotation.progress import AnnotationProgressRefresher

_RESOURCE_TYPE = "ANNOTATION_ARBITRATION"


class AnnotationArbitrationUseCases:
    def __init__(
        self,
        arbitrations: AnnotationArbitrationRepository,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        reviews: AnnotationReviewRepository,
        members: MemberDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        *,
        episodes: EpisodeDirectory | None = None,
        progress: AnnotationProgressRefresher | None = None,
    ) -> None:
        self._arbitrations = arbitrations
        self._submissions = submissions
        self._records = records
        self._reviews = reviews
        self._members = members
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        self._episodes = episodes
        self._progress = progress

    def get(self, scope: ProjectScope, arbitration_id: UUID) -> AnnotationArbitration:
        arbitration = self._arbitrations.get(scope, arbitration_id)
        if arbitration is None:
            raise ResourceNotFoundError("标注仲裁", arbitration_id)
        return arbitration

    def allowed_actions(
        self,
        scope: ProjectScope,
        arbitration_id: UUID,
    ) -> frozenset[str]:
        return allowed_arbitration_actions(self.get(scope, arbitration_id).status)

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationArbitration]:
        return self._arbitrations.list_for_submission(scope, submission_id)

    def create(
        self,
        scope: ProjectScope,
        command: NewAnnotationArbitration,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationArbitration:
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
            return self.get(scope, _as_uuid(replay.response_ref, "标注仲裁"))

        submission = self._get_submission(scope, command.submission_id)
        if submission.status is not AnnotationSubmissionStatus.ESCALATED:
            if submission_already_decided(submission.status):
                raise SubmissionAlreadyDecidedError(
                    "提交已作出终态结论，不能重复仲裁",
                    details={
                        "submission_id": str(submission.submission_id),
                        "status": getattr(submission.status, "value", submission.status),
                    },
                )
            raise ArbitrationRequiredError("只有 ESCALATED 提交可以创建仲裁")
        for arbiter_id in command.arbiter_ids:
            if not self._members.user_is_member(scope, arbiter_id):
                raise ResourceNotFoundError("项目成员", arbiter_id)
        review = None
        if command.review_id is not None:
            review = self._reviews.get(scope, command.review_id)
            if review is None or review.submission_id != submission.submission_id:
                raise ResourceNotFoundError("标注复核", command.review_id)
        else:
            review = self._reviews.first_for_submission(scope, submission.submission_id)
        arbitration = AnnotationArbitration(
            arbitration_id=uuid4(),
            project_id=scope.project_id,
            task_id=submission.task_id,
            submission_id=submission.submission_id,
            review_id=review.review_id if review is not None else None,
            arbiter_ids=command.arbiter_ids,
            quorum=command.quorum,
            trigger=command.trigger,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        arbitration.assert_eligible(
            annotator_id=submission.annotator_id,
            first_reviewer_id=review.reviewer_id if review is not None else None,
        )
        created = self._arbitrations.add(scope, arbitration)
        self._record(scope, created, "create", None, created.status.value, None, request_id, now)
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.arbitration_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        arbitration_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationArbitration:
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
            return self.get(scope, _as_uuid(replay.response_ref, "标注仲裁"))
        action_name = validate_action_name(action, family="arbitration")
        current = self.get(scope, arbitration_id)
        if current.version != expected_version:
            raise VersionConflictError("标注仲裁")
        assert_arbitration_action(current.status, action_name)
        if current.status is AnnotationArbitrationStatus.PENDING and action_name in {
            AnnotationArbitrationAction.RESOLVE_APPROVED.value,
            AnnotationArbitrationAction.RESOLVE_REJECTED.value,
            AnnotationArbitrationAction.RESOLVE_REWORK.value,
        }:
            raise ArbitrationNotPendingError("仲裁尚未开始，不能直接出结论")
        current.assert_actor_is_arbiter(scope.actor_id)
        updated = replace(
            current.apply_action(action_name, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._arbitrations.save(scope, updated, expected_version=expected_version)
        submission = self._get_submission(scope, current.submission_id)
        if action_name == AnnotationArbitrationAction.CLAIM.value:
            # recuse 会把仲裁退回 PENDING，同一仲裁可以被再次受理；但提交在
            # 第一次受理时已经进入 ARBITRATING，重复推进会被状态机拒绝，
            # 结果就是"退出了就再也接不回来"。按提交当前状态决定是否推进。
            if submission.status is AnnotationSubmissionStatus.ESCALATED:
                self._transition_submission(
                    scope,
                    submission,
                    AnnotationSubmissionAction.ARBITRATE_START.value,
                    now=now,
                )
        elif action_name in {
            AnnotationArbitrationAction.RESOLVE_APPROVED.value,
            AnnotationArbitrationAction.RESOLVE_REJECTED.value,
            AnnotationArbitrationAction.RESOLVE_REWORK.value,
        }:
            self._apply_resolution(
                scope,
                saved,
                submission,
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
            response_ref=str(saved.arbitration_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def vote(
        self,
        scope: ProjectScope,
        arbitration_id: UUID,
        *,
        decision: str,
        expected_version: int,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> AnnotationArbitration:
        current = self.get(scope, arbitration_id)
        if current.version != expected_version:
            raise VersionConflictError("标注仲裁")
        if current.status is not AnnotationArbitrationStatus.IN_ARBITRATION:
            raise ArbitrationNotPendingError("仲裁不在进行中，不能投票")
        current.assert_actor_is_arbiter(scope.actor_id)
        now = _utcnow()
        updated = replace(
            current.add_vote(
                arbiter_id=scope.actor_id,
                decision=decision,
                reason=reason,
                now=now,
            ),
            updated_by=scope.actor_id,
        )
        saved = self._arbitrations.save(scope, updated, expected_version=expected_version)
        self._record(
            scope,
            saved,
            "vote",
            current.status.value,
            saved.status.value,
            reason,
            request_id,
            now,
        )
        self._uow.commit()
        return saved

    def _apply_resolution(
        self,
        scope: ProjectScope,
        arbitration: AnnotationArbitration,
        submission,
        *,
        reason: str | None,
        now: datetime,
    ) -> None:
        action = {
            AnnotationArbitrationStatus.RESOLVED_APPROVED: AnnotationSubmissionAction.RESOLVE_APPROVED,  # noqa: E501
            AnnotationArbitrationStatus.RESOLVED_REJECTED: AnnotationSubmissionAction.RESOLVE_REJECTED,  # noqa: E501
            AnnotationArbitrationStatus.RESOLVED_REWORK: AnnotationSubmissionAction.RESOLVE_REWORK,  # noqa: E501
        }[arbitration.status]
        updated = replace(
            submission.apply_action(action.value, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._submissions.save(scope, updated, expected_version=submission.version)
        self._project(scope, saved.submission_id, saved.status, now=now)
        record_target = (
            AnnotationRecordStatus.APPROVED
            if action is AnnotationSubmissionAction.RESOLVE_APPROVED
            else AnnotationRecordStatus.REJECTED
        )
        record_action = (
            "approve-with-submission"
            if record_target is AnnotationRecordStatus.APPROVED
            else "reject-with-submission"
        )
        for record in self._records.list_for_submission(scope, saved.submission_id):
            if record.status is not AnnotationRecordStatus.SUBMITTED:
                continue
            record_updated = replace(
                record.apply_action(
                    record_action,
                    actor_id=scope.actor_id,
                    reason=reason,
                    now=now,
                ),
                updated_by=scope.actor_id,
            )
            self._records.save(scope, record_updated, expected_version=record.version)

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
        self._project(scope, submission.submission_id, updated.status, now=now)

    def _project(self, scope: ProjectScope, submission_id: UUID, status, *, now: datetime) -> None:
        """把提交状态投影到 Episode（规则见 episode_status_projection）。"""
        if self._episodes is None:
            return
        episode_ids = tuple(
            dict.fromkeys(
                record.episode_id
                for record in self._records.list_for_submission(scope, submission_id)
            )
        )
        project_submission_status(
            self._episodes,
            scope,
            submission_status=status,
            episode_ids=episode_ids,
            now=now,
        )

    def _get_submission(self, scope: ProjectScope, submission_id: UUID):
        submission = self._submissions.get(scope, submission_id)
        if submission is None:
            raise ResourceNotFoundError("标注提交", submission_id)
        return submission

    def _record(
        self,
        scope: ProjectScope,
        arbitration: AnnotationArbitration,
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
            resource_id=arbitration.arbitration_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            details={
                "submission_id": str(arbitration.submission_id),
                "review_id": str(arbitration.review_id) if arbitration.review_id else None,
                "quorum": arbitration.quorum,
                "vote_count": arbitration.effective_vote_count,
            },
            event_type="annotation.arbitration.status_changed",
            event_payload={
                "arbitration_id": str(arbitration.arbitration_id),
                "submission_id": str(arbitration.submission_id),
                "from_status": from_status,
                "to_status": to_status,
                "action": action,
                "final_decision": (
                    arbitration.final_decision.value
                    if arbitration.final_decision is not None
                    else None
                ),
                "version": arbitration.version,
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


__all__ = ["AnnotationArbitrationUseCases"]
