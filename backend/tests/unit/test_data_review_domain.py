"""导入审核领域测试：职责分离、终态不可再裁决、拒绝必须写原因。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.datasources.review import (
    DataReview,
    DomainValidationError,
    ReviewDecision,
)

_NOW = datetime(2026, 11, 15, tzinfo=UTC)


def _review(submitted_by=None, **overrides: object) -> DataReview:
    base: dict[str, object] = {
        "review_id": uuid4(),
        "project_id": uuid4(),
        "import_id": uuid4(),
        "status": "PENDING",
        "decision": None,
        "reason": None,
        "reviewer_id": None,
        "submitted_by": submitted_by or uuid4(),
        "created_at": _NOW,
    }
    base.update(overrides)
    return DataReview(**base)  # type: ignore[arg-type]


def test_reviewer_cannot_be_submitter() -> None:
    """职责分离：自己审自己是合规问题，不是风格问题。"""
    submitter = uuid4()
    review = _review(submitted_by=submitter)

    with pytest.raises(DomainValidationError, match="职责分离"):
        review.decide(
            decision=ReviewDecision.ACCEPT,
            reviewer_id=submitter,
            reason=None,
            now=_NOW,
        )


def test_accept_approves_without_reason() -> None:
    review = _review()
    reviewer = uuid4()

    decided = review.decide(
        decision=ReviewDecision.ACCEPT, reviewer_id=reviewer, reason=None, now=_NOW
    )

    assert decided.status == "APPROVED"
    assert decided.decision is ReviewDecision.ACCEPT
    assert decided.reviewer_id == reviewer
    assert decided.decided_at == _NOW


@pytest.mark.parametrize("decision", [ReviewDecision.REJECT, ReviewDecision.NEEDS_FIX])
def test_reject_needs_reason(decision: ReviewDecision) -> None:
    """被拒方需要知道改什么，否则审核没有信息量。"""
    review = _review()

    with pytest.raises(DomainValidationError, match="必须填写原因"):
        review.decide(decision=decision, reviewer_id=uuid4(), reason=None, now=_NOW)


@pytest.mark.parametrize("decision", [ReviewDecision.REJECT, ReviewDecision.NEEDS_FIX])
def test_reject_with_reason_is_accepted(decision: ReviewDecision) -> None:
    review = _review()

    decided = review.decide(decision=decision, reviewer_id=uuid4(), reason="机位缺失", now=_NOW)

    assert decided.status == "REJECTED"
    assert decided.reason == "机位缺失"


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED"])
def test_terminal_review_cannot_be_decided_again(status: str) -> None:
    review = _review(status=status, decision="ACCEPT")

    with pytest.raises(DomainValidationError, match="已结束"):
        review.decide(decision=ReviewDecision.ACCEPT, reviewer_id=uuid4(), reason=None, now=_NOW)


def test_row_version_must_be_positive() -> None:
    with pytest.raises(DomainValidationError, match="row_version"):
        _review(row_version=0)


def test_overlong_reason_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="超过"):
        _review(reason="x" * 2001)
