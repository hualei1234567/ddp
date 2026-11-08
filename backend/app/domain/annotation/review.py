"""标注复核聚合。

复核人不得是标注人；通过必须有检查项且全部通过，驳回必须有问题项与原因。
终态复核记录内容不可变，便于审计追溯。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationReviewStatus,
    ReviewDecision,
    ReviewIssueSeverity,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    ResourceImmutableError,
    ReviewSelfForbiddenError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    review_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    REASON_MAX_LENGTH,
    coerce_enum,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    code: str
    severity: ReviewIssueSeverity | str
    message: str
    label_key: str | None = None
    item_ref: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "issue.code", max_length=100),
        )
        object.__setattr__(
            self,
            "severity",
            coerce_enum(self.severity, ReviewIssueSeverity, "issue.severity"),
        )
        object.__setattr__(
            self,
            "message",
            normalized_text(self.message, "issue.message", max_length=2000),
        )
        object.__setattr__(
            self,
            "label_key",
            optional_text(self.label_key, "issue.label_key", max_length=100),
        )
        object.__setattr__(
            self,
            "item_ref",
            optional_text(self.item_ref, "issue.item_ref", max_length=200),
        )
        object.__setattr__(
            self,
            "evidence_ref",
            optional_text(self.evidence_ref, "issue.evidence_ref", max_length=1024),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "label_key": self.label_key,
            "item_ref": self.item_ref,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class ReviewCheckItem:
    key: str
    passed: bool
    comment: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", normalized_text(self.key, "check.key", max_length=100))
        if not isinstance(self.passed, bool):
            raise DomainValidationError("check.passed 必须是布尔值")
        object.__setattr__(
            self,
            "comment",
            optional_text(self.comment, "check.comment", max_length=1000),
        )

    def to_payload(self) -> dict[str, object]:
        return {"key": self.key, "passed": self.passed, "comment": self.comment}


def _normalize_issues(value: object) -> tuple[ReviewIssue, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError("issues 必须是列表")
    issues: list[ReviewIssue] = []
    for item in value:
        issue = item if isinstance(item, ReviewIssue) else ReviewIssue(**item)  # type: ignore[arg-type]
        issues.append(issue)
    return tuple(issues)


def _normalize_checklist(value: object) -> tuple[ReviewCheckItem, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError("checklist 必须是列表")
    items: list[ReviewCheckItem] = []
    seen: set[str] = set()
    for item in value:
        check = (
            item if isinstance(item, ReviewCheckItem) else ReviewCheckItem(**item)  # type: ignore[arg-type]
        )
        if check.key in seen:
            raise DomainValidationError("checklist key 不得重复")
        seen.add(check.key)
        items.append(check)
    return tuple(items)


@dataclass(frozen=True, slots=True)
class AnnotationReview:
    review_id: UUID
    project_id: UUID
    submission_id: UUID
    task_id: UUID
    reviewer_id: UUID
    review_round: int
    status: AnnotationReviewStatus | str = AnnotationReviewStatus.PENDING
    decision: ReviewDecision | str | None = None
    issues: tuple[ReviewIssue, ...] = ()
    checklist: tuple[ReviewCheckItem, ...] = ()
    review_duration_sec: int | None = None
    reason: str | None = None
    decided_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("review_id", "project_id", "submission_id", "task_id", "reviewer_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "review_round",
            positive_int(self.review_round, "review_round"),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationReviewStatus, "status"),
        )
        if self.decision is not None:
            object.__setattr__(
                self,
                "decision",
                coerce_enum(self.decision, ReviewDecision, "decision"),
            )
        object.__setattr__(self, "issues", _normalize_issues(self.issues))
        object.__setattr__(self, "checklist", _normalize_checklist(self.checklist))
        if self.review_duration_sec is not None:
            object.__setattr__(
                self,
                "review_duration_sec",
                non_negative_int(self.review_duration_sec, "review_duration_sec"),
            )
        object.__setattr__(
            self,
            "reason",
            optional_text(self.reason, "reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "decided_at",
            optional_aware_datetime(self.decided_at, "decided_at"),
        )
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        if self.status is AnnotationReviewStatus.REJECTED and not self.issues:
            raise DomainValidationError("驳回复核必须填写问题项")
        if self.status is AnnotationReviewStatus.APPROVED and (
            not self.checklist or not all(item.passed for item in self.checklist)
        ):
            raise DomainValidationError("通过复核必须提供全部通过的检查项")

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            AnnotationReviewStatus.APPROVED,
            AnnotationReviewStatus.REJECTED,
            AnnotationReviewStatus.ESCALATED,
            AnnotationReviewStatus.CANCELLED,
        }

    def assert_not_self_review(self, annotator_id: UUID) -> None:
        if self.reviewer_id == annotator_id:
            raise ReviewSelfForbiddenError("复核人不得是标注人")

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        issues: object = None,
        checklist: object = None,
        review_duration_sec: int | None = None,
        now: datetime | None = None,
    ) -> AnnotationReview:
        action_name = validate_action_name(action, family="review")
        target = review_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("review", action_name) and cleaned_reason is None:
            raise DomainValidationError("该复核动作必须填写原因")
        normalized_issues = _normalize_issues(issues) if issues is not None else self.issues
        normalized_checklist = (
            _normalize_checklist(checklist) if checklist is not None else self.checklist
        )
        changes: dict[str, object] = {
            "status": target,
            "issues": normalized_issues,
            "checklist": normalized_checklist,
            "updated_at": occurred_at,
        }
        if review_duration_sec is not None:
            changes["review_duration_sec"] = non_negative_int(
                review_duration_sec,
                "review_duration_sec",
            )
        if target is AnnotationReviewStatus.APPROVED:
            if not normalized_checklist or not all(item.passed for item in normalized_checklist):
                raise DomainValidationError("通过复核必须提供全部通过的检查项")
            changes["decision"] = ReviewDecision.APPROVE
            changes["decided_at"] = occurred_at
        elif target is AnnotationReviewStatus.REJECTED:
            if not normalized_issues:
                raise DomainValidationError("驳回复核必须填写问题项")
            changes["decision"] = ReviewDecision.REJECT
            changes["decided_at"] = occurred_at
            changes["reason"] = cleaned_reason
        elif target is AnnotationReviewStatus.ESCALATED:
            changes["decision"] = ReviewDecision.ESCALATE
            changes["decided_at"] = occurred_at
            changes["reason"] = cleaned_reason
        elif target is AnnotationReviewStatus.CANCELLED:
            changes["reason"] = cleaned_reason
        return replace(self, **changes)

    def assert_terminal_immutable(self, candidate: AnnotationReview) -> None:
        if self.is_terminal and (
            self.submission_id != candidate.submission_id
            or self.reviewer_id != candidate.reviewer_id
            or self.review_round != candidate.review_round
            or self.status != candidate.status
            or self.decision != candidate.decision
            or [item.to_payload() for item in self.issues]
            != [item.to_payload() for item in candidate.issues]
            or [item.to_payload() for item in self.checklist]
            != [item.to_payload() for item in candidate.checklist]
            or self.reason != candidate.reason
        ):
            raise ResourceImmutableError("终态复核记录不可修改")


__all__ = ["AnnotationReview", "ReviewCheckItem", "ReviewIssue"]
