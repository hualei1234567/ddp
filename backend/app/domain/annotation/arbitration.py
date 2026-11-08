"""标注仲裁聚合。

仲裁人必须独立于标注人与首轮复核人；投票达到 quorum 才能形成终态结论。
判断与投票由用例层编排，领域层保证人数、分离与状态规则。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationArbitrationStatus,
    ArbitrationDecision,
    ArbitrationTrigger,
)
from app.domain.annotation.errors import (
    ArbiterConflictError,
    ArbitrationQuorumNotMetError,
    DomainValidationError,
    ResourceImmutableError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    arbitration_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    REASON_MAX_LENGTH,
    aware_datetime,
    coerce_enum,
    optional_aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
    uuid_tuple,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_votes(value: object) -> tuple[ArbitrationVote, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError("votes 必须是列表")
    votes: list[ArbitrationVote] = []
    seen: set[UUID] = set()
    for item in value:
        vote = item if isinstance(item, ArbitrationVote) else ArbitrationVote(**item)  # type: ignore[arg-type]
        if vote.arbiter_id in seen:
            raise DomainValidationError("同一仲裁人不得重复投票")
        seen.add(vote.arbiter_id)
        votes.append(vote)
    return tuple(votes)


@dataclass(frozen=True, slots=True)
class ArbitrationVote:
    arbiter_id: UUID
    decision: ArbitrationDecision | str
    voted_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        required_uuid(self.arbiter_id, "vote.arbiter_id")
        object.__setattr__(
            self,
            "decision",
            coerce_enum(self.decision, ArbitrationDecision, "vote.decision"),
        )
        object.__setattr__(self, "voted_at", aware_datetime(self.voted_at, "vote.voted_at"))
        object.__setattr__(
            self,
            "reason",
            optional_text(self.reason, "vote.reason", max_length=REASON_MAX_LENGTH),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "arbiter_id": str(self.arbiter_id),
            "decision": self.decision.value,
            "reason": self.reason,
            "voted_at": self.voted_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AnnotationArbitration:
    arbitration_id: UUID
    project_id: UUID
    task_id: UUID
    submission_id: UUID
    review_id: UUID | None
    arbiter_ids: tuple[UUID, ...]
    quorum: int
    trigger: ArbitrationTrigger | str
    status: AnnotationArbitrationStatus | str = AnnotationArbitrationStatus.PENDING
    votes: tuple[ArbitrationVote, ...] = ()
    final_decision: ArbitrationDecision | str | None = None
    reason: str | None = None
    resolved_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("arbitration_id", "project_id", "task_id", "submission_id"):
            required_uuid(getattr(self, name), name)
        if self.review_id is not None:
            required_uuid(self.review_id, "review_id")
        object.__setattr__(
            self,
            "arbiter_ids",
            uuid_tuple(self.arbiter_ids, "arbiter_ids"),
        )
        if not self.arbiter_ids:
            raise DomainValidationError("仲裁人列表不能为空")
        object.__setattr__(self, "quorum", positive_int(self.quorum, "quorum"))
        if self.quorum > len(self.arbiter_ids):
            raise ArbitrationQuorumNotMetError("法定人数不得超过仲裁人数量")
        object.__setattr__(
            self,
            "trigger",
            coerce_enum(self.trigger, ArbitrationTrigger, "trigger"),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationArbitrationStatus, "status"),
        )
        object.__setattr__(self, "votes", _normalize_votes(self.votes))
        if self.final_decision is not None:
            object.__setattr__(
                self,
                "final_decision",
                coerce_enum(self.final_decision, ArbitrationDecision, "final_decision"),
            )
        object.__setattr__(
            self,
            "reason",
            optional_text(self.reason, "reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "resolved_at",
            optional_aware_datetime(self.resolved_at, "resolved_at"),
        )
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        for vote in self.votes:
            if vote.arbiter_id not in self.arbiter_ids:
                raise DomainValidationError("投票人必须在仲裁人列表中")

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            AnnotationArbitrationStatus.RESOLVED_APPROVED,
            AnnotationArbitrationStatus.RESOLVED_REJECTED,
            AnnotationArbitrationStatus.RESOLVED_REWORK,
            AnnotationArbitrationStatus.CANCELLED,
        }

    def assert_eligible(
        self,
        *,
        annotator_id: UUID,
        first_reviewer_id: UUID | None,
    ) -> None:
        if annotator_id in self.arbiter_ids:
            raise ArbiterConflictError("仲裁人不得是标注人")
        if first_reviewer_id is not None and first_reviewer_id in self.arbiter_ids:
            raise ArbiterConflictError("仲裁人不得是首轮复核人")

    def assert_actor_is_arbiter(self, actor_id: UUID) -> None:
        if actor_id not in self.arbiter_ids:
            raise ArbiterConflictError("当前用户不是该仲裁的仲裁人")

    def add_vote(
        self,
        *,
        arbiter_id: UUID,
        decision: ArbitrationDecision | str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationArbitration:
        if self.status is not AnnotationArbitrationStatus.IN_ARBITRATION:
            raise DomainValidationError("仅仲裁进行中允许投票")
        if any(vote.arbiter_id == arbiter_id for vote in self.votes):
            raise DomainValidationError("同一仲裁人不得重复投票")
        vote = ArbitrationVote(
            arbiter_id=arbiter_id,
            decision=decision,
            reason=reason,
            voted_at=now or _now(),
        )
        return replace(self, votes=(*self.votes, vote), updated_at=vote.voted_at)

    @property
    def effective_vote_count(self) -> int:
        return sum(1 for vote in self.votes if vote.decision is not ArbitrationDecision.ABSTAIN)

    def assert_quorum(self) -> None:
        if self.effective_vote_count < self.quorum:
            raise ArbitrationQuorumNotMetError("仲裁投票未达到法定人数")

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationArbitration:
        action_name = validate_action_name(action, family="arbitration")
        target = arbitration_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("arbitration", action_name) and cleaned_reason is None:
            raise DomainValidationError("该仲裁动作必须填写原因")
        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if target in {
            AnnotationArbitrationStatus.RESOLVED_APPROVED,
            AnnotationArbitrationStatus.RESOLVED_REJECTED,
            AnnotationArbitrationStatus.RESOLVED_REWORK,
        }:
            self.assert_quorum()
            decision = {
                AnnotationArbitrationStatus.RESOLVED_APPROVED: ArbitrationDecision.APPROVE,
                AnnotationArbitrationStatus.RESOLVED_REJECTED: ArbitrationDecision.REJECT,
                AnnotationArbitrationStatus.RESOLVED_REWORK: ArbitrationDecision.REWORK,
            }[target]
            changes["final_decision"] = decision
            changes["resolved_at"] = occurred_at
            changes["reason"] = cleaned_reason
        if target is AnnotationArbitrationStatus.PENDING:
            changes["reason"] = cleaned_reason
        if target is AnnotationArbitrationStatus.CANCELLED:
            changes["reason"] = cleaned_reason
        return replace(self, **changes)

    def assert_terminal_immutable(self, candidate: AnnotationArbitration) -> None:
        if self.is_terminal and (
            self.submission_id != candidate.submission_id
            or self.review_id != candidate.review_id
            or self.arbiter_ids != candidate.arbiter_ids
            or self.quorum != candidate.quorum
            or self.status != candidate.status
            or self.final_decision != candidate.final_decision
            or [vote.to_payload() for vote in self.votes]
            != [vote.to_payload() for vote in candidate.votes]
            or self.reason != candidate.reason
        ):
            raise ResourceImmutableError("终态仲裁记录不可修改")


__all__ = ["AnnotationArbitration", "ArbitrationVote"]
