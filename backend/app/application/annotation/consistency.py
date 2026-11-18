"""双标任务一致性门槛与自动仲裁/自动通过编排。

职责边界：

- 指标计算：唯一实现在 :mod:`app.domain.annotation.iaa`；
- 策略解析：唯一实现在 :mod:`app.domain.annotation.review_policy`；
- 状态跃迁：复用既有提交/复核/仲裁状态机，不新增第二套状态机。

本用例不做读取即写入：``evaluate`` 是纯读；只有提交后的
``after_submission`` 才会因门槛结论创建仲裁项或自动通过。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.annotation.arbitrations import AnnotationArbitrationUseCases
from app.application.annotation.commands import (
    NewAnnotationArbitration,
    NewAnnotationReview,
)
from app.application.annotation.iaa import to_iaa_observation
from app.application.annotation.ports import (
    AnnotationArbitrationRepository,
    AnnotationRecordRepository,
    AnnotationReviewRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    MemberDirectory,
    UnitOfWork,
)
from app.application.annotation.reviews import AnnotationReviewUseCases
from app.domain.annotation.enums import (
    AnnotationArbitrationStatus,
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
    ArbitrationTrigger,
)
from app.domain.annotation.errors import (
    AnnotationDomainError,
    ResourceNotFoundError,
)
from app.domain.annotation.iaa import (
    DoubleAnnotationEvaluation,
    evaluate_double_annotation,
)
from app.domain.annotation.review_policy import ReviewMode, ReviewPolicy
from app.domain.annotation.submission import AnnotationSubmission
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectRole, ProjectScope

logger = logging.getLogger(__name__)

AUTO_APPROVE_REASON = "双标一致性达标自动通过"
ARBITRATION_REASON = "双标一致性低于阈值"
_METRIC_SOURCE_STATUSES = ("SUBMITTED", "APPROVED")
_PENDING_SUBMISSION_STATUSES = (
    AnnotationSubmissionStatus.SUBMITTED.value,
    AnnotationSubmissionStatus.IN_REVIEW.value,
    AnnotationSubmissionStatus.ESCALATED.value,
)


@dataclass(frozen=True, slots=True)
class DoubleAnnotationConsistencyResult:
    task_id: UUID
    project_id: UUID
    review_mode: str
    status: str
    metric: str | None
    threshold: float | None
    value: float | None
    passed: bool | None
    sample_size: int
    episode_count: int
    double_episode_count: int
    annotator_count: int
    missing_episode_ids: tuple[UUID, ...]
    auto_approve_enabled: bool
    auto_approved: bool
    arbitration_required: bool
    arbitration_ids: tuple[UUID, ...]
    details: tuple[dict[str, object], ...]
    evaluated_at: datetime
    message: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


_MESSAGES = {
    "SINGLE": "任务为单人复核，不适用双标一致性门槛",
    "PENDING_SECOND_ANNOTATOR": "尚缺第二名标注者独立提交",
    "PARTIAL": "部分 Episode 仍缺第二名标注者",
    "INSUFFICIENT_DATA": "重叠样本存在，但所选指标无法计算",
    "PASSED": "一致性达标",
    "FAILED": "一致性低于阈值",
}


class AnnotationConsistencyUseCases:
    def __init__(
        self,
        tasks: AnnotationTaskRepository,
        records: AnnotationRecordRepository,
        submissions: AnnotationSubmissionRepository,
        reviews: AnnotationReviewRepository,
        arbitrations: AnnotationArbitrationRepository,
        members: MemberDirectory,
        arbitration_use_cases: AnnotationArbitrationUseCases,
        review_use_cases: AnnotationReviewUseCases,
        uow: UnitOfWork,
    ) -> None:
        self._tasks = tasks
        self._records = records
        self._submissions = submissions
        self._reviews = reviews
        self._arbitrations = arbitrations
        self._members = members
        self._arbitration_use_cases = arbitration_use_cases
        self._review_use_cases = review_use_cases
        self._uow = uow

    def evaluate(self, scope: ProjectScope, task_id: UUID) -> DoubleAnnotationConsistencyResult:
        """只读计算一致性结论，不创建仲裁、不自动通过。"""
        task = self._get_task(scope, task_id)
        policy = ReviewPolicy.from_mapping(task.iaa_policy)
        if policy.review_mode is not ReviewMode.DOUBLE:
            return self._single_result(scope, task, policy)

        observations = [
            to_iaa_observation(item.record, item.annotator_id)
            for item in self._records.list_for_task(
                scope,
                task_id=task_id,
                statuses=_METRIC_SOURCE_STATUSES,
            )
        ]
        evaluation = evaluate_double_annotation(
            observations,
            metric=policy.agreement_metric,
            threshold=policy.agreement_threshold,
        )
        return self._from_evaluation(scope, task, policy, evaluation)

    def after_submission(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        request_id: str | None = None,
    ) -> DoubleAnnotationConsistencyResult:
        """提交后的门槛执行：不达标进仲裁，达标且显式开启时自动通过。"""
        result = self.evaluate(scope, submission.task_id)
        if result.review_mode != ReviewMode.DOUBLE.value:
            return result
        policy = ReviewPolicy.from_mapping(self._get_task(scope, submission.task_id).iaa_policy)
        if result.status == "FAILED":
            arbitration_ids, message = self._create_arbitrations(
                scope,
                submission.task_id,
                request_id=request_id,
            )
            return replace(
                result,
                arbitration_ids=arbitration_ids,
                message=message,
            )
        if result.status == "PASSED" and policy.auto_approve:
            auto_approved, message = self._auto_approve(
                scope,
                submission.task_id,
                request_id=request_id,
            )
            return replace(result, auto_approved=auto_approved, message=message)
        return result

    def _single_result(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        policy: ReviewPolicy,
    ) -> DoubleAnnotationConsistencyResult:
        return DoubleAnnotationConsistencyResult(
            task_id=task.task_id,
            project_id=scope.project_id,
            review_mode=policy.review_mode.value,
            status="SINGLE",
            metric=None,
            threshold=None,
            value=None,
            passed=None,
            sample_size=0,
            episode_count=0,
            double_episode_count=0,
            annotator_count=0,
            missing_episode_ids=(),
            auto_approve_enabled=False,
            auto_approved=False,
            arbitration_required=False,
            arbitration_ids=(),
            details=(),
            evaluated_at=_utcnow(),
            message=_MESSAGES["SINGLE"],
        )

    def _from_evaluation(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        policy: ReviewPolicy,
        evaluation: DoubleAnnotationEvaluation,
    ) -> DoubleAnnotationConsistencyResult:
        return DoubleAnnotationConsistencyResult(
            task_id=task.task_id,
            project_id=scope.project_id,
            review_mode=policy.review_mode.value,
            status=evaluation.status,
            metric=evaluation.metric.value,
            threshold=evaluation.threshold,
            value=evaluation.value,
            passed=evaluation.passed,
            sample_size=evaluation.sample_size,
            episode_count=evaluation.episode_count,
            double_episode_count=evaluation.double_episode_count,
            annotator_count=evaluation.annotator_count,
            missing_episode_ids=evaluation.missing_episode_ids,
            auto_approve_enabled=policy.auto_approve,
            auto_approved=False,
            arbitration_required=evaluation.status == "FAILED",
            arbitration_ids=(),
            details=tuple(item.to_payload() for item in evaluation.metrics),
            evaluated_at=_utcnow(),
            message=_MESSAGES.get(evaluation.status),
        )

    def _create_arbitrations(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        request_id: str | None,
    ) -> tuple[tuple[UUID, ...], str]:
        submissions = self._submissions.list_for_task(
            scope,
            task_id=task_id,
            statuses=_PENDING_SUBMISSION_STATUSES,
            limit=500,
        )
        if not submissions:
            return (), "一致性低于阈值，但没有可进入仲裁的提交"
        annotator_ids = {item.annotator_id for item in submissions}
        arbiter_ids = self._arbiter_candidates(scope, annotator_ids)
        if not arbiter_ids:
            return (), "一致性低于阈值，但项目内没有可用仲裁人"

        created_ids: list[UUID] = []
        approval_scope = ProjectScope(project_id=scope.project_id, actor_id=arbiter_ids[0])
        now = _utcnow()
        for submission in submissions:
            existing = tuple(
                self._arbitrations.list_for_submission(scope, submission.submission_id)
            )
            if any(
                item.trigger is ArbitrationTrigger.IAA_CONFLICT
                and item.status is not AnnotationArbitrationStatus.CANCELLED
                for item in existing
            ):
                continue
            try:
                escalated = self._escalate_submission(scope, submission, now=now)
                arbitration = self._arbitration_use_cases.create(
                    approval_scope,
                    NewAnnotationArbitration(
                        submission_id=escalated.submission_id,
                        arbiter_ids=arbiter_ids,
                        quorum=1,
                        trigger=ArbitrationTrigger.IAA_CONFLICT,
                        review_id=None,
                    ),
                    method="POST",
                    path=f"/annotation-submissions/{escalated.submission_id}/arbitrations",
                    request_id=request_id,
                )
            except AnnotationDomainError as exc:
                logger.warning(
                    "双标不达标自动创建仲裁失败",
                    extra={
                        "submission_id": str(submission.submission_id),
                        "project_id": str(scope.project_id),
                        "reason": str(exc),
                    },
                )
                continue
            created_ids.append(arbitration.arbitration_id)
        self._uow.commit()
        if not created_ids:
            return (), "一致性低于阈值，但仲裁项已存在或无法创建"
        return tuple(created_ids), f"{ARBITRATION_REASON}，已进入仲裁"

    def _escalate_submission(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        now: datetime,
    ) -> AnnotationSubmission:
        """用既有提交状态机推进到 ESCALATED（SUBMITTED → IN_REVIEW → ESCALATED）。"""
        current = submission
        if current.status is AnnotationSubmissionStatus.SUBMITTED:
            updated = replace(
                current.apply_action(
                    AnnotationSubmissionAction.SYSTEM_REVIEW_START.value,
                    now=now,
                ),
                updated_by=scope.actor_id,
            )
            current = self._submissions.save(scope, updated, expected_version=current.version)
        if current.status is AnnotationSubmissionStatus.IN_REVIEW:
            updated = replace(
                current.apply_action(
                    AnnotationSubmissionAction.ESCALATE.value,
                    reason=ARBITRATION_REASON,
                    now=now,
                ),
                updated_by=scope.actor_id,
            )
            current = self._submissions.save(scope, updated, expected_version=current.version)
        return current

    def _arbiter_candidates(
        self,
        scope: ProjectScope,
        excluded: set[UUID],
    ) -> tuple[UUID, ...]:
        owners = self._members.list_member_ids(scope, role=ProjectRole.OWNER.value)
        candidates = [item for item in owners if item not in excluded]
        if candidates:
            return tuple(candidates)
        # 没有可用的 OWNER 时才退回其他成员，避免自动仲裁在只有编辑者的项目里失效。
        return tuple(item for item in self._members.list_member_ids(scope) if item not in excluded)

    def _auto_approve(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        request_id: str | None,
    ) -> tuple[bool, str]:
        submissions = self._submissions.list_for_task(
            scope,
            task_id=task_id,
            statuses=_PENDING_SUBMISSION_STATUSES,
            limit=500,
        )
        pending = [
            item for item in submissions if item.status is AnnotationSubmissionStatus.SUBMITTED
        ]
        if not pending:
            return False, "一致性达标，但没有待自动通过的提交"
        annotator_ids = {item.annotator_id for item in pending}
        owners = self._members.list_member_ids(scope, role=ProjectRole.OWNER.value)
        approver = next((item for item in owners if item not in annotator_ids), None)
        if approver is None:
            return False, "一致性达标，但项目内没有可作为自动通过人的负责人"
        approval_scope = ProjectScope(project_id=scope.project_id, actor_id=approver)
        approved = 0
        for submission in pending:
            try:
                review = self._review_use_cases.create(
                    approval_scope,
                    NewAnnotationReview(
                        submission_id=submission.submission_id,
                        reviewer_id=approver,
                    ),
                    method="POST",
                    path=f"/annotation-submissions/{submission.submission_id}/reviews",
                    request_id=request_id,
                )
                claimed = self._review_use_cases.apply_action(
                    approval_scope,
                    review.review_id,
                    "claim",
                    expected_version=review.version,
                    method="POST",
                    path=f"/annotation-reviews/{review.review_id}/actions/claim",
                    request_id=request_id,
                )
                self._review_use_cases.apply_action(
                    approval_scope,
                    review.review_id,
                    "approve",
                    expected_version=claimed.version,
                    reason=AUTO_APPROVE_REASON,
                    checklist=[
                        {
                            "key": "double_annotation_consistency",
                            "passed": True,
                            "comment": AUTO_APPROVE_REASON,
                        }
                    ],
                    method="POST",
                    path=f"/annotation-reviews/{review.review_id}/actions/approve",
                    request_id=request_id,
                )
            except AnnotationDomainError as exc:
                logger.warning(
                    "双标达标自动通过失败：%s",
                    exc,
                    extra={
                        "submission_id": str(submission.submission_id),
                        "project_id": str(scope.project_id),
                        "reason": str(exc),
                    },
                )
                continue
            approved += 1
        if approved == 0:
            return False, "一致性达标，但自动通过未执行"
        return True, "一致性达标，已自动通过"

    def _get_task(self, scope: ProjectScope, task_id: UUID) -> AnnotationTask:
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        return task


__all__ = [
    "AnnotationConsistencyUseCases",
    "DoubleAnnotationConsistencyResult",
]
