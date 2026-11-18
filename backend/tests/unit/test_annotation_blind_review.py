"""普通复核盲审策略的领域单测。

盲审开关复用任务 ``iaa_policy.blind_review``，不新增表/列；这里只钉住
策略解析与"谁能看到标签值"的单一权威规则。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.review_policy import ReviewPolicy
from app.domain.annotation.submission import LabelValue


def test_blind_review_defaults_to_off() -> None:
    policy = ReviewPolicy.from_mapping({})
    assert policy.blind_review is False


def test_blind_review_parses_true() -> None:
    policy = ReviewPolicy.from_mapping({"blind_review": True})
    assert policy.blind_review is True


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_blind_review_rejects_non_boolean(value: object) -> None:
    with pytest.raises(DomainValidationError):
        ReviewPolicy.from_mapping({"blind_review": value})


def test_hides_labels_only_from_non_annotator() -> None:
    annotator, reviewer = uuid4(), uuid4()
    policy = ReviewPolicy.from_mapping({"blind_review": True})

    assert policy.hides_labels_from(actor_id=reviewer, annotator_id=annotator) is True
    assert policy.hides_labels_from(actor_id=annotator, annotator_id=annotator) is False


def test_non_blind_policy_never_hides_labels() -> None:
    annotator, reviewer = uuid4(), uuid4()
    policy = ReviewPolicy.from_mapping({"blind_review": False})

    assert policy.hides_labels_from(actor_id=reviewer, annotator_id=annotator) is False


def test_redacted_label_payload_keeps_key_but_drops_value_and_metadata() -> None:
    label = LabelValue(
        label_key="action",
        value="PICK",
        confidence=0.9,
        comment="看起来像抓取",
    )

    assert label.redacted_payload() == {
        "label_key": "action",
        "value": None,
        "redacted": True,
    }
