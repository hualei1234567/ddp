"""V31-047 三级语义分层与结果帧标注：领域单测。"""

from __future__ import annotations

import pytest
from app.domain.annotation.enums import (
    AnnotationLabelType,
    AnnotationLevel,
    LabelVisibility,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.schema import (
    OUTCOME_ERROR_CATEGORIES,
    OUTCOME_VALUES,
    LabelDefinition,
)
from app.domain.annotation.schema_presets import default_registry

# ---- 枚举与常量 ----


def test_semantic_levels_registered() -> None:
    values = {item.value for item in AnnotationLevel}
    assert {"SEMANTIC_TASK", "SEMANTIC_SUBTASK", "SEMANTIC_EVENT"} <= values


def test_outcome_constants() -> None:
    assert OUTCOME_VALUES == ("SUCCESS", "PARTIAL_SUCCESS", "FAILURE", "UNKNOWN")
    assert OUTCOME_ERROR_CATEGORIES == (
        "perception",
        "planning",
        "manipulation",
        "sensing",
        "recording",
    )


# ---- OUTCOME_SPAN 值规范校验 ----


def make_outcome_label(**value_spec: object) -> LabelDefinition:
    return LabelDefinition(
        label_key="outcome_span",
        label_type=AnnotationLabelType.OUTCOME_SPAN,
        required=True,
        level=AnnotationLevel.SEMANTIC_TASK,
        visibility=LabelVisibility.ANNOTATOR,
        value_spec=dict(value_spec),
    )


def test_outcome_span_with_default_spec_passes() -> None:
    label = make_outcome_label()
    assert label.label_type is AnnotationLabelType.OUTCOME_SPAN


def test_outcome_span_with_narrowed_values_passes() -> None:
    label = make_outcome_label(
        outcome_values=["SUCCESS", "FAILURE"],
        error_categories=["manipulation", "sensing"],
    )
    assert label.value_spec["outcome_values"] == ["SUCCESS", "FAILURE"]


def test_outcome_span_rejects_bad_outcome() -> None:
    with pytest.raises(DomainValidationError, match="outcome_values 非法"):
        make_outcome_label(outcome_values=["SUCCESS", "PERFECT"])


def test_outcome_span_rejects_bad_category() -> None:
    with pytest.raises(DomainValidationError, match="error_categories 非法"):
        make_outcome_label(error_categories=["magic"])


def test_outcome_span_rejects_empty_outcomes() -> None:
    with pytest.raises(DomainValidationError, match="非空列表"):
        make_outcome_label(outcome_values=[])


# ---- semantic-hierarchy 预设 ----


def test_semantic_hierarchy_preset_registered() -> None:
    preset = default_registry().get("semantic-hierarchy")
    assert {item.value for item in preset.annotation_levels} == {
        "SEMANTIC_TASK",
        "SEMANTIC_SUBTASK",
        "SEMANTIC_EVENT",
    }


def test_preset_labels_cover_three_levels_and_outcome() -> None:
    preset = default_registry().get("semantic-hierarchy")
    by_key = {item.label_key: item for item in preset.labels}
    assert by_key["task_instruction"].level is AnnotationLevel.SEMANTIC_TASK
    assert by_key["subtask_span"].level is AnnotationLevel.SEMANTIC_SUBTASK
    assert by_key["event_span"].level is AnnotationLevel.SEMANTIC_EVENT
    outcome = by_key["outcome_span"]
    assert outcome.label_type is AnnotationLabelType.OUTCOME_SPAN
    assert outcome.required is True
    # 失败细节只给复核者
    assert by_key["outcome_error_detail"].visibility is LabelVisibility.REVIEWER_ONLY


def test_preset_registry_still_serves_old_presets() -> None:
    registry = default_registry()
    for key in ("action", "segment", "object", "attributes", "quality", "scene", "robot-state"):
        registry.get(key)  # 不抛即通过
