"""导入任务状态机。

显式迁移表 + 动作名白名单，照 ``domain/suppliers/state_machines.py`` 的写法
（那是仓库里最干净的一份）。未知动作、非法迁移、终态回退一律显式失败。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

_IMPORT_TRANSITIONS: Mapping[ImportStatus, frozenset[ImportStatus]] = MappingProxyType(
    {
        ImportStatus.PENDING: frozenset({ImportStatus.RUNNING, ImportStatus.CANCELLED}),
        ImportStatus.RUNNING: frozenset(
            {ImportStatus.SUCCEEDED, ImportStatus.FAILED, ImportStatus.CANCELLED}
        ),
        # SUCCEEDED / CANCELLED 是真正的终态，不得回退。
        # FAILED 例外：允许 ``retry`` 退回 PENDING 重新领取（见下）。
        ImportStatus.SUCCEEDED: frozenset(),
        ImportStatus.FAILED: frozenset({ImportStatus.PENDING}),
        ImportStatus.CANCELLED: frozenset(),
    }
)

# 动作名 → 目标状态。
#
# 这些是**客户端可见动作**：``allowed_import_actions`` 会据此产出
# ``allowed_actions`` 供前端渲染按钮，契约 ``ImportAction`` 枚举必须与
# ``_CLIENT_ACTIONS`` 完全一致。
_IMPORT_ACTIONS: Mapping[str, ImportStatus] = MappingProxyType(
    {
        "start": ImportStatus.RUNNING,
        "succeed": ImportStatus.SUCCEEDED,
        "fail": ImportStatus.FAILED,
        "cancel": ImportStatus.CANCELLED,
    }
)

# 仅**系统内部**可用的动作：不进 ``_IMPORT_ACTIONS``，因此既不会出现在
# ``allowed_actions`` 里，也不在契约 ``ImportAction`` 枚举中——客户端无法
# 通过 HTTP 触发，只能由 Worker 在"可重试失败"时调用。
#
# 为什么需要它：Worker 执行失败时若直接写 FAILED，而 FAILED 按契约是终态
# （"终态之后任何动作都返回 state_transition_invalid"），事件已经被
# mark_retry 排到下一轮，下一轮 ImportRunner.execute 却要求 PENDING 且状态机
# 拒绝 FAILED→RUNNING —— 任务永久卡死，事件反复重试到死信。
_SYSTEM_ACTIONS: Mapping[str, ImportStatus] = MappingProxyType(
    {
        "retry": ImportStatus.PENDING,
    }
)

_ALL_ACTIONS: Mapping[str, ImportStatus] = MappingProxyType({**_IMPORT_ACTIONS, **_SYSTEM_ACTIONS})


def allowed_import_actions(status: str | ImportStatus) -> frozenset[str]:
    """当前状态下允许的**客户端**动作名；供 ``allowed_actions`` 出口使用。

    系统动作（``retry``）不在返回值里：前端不该看到"重试"按钮，重试由
    Worker 依据 ``attempt < max_attempts`` 自行决定。
    """
    current = ImportStatus(status)
    return frozenset(
        name for name, target in _IMPORT_ACTIONS.items() if target in _IMPORT_TRANSITIONS[current]
    )


def validate_import_action(action: str) -> str:
    """校验**客户端**动作名；系统动作走 ``import_target_status`` 内部校验。

    系统动作（``retry``）是已知动作，但客户端调用属于越权：显式报
    "仅限系统内部使用"，而不是笼统的"未知动作"，便于排障。HTTP 用例入口
    必须先经本函数过滤，不得把路径参数直接交给 ``import_target_status``。
    """
    name = (action or "").strip()
    if name in _SYSTEM_ACTIONS:
        raise DomainValidationError(f"导入动作 {name!r} 仅限系统内部使用，不能由客户端触发")
    if name not in _IMPORT_ACTIONS:
        raise DomainValidationError(f"未知导入动作：{action!r}")
    return name


def import_target_status(current: str | ImportStatus, action: str) -> ImportStatus:
    """返回动作后的目标状态；非法迁移抛 ``InvalidStateTransitionError``。

    同时接受客户端动作与系统动作（``retry``）；调用方若需要限制为客户端动作，
    先经 ``validate_import_action`` 过滤。
    """
    status = ImportStatus(current)
    name = (action or "").strip()
    if name not in _ALL_ACTIONS:
        raise DomainValidationError(f"未知导入动作：{action!r}")
    target = _ALL_ACTIONS[name]
    if target not in _IMPORT_TRANSITIONS[status]:
        raise InvalidStateTransitionError("导入任务", status, name)
    return target


def is_system_action(action: str) -> bool:
    return (action or "").strip() in _SYSTEM_ACTIONS


def import_transition_pairs() -> tuple[tuple[ImportStatus, ImportStatus], ...]:
    """所有合法迁移对；供领域测试与契约一致性问题使用。"""
    return tuple(
        (src, dst) for src, targets in _IMPORT_TRANSITIONS.items() for dst in sorted(targets)
    )
