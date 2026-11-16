"""AI 预标注建议领域单测：必填模型身份、置信度范围与确认一致性。

覆盖审计确认的三个缺口：
1. ``model_id`` / ``model_version`` / ``prompt_version`` 缺失时必须失败
   （历史上这些字段根本不存在，导致「哪批标签来自哪个模型版本」不可追溯）；
2. ``confidence`` 必须在 ``[0, 1]``（历史上 ``source=AI_ASSISTED`` 不给
   confidence 也能通过）；
3. ``human_confirmed`` 与人工决定字段必须自洽。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.annotation.ai_suggestion import AiSuggestion
from app.domain.annotation.errors import DomainValidationError, ResourceImmutableError

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def _suggestion(**overrides: object) -> AiSuggestion:
    payload: dict[str, object] = {
        "suggestion_id": uuid4(),
        "project_id": uuid4(),
        "annotation_id": uuid4(),
        "label_key": "action",
        "suggested_value": "PICK",
        "model_id": "ddp-action-vlm",
        "model_version": "1.2.0",
        "prompt_version": "prompt-7",
        "confidence": 0.82,
        "generated_at": NOW,
    }
    payload.update(overrides)
    return AiSuggestion(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    ["model_id", "model_version", "prompt_version"],
)
def test_model_identity_fields_are_required(field: str) -> None:
    """模型身份三列任一为空都必须失败——这是审计发现的核心缺口。"""
    with pytest.raises(DomainValidationError, match=field):
        _suggestion(**{field: ""})


@pytest.mark.parametrize("field", ["model_id", "model_version", "prompt_version"])
def test_model_identity_fields_reject_whitespace_only(field: str) -> None:
    with pytest.raises(DomainValidationError, match=field):
        _suggestion(**{field: "   "})


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 2, -1])
def test_confidence_must_be_within_unit_interval(confidence: float) -> None:
    with pytest.raises(DomainValidationError, match="confidence"):
        _suggestion(confidence=confidence)


@pytest.mark.parametrize("confidence", [0, 0.0, 1, 1.0, 0.5])
def test_confidence_accepts_boundaries(confidence: float) -> None:
    assert _suggestion(confidence=confidence).confidence == float(confidence)


def test_confidence_rejects_bool_and_text() -> None:
    """``True`` 是 ``int`` 的子类，若不显式排除会被当成 1.0 静默通过。"""
    with pytest.raises(DomainValidationError, match="confidence"):
        _suggestion(confidence=True)
    with pytest.raises(DomainValidationError, match="confidence"):
        _suggestion(confidence="0.9")


def test_confirmed_without_human_value_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="已确认"):
        _suggestion(human_confirmed=True, decided_by=uuid4(), decided_at=NOW)


def test_confirmed_requires_decided_by_and_decided_at() -> None:
    with pytest.raises(DomainValidationError, match="已确认"):
        _suggestion(human_confirmed=True, human_value="PLACE")
    with pytest.raises(DomainValidationError, match="已确认"):
        _suggestion(
            human_confirmed=True,
            human_value="PLACE",
            decided_at=NOW,
        )


def test_unconfirmed_must_not_carry_decision_fields() -> None:
    with pytest.raises(DomainValidationError, match="未确认"):
        _suggestion(human_value="PLACE")
    with pytest.raises(DomainValidationError, match="未确认"):
        _suggestion(decided_by=uuid4())


def test_confirmed_with_full_decision_fields_is_valid() -> None:
    decided_by = uuid4()
    suggestion = _suggestion(
        human_confirmed=True,
        human_value="PLACE",
        decided_by=decided_by,
        decided_at=NOW,
    )
    assert suggestion.is_decided
    assert suggestion.human_value == "PLACE"
    assert suggestion.decided_by == decided_by


def test_confirm_sets_human_value_and_rejects_second_confirmation() -> None:
    suggestion = _suggestion()
    actor_id = uuid4()
    confirmed = suggestion.confirm(actor_id=actor_id, human_value="PLACE", now=NOW)
    assert confirmed.human_confirmed
    assert confirmed.human_value == "PLACE"
    assert confirmed.decided_by == actor_id
    assert confirmed.decided_at == NOW
    # 建议值本身不被覆盖——这正是"AI vs 人工"可统计的前提。
    assert confirmed.suggested_value == "PICK"

    with pytest.raises(ResourceImmutableError):
        confirmed.confirm(actor_id=actor_id, human_value="PICK", now=NOW)


def test_suggested_value_rejects_non_json() -> None:
    with pytest.raises(DomainValidationError, match="suggested_value"):
        _suggestion(suggested_value=object())


def test_model_output_is_immutable_against_rewrite() -> None:
    suggestion = _suggestion()
    tampered = _suggestion(
        suggestion_id=suggestion.suggestion_id,
        project_id=suggestion.project_id,
        annotation_id=suggestion.annotation_id,
        model_version="9.9.9",
    )
    with pytest.raises(ResourceImmutableError):
        suggestion.assert_suggestion_immutable(tampered)
    # 只有人工决定字段变化时不算篡改模型输出。
    suggestion.assert_suggestion_immutable(
        _suggestion(
            suggestion_id=suggestion.suggestion_id,
            project_id=suggestion.project_id,
            annotation_id=suggestion.annotation_id,
            human_confirmed=True,
            human_value="PLACE",
            decided_by=uuid4(),
            decided_at=NOW,
        )
    )
