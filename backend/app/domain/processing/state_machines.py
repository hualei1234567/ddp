"""ProcessingRun 七态状态机。

显式迁移表：未列出的 ``(当前状态, 动作)`` 一律 ``state_transition_invalid``。

- ``system-*`` / ``worker-*`` 是服务端内部动作，不出现在 ``allowed_actions``；
- ``FAILED → RETRYING → RUNNING`` 是重试链，重试计数在领域层递增；
- ``SUCCEEDED`` / ``CANCELLED`` 为终态，没有出边。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingRunAction,
    ProcessingRunStatus,
    ProcessingStage,
)
from app.domain.processing.errors import DomainValidationError, InvalidStateTransitionError

_Rules = Mapping[str, Mapping[str, str]]

_RUN_RULES: _Rules = MappingProxyType(
    {
        ProcessingRunStatus.CREATED.value: {
            ProcessingRunAction.SYSTEM_QUEUE.value: ProcessingRunStatus.QUEUED.value,
            ProcessingRunAction.CANCEL.value: ProcessingRunStatus.CANCELLED.value,
        },
        ProcessingRunStatus.QUEUED.value: {
            ProcessingRunAction.WORKER_CLAIM.value: ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.CANCEL.value: ProcessingRunStatus.CANCELLED.value,
        },
        ProcessingRunStatus.RUNNING.value: {
            # 阶段推进是 RUNNING 上的自环：状态不变，只允许阶段单调前进。
            ProcessingRunAction.WORKER_ADVANCE.value: ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.WORKER_SUCCEED.value: ProcessingRunStatus.SUCCEEDED.value,
            ProcessingRunAction.WORKER_FAIL.value: ProcessingRunStatus.FAILED.value,
            ProcessingRunAction.CANCEL.value: ProcessingRunStatus.CANCELLED.value,
        },
        ProcessingRunStatus.FAILED.value: {
            ProcessingRunAction.WORKER_RETRY.value: ProcessingRunStatus.RETRYING.value,
            # 失败后不重试也要能显式收口，否则 FAILED 永远是非终态。
            ProcessingRunAction.CANCEL.value: ProcessingRunStatus.CANCELLED.value,
        },
        ProcessingRunStatus.RETRYING.value: {
            ProcessingRunAction.WORKER_CLAIM.value: ProcessingRunStatus.RUNNING.value,
            ProcessingRunAction.CANCEL.value: ProcessingRunStatus.CANCELLED.value,
        },
        ProcessingRunStatus.SUCCEEDED.value: {},
        ProcessingRunStatus.CANCELLED.value: {},
    }
)


def target_status(current: str, action: str) -> str:
    transitions = _RUN_RULES.get(str(current))
    if transitions is None:
        raise InvalidStateTransitionError(current, action)
    target = transitions.get(action)
    if target is None:
        raise InvalidStateTransitionError(current, action)
    return target


def allowed_actions(current: str) -> frozenset[str]:
    """客户端可见动作；内部动作（``system-*`` / ``worker-*``）永不下发。"""
    transitions = _RUN_RULES.get(str(current), {})
    return frozenset(
        action for action in transitions if not action.startswith(("system-", "worker-"))
    )


def validate_action_name(action: object) -> str:
    if not isinstance(action, str):
        raise DomainValidationError("action 必须是字符串")
    cleaned = action.strip()
    if not cleaned or len(cleaned) > 64:
        raise DomainValidationError("action 长度非法")
    known = {name for transitions in _RUN_RULES.values() for name in transitions}
    if cleaned not in known:
        raise DomainValidationError(f"未知动作 {cleaned}")
    return cleaned


def coerce_stage(value: object) -> str:
    try:
        return ProcessingStage(value).value  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(stage.value for stage in STAGE_SEQUENCE)
        raise DomainValidationError(f"stage 取值非法，允许值：{allowed}") from exc


def stage_index(stage: str) -> int:
    try:
        return STAGE_SEQUENCE.index(ProcessingStage(stage))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("stage 取值非法") from exc


def first_stage() -> str:
    return STAGE_SEQUENCE[0].value


def transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库/测试核对白名单；自环也在内。"""
    return frozenset(
        (current, target) for current, rules in _RUN_RULES.items() for target in rules.values()
    )


__all__ = [
    "allowed_actions",
    "coerce_stage",
    "first_stage",
    "stage_index",
    "target_status",
    "transition_pairs",
    "validate_action_name",
]
