"""采集任务与批次状态、优先级和动作枚举。

取值以 ``docs/architecture/contract-draft/collection/state-machines.v1.json``
为准；本轮实现只覆盖采集任务/批次，不包含上传、资产、Job 与 Episode。

COL-002 追加：采集方案 / SOP / 场景 / 动作库四类**素材实体**的状态枚举。
它们与任务状态机无关（素材是版本化的可复用定义，任务才走状态机），
因此单独列出，不复用 ``CollectionTaskStatus``。
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


class CollectionAssetStatus(StrEnum):
    """素材实体的生命周期状态（四类实体共用一套取值）。

    为什么共用：四类实体（方案 / SOP / 场景 / 动作库）在本阶段都只有
    "草稿 → 启用 → 停用"这三个业务含义，取同一套枚举可以让 CHECK 约束、
    列表过滤与前端状态标签保持一致；差异体现在各自的版本列（``plan_version``
    / ``sop_version`` / ``spec_version``），而不是状态取值上。

    刻意不含 ``PUBLISHED``：素材的"可被任务引用"由 ``ACTIVE`` 表达，
    任务侧冻结的版本号已经记录了"当时用的是哪一版"，不需要第二条发布态。
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
