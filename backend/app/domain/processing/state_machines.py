"""ProcessingRun 七态状态机。

显式迁移表：未列出的 ``(当前状态, 动作)`` 一律 ``state_transition_invalid``。

- ``system-*`` / ``worker-*`` 是服务端内部动作，不出现在 ``allowed_actions``；
- ``FAILED → RETRYING → RUNNING`` 是重试链，重试计数在领域层递增；
- ``FAILED → FAILED`` 的 ``system-close`` 是保留错误的收口自环：状态不变，
  但写入 ``closed_at`` 后不再允许任何出边（含 Worker 重试）；
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
            # 只允许 Worker 重试。**不给客户端 cancel**：CANCELLED 不允许携带
            # error_*（DB CHECK），从 FAILED 取消会把失败原因永久抹掉，
            # 与契约「FAILED 不是终态、必须能解释为什么失败」冲突。
            #
            # ``system-close`` 是状态不变的自环：它保留 error_*，只在运行上
            # 写入 ``closed_at``，标记为"已收口、不可再操作"。
            # 收口后的运行连 Worker 重试也拒绝，见 ProcessingRun.transition。
            ProcessingRunAction.WORKER_RETRY.value: ProcessingRunStatus.RETRYING.value,
            ProcessingRunAction.SYSTEM_CLOSE.value: ProcessingRunStatus.FAILED.value,
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
