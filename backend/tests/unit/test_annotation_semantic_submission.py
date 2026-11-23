"""V4.1 FR-016/017/018：三级语义层级包含与 OUTCOME_SPAN 提交值校验。

先写测试后实现（仓库规范）：本文件先于实现编写，红转绿。

- FR-016/017：L2⊆L1、L3⊆L2 的提交强校验；子级片段必须完整落在某一同
  Episode 的父级片段内，子级存在而父级为空同样违规。
- FR-018：OUTCOME_SPAN 标签值的区间/枚举/失败原因校验。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.annotation.enums import AnnotationLabelType, AnnotationLevel
from app.domain.annotation.errors import LabelValueInvalidError
from app.domain.annotation.schema import LabelDefinition
from app.domain.annotation.submission import (
    AnnotationRecord,
    validate_record_labels,
    validate_submission_records,
)


def make_record(
    *labels: dict[str, object],
    episode_id: UUID | None = None,
) -> AnnotationRecord:
    return AnnotationRecord(
        annotation_id=uuid4(),
        annotation_lineage_id=uuid4(),
        annotation_version=1,
        project_id=uuid4(),
        submission_id=uuid4(),
        episode_id=episode_id or uuid4(),
        episode_version=1,
        task_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        labels=list(labels),
    )


def semantic_definitions() -> tuple[LabelDefinition, ...]:
    """对齐 semantic-hierarchy 预设的三级语义时间片段标签。

    ``required`` 全部为 False：本文件只测层级包含与值形状，不测提交完整性。
    """
    return (
        LabelDefinition(
            label_key="task_span",
            label_type=AnnotationLabelType.TIME_RANGE,
            required=False,
            level=AnnotationLevel.SEMANTIC_TASK,
            value_spec={"min_duration_ns": 1},
        ),
        LabelDefinition(
            label_key="subtask_span",
            label_type=AnnotationLabelType.TIME_RANGE,
            required=False,
            level=AnnotationLevel.SEMANTIC_SUBTASK,
            value_spec={"min_duration_ns": 1},
        ),
        LabelDefinition(
            label_key="event_span",
            label_type=AnnotationLabelType.TIME_RANGE,
            required=False,
            level=AnnotationLevel.SEMANTIC_EVENT,
            value_spec={"min_duration_ns": 1},
        ),
    )


def span(start: int, end: int) -> dict[str, object]:
    return {"start_time_ns": start, "end_time_ns": end, "time_domain": "UTC"}


# ---- FR-016/017：三级语义层级包含 ----


def test_l1_without_l2_l3_passes() -> None:
    record = make_record({"label_key": "task_span", "value": span(0, 100)})
    # 不抛异常即为通过
    validate_record_labels(record, semantic_definitions())


def test_full_hierarchy_within_bounds_passes() -> None:
    record = make_record(
        {"label_key": "task_span", "value": span(0, 100)},
        {"label_key": "subtask_span", "value": span(10, 90)},
        {"label_key": "event_span", "value": span(20, 30)},
    )
    # 不抛异常即为通过
    validate_record_labels(record, semantic_definitions())


def test_child_boundary_touching_parent_passes() -> None:
    """子片段端点与父片段端点相等仍算完整包含（>= / <=）。"""
    record = make_record(
        {"label_key": "task_span", "value": span(0, 100)},
        {"label_key": "subtask_span", "value": span(0, 100)},
    )
    # 不抛异常即为通过
    validate_record_labels(record, semantic_definitions())


def test_l2_outside_l1_fails_and_names_label_key() -> None:
    record = make_record(
        {"label_key": "task_span", "value": span(0, 100)},
        {"label_key": "subtask_span", "value": span(50, 120)},  # end 越过 L1
    )
    with pytest.raises(LabelValueInvalidError, match="subtask_span.*未落在任何 L1 片段内"):
        validate_record_labels(record, semantic_definitions())


def test_l3_outside_l2_fails_and_names_label_key() -> None:
    record = make_record(
        {"label_key": "task_span", "value": span(0, 100)},
        {"label_key": "subtask_span", "value": span(10, 90)},
        {"label_key": "event_span", "value": span(85, 120)},  # end 越过 L2
    )
    with pytest.raises(LabelValueInvalidError, match="event_span.*未落在任何 L2 片段内"):
        validate_record_labels(record, semantic_definitions())


def test_l2_without_any_l1_fails() -> None:
    """L2 存在但没有任何 L1 片段：反向违规。"""
    record = make_record({"label_key": "subtask_span", "value": span(10, 20)})
    with pytest.raises(LabelValueInvalidError, match="subtask_span.*未落在任何 L1 片段内"):
        validate_record_labels(record, semantic_definitions())


def test_l3_without_any_l2_fails() -> None:
    record = make_record(
        {"label_key": "task_span", "value": span(0, 100)},
        {"label_key": "event_span", "value": span(20, 30)},
    )
    with pytest.raises(LabelValueInvalidError, match="event_span.*未落在任何 L2 片段内"):
        validate_record_labels(record, semantic_definitions())


def test_multiple_l1_parents_any_containment_passes() -> None:
    """多个同级父片段：子片段落在任意一个内即可。"""
    definitions = semantic_definitions() + (
        LabelDefinition(
            label_key="task_span_b",
            label_type=AnnotationLabelType.TIME_RANGE,
            required=False,
            level=AnnotationLevel.SEMANTIC_TASK,
            value_spec={"min_duration_ns": 1},
        ),
    )
    record = make_record(
        {"label_key": "task_span", "value": span(0, 50)},
        {"label_key": "task_span_b", "value": span(60, 100)},
        {"label_key": "subtask_span", "value": span(60, 90)},  # 只落在第二个 L1 内
    )
    # 不抛异常即为通过
    validate_record_labels(record, definitions)


def test_cross_record_l2_within_l1_same_episode_passes() -> None:
    """L1 与 L2 分布在不同 record（同 Episode）：提交聚合视角包含成立。"""
    episode = uuid4()
    records = (
        make_record({"label_key": "task_span", "value": span(0, 100)}, episode_id=episode),
        make_record({"label_key": "subtask_span", "value": span(10, 90)}, episode_id=episode),
    )
    # 不抛异常即为通过
    validate_submission_records(records, semantic_definitions())


def test_cross_record_l2_outside_l1_same_episode_fails() -> None:
    episode = uuid4()
    records = (
        make_record({"label_key": "task_span", "value": span(0, 100)}, episode_id=episode),
        make_record({"label_key": "subtask_span", "value": span(150, 200)}, episode_id=episode),
    )
    with pytest.raises(LabelValueInvalidError, match="subtask_span.*未落在任何 L1 片段内"):
        validate_submission_records(records, semantic_definitions())


def test_cross_episode_hierarchy_does_not_leak() -> None:
    """不同 Episode 的 L1 不能"救"另一 Episode 的 L2：层级归属必须同源。"""
    episode_a, episode_b = uuid4(), uuid4()
    records = (
        make_record({"label_key": "task_span", "value": span(0, 100)}, episode_id=episode_a),
        make_record({"label_key": "subtask_span", "value": span(10, 90)}, episode_id=episode_b),
    )
    with pytest.raises(LabelValueInvalidError, match="subtask_span.*未落在任何 L1 片段内"):
        validate_submission_records(records, semantic_definitions())


def test_outcome_span_does_not_participate_in_hierarchy() -> None:
    """FR-016/017 只约束 TIME_RANGE/ACTION_SEGMENT 的语义层级标签。

    OUTCOME_SPAN 挂在语义层级但不参与父子包含；其所在层级无父级片段也不违规。
    """
    definitions = semantic_definitions() + (
        LabelDefinition(
            label_key="outcome_span",
            label_type=AnnotationLabelType.OUTCOME_SPAN,
            required=False,
            level=AnnotationLevel.SEMANTIC_SUBTASK,
        ),
    )
    value = {"start_time_ns": 0, "end_time_ns": 50, "outcome": "SUCCESS"}
    record = make_record({"label_key": "outcome_span", "value": value})
    # 不抛异常即为通过
    validate_record_labels(record, definitions)


# ---- FR-018：OUTCOME_SPAN 提交值校验 ----


def outcome_definition(**value_spec: object) -> LabelDefinition:
    return LabelDefinition(
        label_key="outcome_span",
        label_type=AnnotationLabelType.OUTCOME_SPAN,
        required=False,
        level=AnnotationLevel.SEMANTIC_TASK,
        value_spec=dict(value_spec),
    )


def outcome_value(start: int = 0, end: int = 50, **extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "start_time_ns": start,
        "end_time_ns": end,
        "outcome": "SUCCESS",
    }
    value.update(extra)
    return value


def test_outcome_span_valid_value_passes() -> None:
    record = make_record({"label_key": "outcome_span", "value": outcome_value()})
    # 不抛异常即为通过
    validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_failure_with_category_and_detail_passes() -> None:
    value = outcome_value(
        outcome="FAILURE",
        error_category="manipulation",
        error_detail="抓取打滑",
    )
    record = make_record({"label_key": "outcome_span", "value": value})
    # 不抛异常即为通过
    validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_success_with_redundant_category_passes() -> None:
    """outcome != FAILURE 时带合法 error_category：冗余信息宽容，不报错。"""
    value = outcome_value(outcome="SUCCESS", error_category="planning")
    record = make_record({"label_key": "outcome_span", "value": value})
    # 不抛异常即为通过
    validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_illegal_outcome() -> None:
    value = outcome_value(outcome="PERFECT")
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="outcome 取值非法"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_outcome_outside_narrowed_spec() -> None:
    """value_spec 显式收窄 outcome_values 后，全集取值也被拒绝。"""
    definitions = (outcome_definition(outcome_values=["SUCCESS", "FAILURE"]),)
    value = outcome_value(outcome="PARTIAL_SUCCESS")
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="outcome 取值非法"):
        validate_record_labels(record, definitions)


def test_outcome_span_failure_requires_error_category() -> None:
    value = outcome_value(outcome="FAILURE")
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="必须提供 error_category"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_illegal_error_category() -> None:
    value = outcome_value(outcome="FAILURE", error_category="magic")
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="error_category 取值非法"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_non_string_error_detail() -> None:
    value = outcome_value(outcome="FAILURE", error_category="planning", error_detail=123)
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="error_detail 必须是字符串"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_non_mapping_value() -> None:
    record = make_record({"label_key": "outcome_span", "value": "0-50 SUCCESS"})
    with pytest.raises(LabelValueInvalidError, match="OUTCOME_SPAN 标签值必须是对象"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_missing_time_bounds() -> None:
    value = {"outcome": "SUCCESS"}
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="start_time_ns"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_inverted_time_bounds() -> None:
    value = outcome_value(start=50, end=50)  # start >= end
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="OUTCOME_SPAN 时间区间非法"):
        validate_record_labels(record, (outcome_definition(),))


def test_outcome_span_rejects_unknown_fields() -> None:
    value = outcome_value(reason="why")
    record = make_record({"label_key": "outcome_span", "value": value})
    with pytest.raises(LabelValueInvalidError, match="未知字段"):
        validate_record_labels(record, (outcome_definition(),))
