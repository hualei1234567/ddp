"""双标任务策略与一致性门槛的领域单测。

指标实现复用 iaa.py；本文件只验证策略解析与门槛结论，不重复实现算法。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.annotation.enums import IAAMetric, IAAObservationSource
from app.domain.annotation.errors import DomainValidationError, IAASourceForbiddenError
from app.domain.annotation.iaa import IAAObservation, evaluate_double_annotation
from app.domain.annotation.review_policy import ReviewMode, ReviewPolicy


def observation(episode_id, annotator_id, value: str) -> IAAObservation:
    return IAAObservation(
        episode_id=episode_id,
        annotator_id=annotator_id,
        label_values={"action": value},
    )


def test_review_policy_defaults_to_single() -> None:
    policy = ReviewPolicy.from_mapping({})
    assert policy.review_mode is ReviewMode.SINGLE
    assert policy.auto_approve is False


def test_review_policy_parses_double_threshold_and_auto_approve() -> None:
    policy = ReviewPolicy.from_mapping(
        {
            "review_mode": "double",
            "agreement_threshold": 0.75,
            "agreement_metric": "COHEN_KAPPA",
            "auto_approve": True,
        }
    )
    assert policy.review_mode is ReviewMode.DOUBLE
    assert policy.agreement_threshold == 0.75
    assert policy.agreement_metric is IAAMetric.COHEN_KAPPA
    assert policy.auto_approve is True


@pytest.mark.parametrize(
    "policy",
    [
        {"review_mode": "TRIPLE"},
        {"agreement_threshold": 1.5},
        {"agreement_threshold": True},
        {"auto_approve": "yes"},
        {"auto_approve": True},
    ],
)
def test_review_policy_rejects_invalid_values(policy: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        ReviewPolicy.from_mapping(policy)


def test_missing_second_annotator_is_pending_not_failure() -> None:
    episode_id = uuid4()
    result = evaluate_double_annotation(
        [observation(episode_id, uuid4(), "PICK")],
        threshold=0.8,
    )
    assert result.status == "PENDING_SECOND_ANNOTATOR"
    assert result.passed is None
    assert result.double_episode_count == 0


def test_two_annotators_agree_passes() -> None:
    episode_id = uuid4()
    result = evaluate_double_annotation(
        [
            observation(episode_id, uuid4(), "PICK"),
            observation(episode_id, uuid4(), "PICK"),
        ],
        threshold=0.8,
    )
    assert result.status == "PASSED"
    assert result.passed is True
    assert result.value == 1.0
    assert result.annotator_count == 2


def test_two_annotators_disagree_fails() -> None:
    episode_id = uuid4()
    result = evaluate_double_annotation(
        [
            observation(episode_id, uuid4(), "PICK"),
            observation(episode_id, uuid4(), "PLACE"),
        ],
        threshold=0.8,
    )
    assert result.status == "FAILED"
    assert result.passed is False
    assert result.value == 0.0


def test_partial_coverage_does_not_pass() -> None:
    first, second = uuid4(), uuid4()
    result = evaluate_double_annotation(
        [
            observation(first, uuid4(), "PICK"),
            observation(first, uuid4(), "PICK"),
            observation(second, uuid4(), "PICK"),
        ],
        threshold=0.8,
    )
    assert result.status == "PARTIAL"
    assert result.passed is None
    assert result.double_episode_count == 1
    assert result.missing_episode_ids == (second,)


def test_gold_set_source_is_rejected() -> None:
    episode_id = uuid4()
    with pytest.raises(IAASourceForbiddenError):
        evaluate_double_annotation(
            [
                IAAObservation(
                    episode_id=episode_id,
                    annotator_id=uuid4(),
                    label_values={"action": "PICK"},
                    source=IAAObservationSource.GOLD_SET,
                )
            ]
        )


def test_unsupported_metric_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        evaluate_double_annotation([], metric=IAAMetric.GOLD_ACCURACY)
