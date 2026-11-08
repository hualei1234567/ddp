"""采集任务与批次状态、优先级和动作枚举。

取值以 ``docs/architecture/contract-draft/collection/state-machines.v1.json``
为准；本轮实现只覆盖采集任务/批次，不包含上传、资产、Job 与 Episode。
"""

from __future__ import annotations

from enum import StrEnum


class CollectionTaskStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    DATA_CHECKING = "DATA_CHECKING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REWORK = "REWORK"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CollectionBatchStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REWORK = "REWORK"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class CollectionTaskAction(StrEnum):
    SUBMIT_REVIEW = "submit-review"
    RETURN_DRAFT = "return-draft"
    APPROVE = "approve"
    READY = "ready"
    ASSIGN = "assign"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    BLOCK = "block"
    UNBLOCK = "unblock"
    SUBMIT = "submit"
    SYSTEM_DATA_CHECK = "system-data-check"
    ACCEPT = "accept"
    REJECT = "reject"
    REWORK = "rework"
    COMPLETE = "complete"
    CANCEL = "cancel"


class CollectionBatchAction(StrEnum):
    READY = "ready"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    BLOCK = "block"
    UNBLOCK = "unblock"
    SUBMIT = "submit"
    ACCEPT = "accept"
    REJECT = "reject"
    REWORK = "rework"
    COMPLETE = "complete"
    CANCEL = "cancel"


# OpenAPI 的路径动作白名单：系统内部动作 ``system-data-check`` 不暴露给客户端。
PUBLIC_TASK_ACTIONS: frozenset[str] = frozenset(
    action.value
    for action in CollectionTaskAction
    if action is not CollectionTaskAction.SYSTEM_DATA_CHECK
)
PUBLIC_BATCH_ACTIONS: frozenset[str] = frozenset(action.value for action in CollectionBatchAction)
