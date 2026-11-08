"""设备域状态机。

状态迁移表来自 DDP-101 §3.1 与 §3.2。规则：
- 只允许表中显式列出的迁移；
- 终态没有任何出边；
- 未知状态或未知目标一律拒绝，不提供“默认成功”分支。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.devices.enums import CalibrationStatus, LifecycleStatus
from app.domain.devices.errors import InvalidStateTransitionError

LIFECYCLE_TERMINAL: frozenset[LifecycleStatus] = frozenset({LifecycleStatus.RETIRED})

_LIFECYCLE_TRANSITIONS: Mapping[LifecycleStatus, frozenset[LifecycleStatus]] = MappingProxyType(
    {
        LifecycleStatus.PROVISIONED: frozenset(
            {
                LifecycleStatus.ACTIVE,
                LifecycleStatus.SUSPENDED,
                LifecycleStatus.RETIRED,
            }
        ),
        LifecycleStatus.ACTIVE: frozenset(
            {
                LifecycleStatus.IN_MAINTENANCE,
                LifecycleStatus.FAULT,
                LifecycleStatus.SUSPENDED,
                LifecycleStatus.RETIRED,
            }
        ),
        LifecycleStatus.IN_MAINTENANCE: frozenset(
            {
                LifecycleStatus.ACTIVE,
                LifecycleStatus.FAULT,
                LifecycleStatus.SUSPENDED,
                LifecycleStatus.RETIRED,
            }
        ),
        LifecycleStatus.FAULT: frozenset(
            {
                LifecycleStatus.IN_MAINTENANCE,
                LifecycleStatus.SUSPENDED,
                LifecycleStatus.RETIRED,
            }
        ),
        LifecycleStatus.SUSPENDED: frozenset(
            {
                LifecycleStatus.ACTIVE,
                LifecycleStatus.RETIRED,
            }
        ),
        LifecycleStatus.RETIRED: frozenset(),
    }
)

CALIBRATION_TERMINAL: frozenset[CalibrationStatus] = frozenset(
    {
        CalibrationStatus.SUPERSEDED,
        CalibrationStatus.REVOKED,
        CalibrationStatus.EXPIRED,
    }
)

_CALIBRATION_TRANSITIONS: Mapping[CalibrationStatus, frozenset[CalibrationStatus]] = (
    MappingProxyType(
        {
            CalibrationStatus.DRAFT: frozenset({CalibrationStatus.SUBMITTED}),
            CalibrationStatus.SUBMITTED: frozenset(
                {
                    CalibrationStatus.VALIDATED,
                    CalibrationStatus.REJECTED,
                }
            ),
            CalibrationStatus.VALIDATED: frozenset({CalibrationStatus.ACTIVE}),
            CalibrationStatus.ACTIVE: frozenset(
                {
                    CalibrationStatus.SUPERSEDED,
                    CalibrationStatus.REVOKED,
                    CalibrationStatus.EXPIRED,
                }
            ),
            CalibrationStatus.REJECTED: frozenset({CalibrationStatus.DRAFT}),
            CalibrationStatus.SUPERSEDED: frozenset(),
            CalibrationStatus.REVOKED: frozenset(),
            CalibrationStatus.EXPIRED: frozenset(),
        }
    )
)


def can_lifecycle_transition(
    current: LifecycleStatus,
    target: LifecycleStatus,
) -> bool:
    allowed = _LIFECYCLE_TRANSITIONS.get(current)
    return allowed is not None and target in allowed


def assert_lifecycle_transition(
    entity: str,
    current: LifecycleStatus,
    target: LifecycleStatus,
) -> LifecycleStatus:
    if not can_lifecycle_transition(current, target):
        raise InvalidStateTransitionError(entity, current.value, target.value)
    return target


def can_calibration_transition(
    current: CalibrationStatus,
    target: CalibrationStatus,
) -> bool:
    allowed = _CALIBRATION_TRANSITIONS.get(current)
    return allowed is not None and target in allowed


def assert_calibration_transition(
    current: CalibrationStatus,
    target: CalibrationStatus,
) -> CalibrationStatus:
    if not can_calibration_transition(current, target):
        raise InvalidStateTransitionError("标定", current.value, target.value)
    return target
