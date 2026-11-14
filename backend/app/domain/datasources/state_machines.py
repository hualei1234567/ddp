"""导入任务状态机。

显式迁移表 + 动作名白名单，照 ``domain/suppliers/state_machines.py`` 的写法
（那是仓库里最干净的一份）。未知动作、非法迁移、终态回退一律显式失败。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.datasources.enums import IMPORT_TERMINAL, ImportStatus
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
        # 终态不得回退
        ImportStatus.SUCCEEDED: frozenset(),
        ImportStatus.FAILED: frozenset(),
        ImportStatus.CANCELLED: frozenset(),
    }
)

# 动作名 → (目标状态, 是否要求非终态)
_IMPORT_ACTIONS: Mapping[str, ImportStatus] = MappingProxyType(
    {
        "start": ImportStatus.RUNNING,
        "succeed": ImportStatus.SUCCEEDED,
        "fail": ImportStatus.FAILED,
        "cancel": ImportStatus.CANCELLED,
    }
)


def allowed_import_actions(status: str | ImportStatus) -> frozenset[str]:
    """当前状态下允许的动作名；供 ``allowed_actions`` 出口使用。"""
    current = ImportStatus(status)
    return frozenset(
        name for name, target in _IMPORT_ACTIONS.items() if target in _IMPORT_TRANSITIONS[current]
    )


def validate_import_action(action: str) -> str:
    name = (action or "").strip()
    if name not in _IMPORT_ACTIONS:
        raise DomainValidationError(f"未知导入动作：{action!r}")
    return name


def import_target_status(current: str | ImportStatus, action: str) -> ImportStatus:
    """返回动作后的目标状态；非法迁移抛 ``InvalidStateTransitionError``。"""
    status = ImportStatus(current)
    name = validate_import_action(action)
    target = _IMPORT_ACTIONS[name]
    if status in IMPORT_TERMINAL:
        raise InvalidStateTransitionError("导入任务", status, name)
    if target not in _IMPORT_TRANSITIONS[status]:
        raise InvalidStateTransitionError("导入任务", status, name)
    return target


def import_transition_pairs() -> tuple[tuple[ImportStatus, ImportStatus], ...]:
    """所有合法迁移对；供领域测试与契约一致性问题使用。"""
    return tuple(
        (src, dst) for src, targets in _IMPORT_TRANSITIONS.items() for dst in sorted(targets)
    )
