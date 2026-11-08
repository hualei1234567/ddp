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
from types import MappingProxyType

from app.domain.annotation.enums import (
    AnnotationAssignmentAction,
    AnnotationAssignmentStatus,
    AnnotationBatchAction,
    AnnotationBatchStatus,
    AnnotationSampleSetAction,
    AnnotationSampleSetStatus,
    AnnotationSchemaAction,
    AnnotationSchemaStatus,
    AnnotationTaskAction,
    AnnotationTaskStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

_TRANSITION = tuple[str, str]

_SCHEMA_RULES: Mapping[AnnotationSchemaStatus, Mapping[str, AnnotationSchemaStatus]] = (
    MappingProxyType(
        {
            AnnotationSchemaStatus.DRAFT: {
                AnnotationSchemaAction.SUBMIT_REVIEW.value: AnnotationSchemaStatus.PENDING_REVIEW,
            },
            AnnotationSchemaStatus.PENDING_REVIEW: {
                AnnotationSchemaAction.RETURN_DRAFT.value: AnnotationSchemaStatus.DRAFT,
                AnnotationSchemaAction.PUBLISH.value: AnnotationSchemaStatus.PUBLISHED,
            },
            AnnotationSchemaStatus.PUBLISHED: {
                AnnotationSchemaAction.DEPRECATE.value: AnnotationSchemaStatus.DEPRECATED,
                AnnotationSchemaAction.REVOKE.value: AnnotationSchemaStatus.REVOKED,
            },
            AnnotationSchemaStatus.DEPRECATED: {
                AnnotationSchemaAction.REVOKE.value: AnnotationSchemaStatus.REVOKED,
            },
            AnnotationSchemaStatus.REVOKED: {},
        }
    )
)

_SAMPLE_SET_RULES: Mapping[AnnotationSampleSetStatus, Mapping[str, AnnotationSampleSetStatus]] = (
    MappingProxyType(
        {
            AnnotationSampleSetStatus.DRAFT: {
                AnnotationSampleSetAction.PREVIEW.value: AnnotationSampleSetStatus.PREVIEW,
                AnnotationSampleSetAction.CANCEL.value: AnnotationSampleSetStatus.CANCELLED,
            },
            AnnotationSampleSetStatus.PREVIEW: {
                AnnotationSampleSetAction.SNAPSHOT.value: AnnotationSampleSetStatus.SNAPSHOTTED,
                AnnotationSampleSetAction.CANCEL.value: AnnotationSampleSetStatus.CANCELLED,
            },
            AnnotationSampleSetStatus.SNAPSHOTTED: {
                AnnotationSampleSetAction.CONSUME.value: AnnotationSampleSetStatus.CONSUMED,
                AnnotationSampleSetAction.CANCEL.value: AnnotationSampleSetStatus.CANCELLED,
            },
            AnnotationSampleSetStatus.CONSUMED: {},
            AnnotationSampleSetStatus.CANCELLED: {},
        }
    )
)

_TASK_RULES: Mapping[AnnotationTaskStatus, Mapping[str, AnnotationTaskStatus]] = MappingProxyType(
    {
        AnnotationTaskStatus.DRAFT: {
            AnnotationTaskAction.SUBMIT_REVIEW.value: AnnotationTaskStatus.PENDING_REVIEW,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.PENDING_REVIEW: {
            AnnotationTaskAction.RETURN_DRAFT.value: AnnotationTaskStatus.DRAFT,
            AnnotationTaskAction.APPROVE.value: AnnotationTaskStatus.APPROVED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.APPROVED: {
            AnnotationTaskAction.READY.value: AnnotationTaskStatus.READY,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.READY: {
            AnnotationTaskAction.ASSIGN.value: AnnotationTaskStatus.ASSIGNED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.ASSIGNED: {
            AnnotationTaskAction.START.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.IN_PROGRESS: {
            AnnotationTaskAction.PAUSE.value: AnnotationTaskStatus.PAUSED,
            AnnotationTaskAction.BLOCK.value: AnnotationTaskStatus.BLOCKED,
            AnnotationTaskAction.SUBMIT.value: AnnotationTaskStatus.SUBMITTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.PAUSED: {
            AnnotationTaskAction.RESUME.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.SUBMIT.value: AnnotationTaskStatus.SUBMITTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.BLOCKED: {
            AnnotationTaskAction.UNBLOCK.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.SUBMIT.value: AnnotationTaskStatus.SUBMITTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.SUBMITTED: {
            AnnotationTaskAction.SYSTEM_REVIEW.value: AnnotationTaskStatus.REVIEWING,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.REVIEWING: {
            AnnotationTaskAction.ACCEPT.value: AnnotationTaskStatus.ACCEPTED,
            AnnotationTaskAction.REJECT.value: AnnotationTaskStatus.REJECTED,
            AnnotationTaskAction.ESCALATE.value: AnnotationTaskStatus.ARBITRATION,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.ARBITRATION: {
            AnnotationTaskAction.RESOLVE_ACCEPTED.value: AnnotationTaskStatus.ACCEPTED,
            AnnotationTaskAction.RESOLVE_REJECTED.value: AnnotationTaskStatus.REJECTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.ACCEPTED: {
            AnnotationTaskAction.COMPLETE.value: AnnotationTaskStatus.COMPLETED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.REJECTED: {
            AnnotationTaskAction.REWORK.value: AnnotationTaskStatus.REWORK,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.REWORK: {
            AnnotationTaskAction.RESUME_WORK.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.COMPLETED: {
            AnnotationTaskAction.ARCHIVE.value: AnnotationTaskStatus.ARCHIVED,
        },
        AnnotationTaskStatus.CANCELLED: {},
        AnnotationTaskStatus.ARCHIVED: {},
    }
)

_BATCH_RULES: Mapping[AnnotationBatchStatus, Mapping[str, AnnotationBatchStatus]] = (
    MappingProxyType(
        {
            AnnotationBatchStatus.PLANNED: {
                AnnotationBatchAction.READY.value: AnnotationBatchStatus.READY,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.READY: {
                AnnotationBatchAction.ASSIGN.value: AnnotationBatchStatus.ASSIGNED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.ASSIGNED: {
                AnnotationBatchAction.START.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.IN_PROGRESS: {
                AnnotationBatchAction.PAUSE.value: AnnotationBatchStatus.PAUSED,
                AnnotationBatchAction.BLOCK.value: AnnotationBatchStatus.BLOCKED,
                AnnotationBatchAction.SUBMIT.value: AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.PAUSED: {
                AnnotationBatchAction.RESUME.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.SUBMIT.value: AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.BLOCKED: {
                AnnotationBatchAction.UNBLOCK.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.SUBMIT.value: AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.SUBMITTED: {
                AnnotationBatchAction.SYSTEM_REVIEW.value: AnnotationBatchStatus.REVIEWING,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.REVIEWING: {
                AnnotationBatchAction.ACCEPT.value: AnnotationBatchStatus.ACCEPTED,
                AnnotationBatchAction.REJECT.value: AnnotationBatchStatus.REJECTED,
                AnnotationBatchAction.ESCALATE.value: AnnotationBatchStatus.ARBITRATION,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.ARBITRATION: {
                AnnotationBatchAction.RESOLVE_ACCEPTED.value: AnnotationBatchStatus.ACCEPTED,
                AnnotationBatchAction.RESOLVE_REJECTED.value: AnnotationBatchStatus.REJECTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.REJECTED: {
                AnnotationBatchAction.REWORK.value: AnnotationBatchStatus.REWORK,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.REWORK: {
                AnnotationBatchAction.RESUME_WORK.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.ACCEPTED: {},
            AnnotationBatchStatus.CANCELLED: {},
        }
    )
)

_ASSIGNMENT_RULES: Mapping[AnnotationAssignmentStatus, Mapping[str, AnnotationAssignmentStatus]] = (
    MappingProxyType(
        {
            AnnotationAssignmentStatus.ASSIGNED: {
                AnnotationAssignmentAction.CLAIM.value: AnnotationAssignmentStatus.CLAIMED,
                AnnotationAssignmentAction.REVOKE.value: AnnotationAssignmentStatus.REVOKED,
                AnnotationAssignmentAction.SYSTEM_EXPIRE.value: AnnotationAssignmentStatus.EXPIRED,
            },
            AnnotationAssignmentStatus.CLAIMED: {
                AnnotationAssignmentAction.START.value: AnnotationAssignmentStatus.IN_PROGRESS,
                AnnotationAssignmentAction.RELEASE.value: AnnotationAssignmentStatus.ASSIGNED,
                AnnotationAssignmentAction.REVOKE.value: AnnotationAssignmentStatus.REVOKED,
                AnnotationAssignmentAction.SYSTEM_EXPIRE.value: AnnotationAssignmentStatus.EXPIRED,
            },
            AnnotationAssignmentStatus.IN_PROGRESS: {
                AnnotationAssignmentAction.SUBMIT.value: AnnotationAssignmentStatus.SUBMITTED,
                AnnotationAssignmentAction.REVOKE.value: AnnotationAssignmentStatus.REVOKED,
            },
            AnnotationAssignmentStatus.SUBMITTED: {
                AnnotationAssignmentAction.COMPLETE.value: AnnotationAssignmentStatus.COMPLETED,
                AnnotationAssignmentAction.REWORK.value: AnnotationAssignmentStatus.IN_PROGRESS,
            },
            AnnotationAssignmentStatus.COMPLETED: {},
            AnnotationAssignmentStatus.REVOKED: {},
            AnnotationAssignmentStatus.EXPIRED: {},
        }
    )
)

_REASON_REQUIRED: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "schema": frozenset(
            {
                AnnotationSchemaAction.RETURN_DRAFT.value,
                AnnotationSchemaAction.DEPRECATE.value,
                AnnotationSchemaAction.REVOKE.value,
            }
        ),
        "sample_set": frozenset({AnnotationSampleSetAction.CANCEL.value}),
        "task": frozenset(
            {
                AnnotationTaskAction.RETURN_DRAFT.value,
                AnnotationTaskAction.PAUSE.value,
                AnnotationTaskAction.RESUME.value,
                AnnotationTaskAction.BLOCK.value,
                AnnotationTaskAction.UNBLOCK.value,
                AnnotationTaskAction.REJECT.value,
                AnnotationTaskAction.ESCALATE.value,
                AnnotationTaskAction.RESOLVE_REJECTED.value,
                AnnotationTaskAction.REWORK.value,
                AnnotationTaskAction.CANCEL.value,
            }
        ),
        "batch": frozenset(
            {
                AnnotationBatchAction.PAUSE.value,
                AnnotationBatchAction.RESUME.value,
                AnnotationBatchAction.BLOCK.value,
                AnnotationBatchAction.UNBLOCK.value,
                AnnotationBatchAction.REJECT.value,
                AnnotationBatchAction.ESCALATE.value,
                AnnotationBatchAction.RESOLVE_REJECTED.value,
                AnnotationBatchAction.REWORK.value,
                AnnotationBatchAction.CANCEL.value,
            }
        ),
        "assignment": frozenset(
            {
                AnnotationAssignmentAction.RELEASE.value,
                AnnotationAssignmentAction.REVOKE.value,
                AnnotationAssignmentAction.REWORK.value,
            }
        ),
    }
)


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


def _terminal(rules: Mapping[object, Mapping[str, object]]) -> frozenset[str]:
    return frozenset(
        getattr(current, "value", str(current))
        for current, transitions in rules.items()
        if not transitions
    )
