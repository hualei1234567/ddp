"""供应商、到货批次与片段状态机。

显式迁移表：未列出的 (当前状态, 动作) 一律 ``state_transition_invalid``。
``system-*`` 动作只允许服务端内部调用，不会出现在 ``allowed_actions`` 中。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.suppliers.enums import (
    DeliveryBatchStatus,
    SegmentStatus,
    SupplierStatus,
)
from app.domain.suppliers.errors import DomainValidationError, InvalidStateTransitionError

_Rules = Mapping[str, Mapping[str, str]]

_SUPPLIER: _Rules = MappingProxyType(
    {
        SupplierStatus.DRAFT.value: {"activate": SupplierStatus.ACTIVE.value},
        SupplierStatus.ACTIVE.value: {
            "suspend": SupplierStatus.SUSPENDED.value,
            "terminate": SupplierStatus.TERMINATED.value,
        },
        SupplierStatus.SUSPENDED.value: {
            "resume": SupplierStatus.ACTIVE.value,
            "terminate": SupplierStatus.TERMINATED.value,
        },
        SupplierStatus.TERMINATED.value: {},
    }
)

_BATCH: _Rules = MappingProxyType(
    {
        DeliveryBatchStatus.ANNOUNCED.value: {"dispatch": DeliveryBatchStatus.IN_TRANSIT.value},
        DeliveryBatchStatus.IN_TRANSIT.value: {"arrive": DeliveryBatchStatus.ARRIVED.value},
        DeliveryBatchStatus.ARRIVED.value: {"start-screening": DeliveryBatchStatus.SCREENING.value},
        DeliveryBatchStatus.SCREENING.value: {
            # 目标状态由片段统计决定，由应用层写入；表中用占位表示"允许终审"。
            "finalize": "",
        },
        DeliveryBatchStatus.REJECTED.value: {"rework": DeliveryBatchStatus.REWORK.value},
        DeliveryBatchStatus.REWORK.value: {"start-screening": DeliveryBatchStatus.SCREENING.value},
        DeliveryBatchStatus.ACCEPTED.value: {"close": DeliveryBatchStatus.CLOSED.value},
        DeliveryBatchStatus.PARTIALLY_ACCEPTED.value: {"close": DeliveryBatchStatus.CLOSED.value},
        DeliveryBatchStatus.CLOSED.value: {},
    }
)

_SEGMENT: _Rules = MappingProxyType(
    {
        SegmentStatus.REGISTERED.value: {"queue-review": SegmentStatus.PENDING_REVIEW.value},
        SegmentStatus.PENDING_REVIEW.value: {
            "system-accept": SegmentStatus.ACCEPTED.value,
            "system-reject": SegmentStatus.REJECTED.value,
            "system-need-more": SegmentStatus.NEED_MORE.value,
        },
        SegmentStatus.NEED_MORE.value: {"resubmit": SegmentStatus.PENDING_REVIEW.value},
        SegmentStatus.ACCEPTED.value: {},
        SegmentStatus.REJECTED.value: {},
    }
)

_FAMILIES: Mapping[str, _Rules] = MappingProxyType(
    {
        "supplier": _SUPPLIER,
        "delivery-batch": _BATCH,
        "inbound-segment": _SEGMENT,
    }
)

# 终审动作的目标状态由片段统计决定，这里声明其哨兵值。
FINALIZE_SENTINEL = ""


def _family(name: str) -> _Rules:
    rules = _FAMILIES.get(name)
    if rules is None:
        raise DomainValidationError(f"未知状态机 {name}")
    return rules


def target_status(family: str, current: str, action: str) -> str:
    rules = _family(family)
    transitions = rules.get(str(current))
    if transitions is None:
        raise InvalidStateTransitionError(family, current, action)
    target = transitions.get(action)
    if target is None:
        raise InvalidStateTransitionError(family, current, action)
    return target


def allowed_actions(family: str, current: str) -> frozenset[str]:
    transitions = _family(family).get(str(current), {})
    return frozenset(action for action in transitions if not action.startswith("system-"))


def validate_action_name(action: object, *, family: str) -> str:
    if not isinstance(action, str):
        raise DomainValidationError("action 必须是字符串")
    cleaned = action.strip()
    if not cleaned or len(cleaned) > 64:
        raise DomainValidationError("action 长度非法")
    known = {name for transitions in _family(family).values() for name in transitions}
    if cleaned not in known:
        raise DomainValidationError(f"未知动作 {cleaned}")
    return cleaned
