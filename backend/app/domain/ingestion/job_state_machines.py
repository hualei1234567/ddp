"""IngestionJob 状态机。

权威矩阵来自 ``docs/architecture/contract-draft/collection/state-machines.v1.json``：
- 初始状态固定为 ``QUEUED``；
- ``RUNNING`` 不得直接回到 ``QUEUED``，必须先 ``FAILED`` 再重试；
- ``FAILED → QUEUED`` 由领域动作校验 ``attempt < max_attempts``；
- ``SUCCEEDED`` / ``CANCELLED`` 为终态，没有出边。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.ingestion.enums import IngestionJobAction, IngestionJobStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

INGESTION_JOB_TERMINAL: frozenset[IngestionJobStatus] = frozenset(
    {
        IngestionJobStatus.SUCCEEDED,
        IngestionJobStatus.CANCELLED,
    }
)

_JOB_RULES: Mapping[IngestionJobStatus, Mapping[str, IngestionJobStatus]] = MappingProxyType(
    {
        IngestionJobStatus.QUEUED: {
            IngestionJobAction.WORKER_START.value: IngestionJobStatus.RUNNING,
            IngestionJobAction.CANCEL.value: IngestionJobStatus.CANCELLING,
        },
        IngestionJobStatus.RUNNING: {
            IngestionJobAction.WORKER_SUCCEED.value: IngestionJobStatus.SUCCEEDED,
            IngestionJobAction.WORKER_FAIL.value: IngestionJobStatus.FAILED,
            IngestionJobAction.CANCEL.value: IngestionJobStatus.CANCELLING,
        },
        IngestionJobStatus.FAILED: {
            IngestionJobAction.RETRY.value: IngestionJobStatus.QUEUED,
        },
        IngestionJobStatus.CANCELLING: {
            IngestionJobAction.WORKER_CANCELLED.value: IngestionJobStatus.CANCELLED,
        },
        IngestionJobStatus.SUCCEEDED: {},
        IngestionJobStatus.CANCELLED: {},
    }
)


def ingestion_job_target_status(
    current: IngestionJobStatus,
    action: str,
) -> IngestionJobStatus:
    target = _JOB_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("接入任务", current.value, action)
    return target


def validate_job_action(action: object) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = {name for rules in _JOB_RULES.values() for name in rules}
    if value not in known:
        raise DomainValidationError("未知接入任务动作")
    return value


def allowed_job_actions(current: IngestionJobStatus) -> frozenset[str]:
    return frozenset(_JOB_RULES.get(current, {}))


def job_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _JOB_RULES.items()
        for target in rules.values()
    )
