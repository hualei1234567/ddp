# ruff: noqa: E501
"""标注域状态机。

迁移表直接对应阶段 3 契约草案 ``state-machines.v1.json``：
- 只允许显式列出的具名动作；
- 未知状态/动作一律拒绝；
- 契约为 COMPLETED/DEPRECATED 等“名义终态”保留了归档或撤销出边，
  本实现以迁移表为准：只有无出边的状态才是真正的终态。
- 需要原因的动作由实体层进一步校验。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.annotation.enums import (
    AnnotationArbitrationStatus,
    AnnotationAssignmentStatus,
    AnnotationBatchStatus,
    AnnotationRecordStatus,
    AnnotationReviewStatus,
    AnnotationSampleSetStatus,
    AnnotationSchemaStatus,
    AnnotationSubmissionStatus,
    AnnotationTaskStatus,
    GoldSetStatus,
    IAAReportStatus,
    QualitySampleStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

# 规则表拆到 state_rules.py（纯数据）；这里重新导入，保持本模块的既有用法不变。
from app.domain.annotation.state_rules import (  # noqa: E402
    _ARBITRATION_RULES,
    _ASSIGNMENT_RULES,
    _BATCH_RULES,
    _GOLD_SET_RULES,
    _IAA_REPORT_RULES,
    _QUALITY_SAMPLE_RULES,
    _REASON_REQUIRED,
    _RECORD_RULES,
    _REVIEW_RULES,
    _SAMPLE_SET_RULES,
    _SCHEMA_RULES,
    _SUBMISSION_RULES,
    _TASK_RULES,
)

_TRANSITION = tuple[str, str]


def _target(
    rules: Mapping[object, Mapping[str, object]],
    entity: str,
    current: object,
    action: str,
) -> object:
    target = rules.get(current, {}).get(action)
    if target is None:
        current_text = getattr(current, "value", current)
        raise InvalidStateTransitionError(entity, current_text, action)
    return target


def _pairs(rules: Mapping[object, Mapping[str, object]]) -> frozenset[_TRANSITION]:
    return frozenset(
        (getattr(current, "value", str(current)), getattr(target, "value", str(target)))
        for current, transitions in rules.items()
        for target in transitions.values()
    )


def _allowed(rules: Mapping[object, Mapping[str, object]], current: object) -> frozenset[str]:
    return frozenset(rules.get(current, {}))


def schema_target(current: AnnotationSchemaStatus, action: str) -> AnnotationSchemaStatus:
    return _target(_SCHEMA_RULES, "标注 Schema", current, action)  # type: ignore[return-value]


def sample_set_target(
    current: AnnotationSampleSetStatus,
    action: str,
) -> AnnotationSampleSetStatus:
    return _target(  # type: ignore[return-value]
        _SAMPLE_SET_RULES,
        "标注样本集",
        current,
        action,
    )


def task_target(current: AnnotationTaskStatus, action: str) -> AnnotationTaskStatus:
    return _target(_TASK_RULES, "标注任务", current, action)  # type: ignore[return-value]


def batch_target(current: AnnotationBatchStatus, action: str) -> AnnotationBatchStatus:
    return _target(_BATCH_RULES, "标注批次", current, action)  # type: ignore[return-value]


def assignment_target(
    current: AnnotationAssignmentStatus,
    action: str,
) -> AnnotationAssignmentStatus:
    return _target(  # type: ignore[return-value]
        _ASSIGNMENT_RULES,
        "标注分配",
        current,
        action,
    )


def submission_target(
    current: AnnotationSubmissionStatus,
    action: str,
) -> AnnotationSubmissionStatus:
    return _target(  # type: ignore[return-value]
        _SUBMISSION_RULES,
        "标注提交",
        current,
        action,
    )


def record_target(
    current: AnnotationRecordStatus,
    action: str,
) -> AnnotationRecordStatus:
    return _target(_RECORD_RULES, "标注记录", current, action)  # type: ignore[return-value]


def review_target(
    current: AnnotationReviewStatus,
    action: str,
) -> AnnotationReviewStatus:
    return _target(_REVIEW_RULES, "标注复核", current, action)  # type: ignore[return-value]


def arbitration_target(
    current: AnnotationArbitrationStatus,
    action: str,
) -> AnnotationArbitrationStatus:
    return _target(  # type: ignore[return-value]
        _ARBITRATION_RULES,
        "标注仲裁",
        current,
        action,
    )


def gold_set_target(current: GoldSetStatus, action: str) -> GoldSetStatus:
    return _target(_GOLD_SET_RULES, "黄金集", current, action)  # type: ignore[return-value]


def quality_sample_target(
    current: QualitySampleStatus,
    action: str,
) -> QualitySampleStatus:
    return _target(  # type: ignore[return-value]
        _QUALITY_SAMPLE_RULES,
        "质检抽检",
        current,
        action,
    )


def iaa_report_target(current: IAAReportStatus, action: str) -> IAAReportStatus:
    return _target(_IAA_REPORT_RULES, "IAA 报告", current, action)  # type: ignore[return-value]


def allowed_schema_actions(current: AnnotationSchemaStatus) -> frozenset[str]:
    return _allowed(_SCHEMA_RULES, current)


def allowed_sample_set_actions(current: AnnotationSampleSetStatus) -> frozenset[str]:
    return _allowed(_SAMPLE_SET_RULES, current)


def allowed_task_actions(current: AnnotationTaskStatus) -> frozenset[str]:
    return _allowed(_TASK_RULES, current)


def allowed_batch_actions(current: AnnotationBatchStatus) -> frozenset[str]:
    return _allowed(_BATCH_RULES, current)


def allowed_assignment_actions(current: AnnotationAssignmentStatus) -> frozenset[str]:
    return _allowed(_ASSIGNMENT_RULES, current)


def allowed_submission_actions(current: AnnotationSubmissionStatus) -> frozenset[str]:
    return _allowed(_SUBMISSION_RULES, current)


def allowed_record_actions(current: AnnotationRecordStatus) -> frozenset[str]:
    return _allowed(_RECORD_RULES, current)


def allowed_review_actions(current: AnnotationReviewStatus) -> frozenset[str]:
    return _allowed(_REVIEW_RULES, current)


def allowed_arbitration_actions(current: AnnotationArbitrationStatus) -> frozenset[str]:
    return _allowed(_ARBITRATION_RULES, current)


def allowed_gold_set_actions(current: GoldSetStatus) -> frozenset[str]:
    return _allowed(_GOLD_SET_RULES, current)


def allowed_quality_sample_actions(current: QualitySampleStatus) -> frozenset[str]:
    return _allowed(_QUALITY_SAMPLE_RULES, current)


def allowed_iaa_report_actions(current: IAAReportStatus) -> frozenset[str]:
    return _allowed(_IAA_REPORT_RULES, current)


def assert_schema_action(
    current: AnnotationSchemaStatus,
    action: str,
) -> AnnotationSchemaStatus:
    return schema_target(current, action)


def assert_sample_set_action(
    current: AnnotationSampleSetStatus,
    action: str,
) -> AnnotationSampleSetStatus:
    return sample_set_target(current, action)


def assert_task_action(current: AnnotationTaskStatus, action: str) -> AnnotationTaskStatus:
    return task_target(current, action)


def assert_batch_action(current: AnnotationBatchStatus, action: str) -> AnnotationBatchStatus:
    return batch_target(current, action)


def assert_assignment_action(
    current: AnnotationAssignmentStatus,
    action: str,
) -> AnnotationAssignmentStatus:
    return assignment_target(current, action)


def assert_submission_action(
    current: AnnotationSubmissionStatus,
    action: str,
) -> AnnotationSubmissionStatus:
    return submission_target(current, action)


def assert_record_action(
    current: AnnotationRecordStatus,
    action: str,
) -> AnnotationRecordStatus:
    return record_target(current, action)


def assert_review_action(
    current: AnnotationReviewStatus,
    action: str,
) -> AnnotationReviewStatus:
    return review_target(current, action)


def assert_arbitration_action(
    current: AnnotationArbitrationStatus,
    action: str,
) -> AnnotationArbitrationStatus:
    return arbitration_target(current, action)


def assert_gold_set_action(current: GoldSetStatus, action: str) -> GoldSetStatus:
    return gold_set_target(current, action)


def assert_quality_sample_action(
    current: QualitySampleStatus,
    action: str,
) -> QualitySampleStatus:
    return quality_sample_target(current, action)


def assert_iaa_report_action(current: IAAReportStatus, action: str) -> IAAReportStatus:
    return iaa_report_target(current, action)


def validate_action_name(action: object, *, family: str) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = _REASON_REQUIRED.get(family)
    if known is None:
        raise DomainValidationError("未知状态机")
    rules = {
        "schema": _SCHEMA_RULES,
        "sample_set": _SAMPLE_SET_RULES,
        "task": _TASK_RULES,
        "batch": _BATCH_RULES,
        "assignment": _ASSIGNMENT_RULES,
        "submission": _SUBMISSION_RULES,
        "record": _RECORD_RULES,
        "review": _REVIEW_RULES,
        "arbitration": _ARBITRATION_RULES,
        "gold_set": _GOLD_SET_RULES,
        "quality_sample": _QUALITY_SAMPLE_RULES,
        "iaa_report": _IAA_REPORT_RULES,
    }[family]
    if value not in {name for transitions in rules.values() for name in transitions}:
        raise DomainValidationError("未知状态动作")
    return value


def action_requires_reason(family: str, action: str) -> bool:
    return action in _REASON_REQUIRED.get(family, frozenset())


def schema_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_SCHEMA_RULES)


def sample_set_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_SAMPLE_SET_RULES)


def task_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_TASK_RULES)


def batch_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_BATCH_RULES)


def assignment_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_ASSIGNMENT_RULES)


def submission_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_SUBMISSION_RULES)


def record_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_RECORD_RULES)


def review_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_REVIEW_RULES)


def arbitration_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_ARBITRATION_RULES)


def gold_set_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_GOLD_SET_RULES)


def quality_sample_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_QUALITY_SAMPLE_RULES)


def iaa_report_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_IAA_REPORT_RULES)


def schema_terminal_states() -> frozenset[str]:
    return _terminal(_SCHEMA_RULES)


def sample_set_terminal_states() -> frozenset[str]:
    return _terminal(_SAMPLE_SET_RULES)


def task_terminal_states() -> frozenset[str]:
    return _terminal(_TASK_RULES)


def batch_terminal_states() -> frozenset[str]:
    return _terminal(_BATCH_RULES)


def assignment_terminal_states() -> frozenset[str]:
    return _terminal(_ASSIGNMENT_RULES)


def submission_terminal_states() -> frozenset[str]:
    return _terminal(_SUBMISSION_RULES)


def record_terminal_states() -> frozenset[str]:
    return _terminal(_RECORD_RULES)


def review_terminal_states() -> frozenset[str]:
    return _terminal(_REVIEW_RULES)


def arbitration_terminal_states() -> frozenset[str]:
    return _terminal(_ARBITRATION_RULES)


def gold_set_terminal_states() -> frozenset[str]:
    return _terminal(_GOLD_SET_RULES)


def quality_sample_terminal_states() -> frozenset[str]:
    return _terminal(_QUALITY_SAMPLE_RULES)


def iaa_report_terminal_states() -> frozenset[str]:
    return _terminal(_IAA_REPORT_RULES)


def _terminal(rules: Mapping[object, Mapping[str, object]]) -> frozenset[str]:
    return frozenset(
        getattr(current, "value", str(current))
        for current, transitions in rules.items()
        if not transitions
    )
