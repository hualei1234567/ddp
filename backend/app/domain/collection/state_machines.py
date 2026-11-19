"""采集任务与批次状态机。

迁移表直接对应阶段 2 契约草案 ``state-machines.v1.json``：
- 只允许表中显式列出的具名动作；
- 终态没有出边；
- 未知状态/动作一律拒绝；
- 需要原因的动作由实体层进一步校验。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.collection.enums import (
    CaptureSessionAction,
    CaptureSessionStatus,
    CollectionBatchAction,
    CollectionBatchStatus,
    CollectionTaskAction,
    CollectionTaskStatus,
)
from app.domain.collection.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

TASK_TERMINAL: frozenset[CollectionTaskStatus] = frozenset(
    {CollectionTaskStatus.COMPLETED, CollectionTaskStatus.CANCELLED}
)
BATCH_TERMINAL: frozenset[CollectionBatchStatus] = frozenset(
    {CollectionBatchStatus.ACCEPTED, CollectionBatchStatus.CANCELLED}
)

# 每个当前状态允许的“动作 → 目标状态”。
_TASK_RULES: Mapping[CollectionTaskStatus, Mapping[str, CollectionTaskStatus]] = MappingProxyType(
    {
        CollectionTaskStatus.DRAFT: {
            CollectionTaskAction.SUBMIT_REVIEW.value: CollectionTaskStatus.PENDING_REVIEW,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.PENDING_REVIEW: {
            CollectionTaskAction.RETURN_DRAFT.value: CollectionTaskStatus.DRAFT,
            CollectionTaskAction.APPROVE.value: CollectionTaskStatus.APPROVED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.APPROVED: {
            CollectionTaskAction.READY.value: CollectionTaskStatus.READY,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.READY: {
            CollectionTaskAction.ASSIGN.value: CollectionTaskStatus.ASSIGNED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.ASSIGNED: {
            CollectionTaskAction.START.value: CollectionTaskStatus.IN_PROGRESS,
            CollectionTaskAction.SUBMIT.value: CollectionTaskStatus.SUBMITTED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.IN_PROGRESS: {
            CollectionTaskAction.PAUSE.value: CollectionTaskStatus.PAUSED,
            CollectionTaskAction.BLOCK.value: CollectionTaskStatus.BLOCKED,
            CollectionTaskAction.SUBMIT.value: CollectionTaskStatus.SUBMITTED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.PAUSED: {
            CollectionTaskAction.RESUME.value: CollectionTaskStatus.IN_PROGRESS,
            CollectionTaskAction.SUBMIT.value: CollectionTaskStatus.SUBMITTED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.BLOCKED: {
            CollectionTaskAction.UNBLOCK.value: CollectionTaskStatus.IN_PROGRESS,
            CollectionTaskAction.SUBMIT.value: CollectionTaskStatus.SUBMITTED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.SUBMITTED: {
            CollectionTaskAction.SYSTEM_DATA_CHECK.value: CollectionTaskStatus.DATA_CHECKING,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.DATA_CHECKING: {
            CollectionTaskAction.ACCEPT.value: CollectionTaskStatus.ACCEPTED,
            CollectionTaskAction.REJECT.value: CollectionTaskStatus.REJECTED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.ACCEPTED: {
            CollectionTaskAction.COMPLETE.value: CollectionTaskStatus.COMPLETED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.REJECTED: {
            CollectionTaskAction.REWORK.value: CollectionTaskStatus.REWORK,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.REWORK: {
            CollectionTaskAction.SUBMIT.value: CollectionTaskStatus.SUBMITTED,
            CollectionTaskAction.CANCEL.value: CollectionTaskStatus.CANCELLED,
        },
        CollectionTaskStatus.COMPLETED: {},
        CollectionTaskStatus.CANCELLED: {},
    }
)

_BATCH_RULES: Mapping[CollectionBatchStatus, Mapping[str, CollectionBatchStatus]] = (
    MappingProxyType(
        {
            CollectionBatchStatus.PLANNED: {
                CollectionBatchAction.READY.value: CollectionBatchStatus.READY,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.READY: {
                CollectionBatchAction.START.value: CollectionBatchStatus.IN_PROGRESS,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.IN_PROGRESS: {
                CollectionBatchAction.PAUSE.value: CollectionBatchStatus.PAUSED,
                CollectionBatchAction.BLOCK.value: CollectionBatchStatus.BLOCKED,
                CollectionBatchAction.SUBMIT.value: CollectionBatchStatus.SUBMITTED,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.PAUSED: {
                CollectionBatchAction.RESUME.value: CollectionBatchStatus.IN_PROGRESS,
                CollectionBatchAction.SUBMIT.value: CollectionBatchStatus.SUBMITTED,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.BLOCKED: {
                CollectionBatchAction.UNBLOCK.value: CollectionBatchStatus.IN_PROGRESS,
                CollectionBatchAction.SUBMIT.value: CollectionBatchStatus.SUBMITTED,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.SUBMITTED: {
                CollectionBatchAction.ACCEPT.value: CollectionBatchStatus.ACCEPTED,
                CollectionBatchAction.REJECT.value: CollectionBatchStatus.REJECTED,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.REJECTED: {
                CollectionBatchAction.REWORK.value: CollectionBatchStatus.REWORK,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.REWORK: {
                CollectionBatchAction.SUBMIT.value: CollectionBatchStatus.SUBMITTED,
                CollectionBatchAction.CANCEL.value: CollectionBatchStatus.CANCELLED,
            },
            CollectionBatchStatus.ACCEPTED: {},
            CollectionBatchStatus.CANCELLED: {},
        }
    )
)

# 契约要求必须填写原因的动作；缺失原因返回 validation_failed。
_REASON_REQUIRED_ACTIONS: frozenset[str] = frozenset(
    {
        CollectionTaskAction.RETURN_DRAFT.value,
        CollectionTaskAction.PAUSE.value,
        CollectionTaskAction.RESUME.value,
        CollectionTaskAction.BLOCK.value,
        CollectionTaskAction.UNBLOCK.value,
        CollectionTaskAction.REJECT.value,
        CollectionTaskAction.REWORK.value,
        CollectionTaskAction.CANCEL.value,
        CollectionBatchAction.PAUSE.value,
        CollectionBatchAction.RESUME.value,
        CollectionBatchAction.BLOCK.value,
        CollectionBatchAction.UNBLOCK.value,
        CollectionBatchAction.REJECT.value,
        CollectionBatchAction.REWORK.value,
        CollectionBatchAction.CANCEL.value,
    }
)


def task_action_allowed(current: CollectionTaskStatus, action: str) -> bool:
    return action in _TASK_RULES.get(current, {})


def batch_action_allowed(current: CollectionBatchStatus, action: str) -> bool:
    return action in _BATCH_RULES.get(current, {})


def task_target_status(
    current: CollectionTaskStatus,
    action: str,
) -> CollectionTaskStatus:
    target = _TASK_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("采集任务", current.value, action)
    return target


def batch_target_status(
    current: CollectionBatchStatus,
    action: str,
) -> CollectionBatchStatus:
    target = _BATCH_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("采集批次", current.value, action)
    return target


def assert_task_action(
    current: CollectionTaskStatus,
    action: str,
) -> CollectionTaskStatus:
    return task_target_status(current, action)


def assert_batch_action(
    current: CollectionBatchStatus,
    action: str,
) -> CollectionBatchStatus:
    return batch_target_status(current, action)


def allowed_task_actions(current: CollectionTaskStatus) -> frozenset[str]:
    return frozenset(_TASK_RULES.get(current, {}))


def allowed_batch_actions(current: CollectionBatchStatus) -> frozenset[str]:
    return frozenset(_BATCH_RULES.get(current, {}))


def validate_action_name(action: object, *, batch: bool = False) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    allowed = _BATCH_RULES if batch else _TASK_RULES
    known = {name for rules in allowed.values() for name in rules}
    if value not in known:
        raise DomainValidationError("未知状态动作")
    return value


def action_requires_reason(action: str) -> bool:
    return action in _REASON_REQUIRED_ACTIONS


def task_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _TASK_RULES.items()
        for target in rules.values()
    )


def batch_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _BATCH_RULES.items()
        for target in rules.values()
    )


# ---------------------------------------------------------------------------
# 采集会话状态机（N9-001）
# ---------------------------------------------------------------------------
# 与任务/批次的区别：会话是执行态，不走审核流。``SCHEDULED`` 可直接
# ``start``，也可以直接 ``abort``（未开录就放弃）；一旦进入 ``COMPLETED`` /
# ``ABORTED`` 就没有出边，任何动作都显式失败。
SESSION_TERMINAL: frozenset[CaptureSessionStatus] = frozenset(
    {CaptureSessionStatus.COMPLETED, CaptureSessionStatus.ABORTED}
)

_SESSION_RULES: Mapping[CaptureSessionStatus, Mapping[str, CaptureSessionStatus]] = (
    MappingProxyType(
        {
            CaptureSessionStatus.SCHEDULED: {
                CaptureSessionAction.START.value: CaptureSessionStatus.RUNNING,
                CaptureSessionAction.ABORT.value: CaptureSessionStatus.ABORTED,
            },
            CaptureSessionStatus.RUNNING: {
                CaptureSessionAction.PAUSE.value: CaptureSessionStatus.PAUSED,
                CaptureSessionAction.COMPLETE.value: CaptureSessionStatus.COMPLETED,
                CaptureSessionAction.ABORT.value: CaptureSessionStatus.ABORTED,
            },
            CaptureSessionStatus.PAUSED: {
                CaptureSessionAction.RESUME.value: CaptureSessionStatus.RUNNING,
                CaptureSessionAction.COMPLETE.value: CaptureSessionStatus.COMPLETED,
                CaptureSessionAction.ABORT.value: CaptureSessionStatus.ABORTED,
            },
            CaptureSessionStatus.COMPLETED: {},
            CaptureSessionStatus.ABORTED: {},
        }
    )
)

# 与任务/批次同一口径：影响执行连续性的动作必须留下原因。
# ``start`` / ``complete`` 是正常路径，不强制原因。
_SESSION_REASON_REQUIRED_ACTIONS: frozenset[str] = frozenset(
    {
        CaptureSessionAction.PAUSE.value,
        CaptureSessionAction.RESUME.value,
        CaptureSessionAction.ABORT.value,
    }
)


def session_target_status(
    current: CaptureSessionStatus,
    action: str,
) -> CaptureSessionStatus:
    target = _SESSION_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("采集会话", current.value, action)
    return target


def assert_session_action(
    current: CaptureSessionStatus,
    action: str,
) -> CaptureSessionStatus:
    return session_target_status(current, action)


def allowed_session_actions(current: CaptureSessionStatus) -> frozenset[str]:
    return frozenset(_SESSION_RULES.get(current, {}))


def validate_session_action_name(action: object) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = {name for rules in _SESSION_RULES.values() for name in rules}
    if value not in known:
        raise DomainValidationError("未知状态动作")
    return value


def session_action_requires_reason(action: str) -> bool:
    return action in _SESSION_REASON_REQUIRED_ACTIONS


def session_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _SESSION_RULES.items()
        for target in rules.values()
    )
