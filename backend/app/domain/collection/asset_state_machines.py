"""采集素材生命周期状态机：``DRAFT → ACTIVE → ARCHIVED``（N8）。

为什么单独成文件而不是塞进 ``state_machines.py``
------------------------------------------------
``state_machines.py`` 承载任务/批次状态机（16 / 11 个状态、需要原因的动作、
数据库兜底触发器白名单）。素材状态机只有 3 状态、2 动作、无原因要求，
把它混进同一份迁移表会让两套语义互相干扰（尤其是 ``validate_action_name``
的 ``known`` 集合会被素材动作污染，任务路由就会接受 ``activate``）。
因此按"一个状态机家族一个模块"拆开，两者只在 ``__init__`` 汇总。

为什么把规则放领域层
--------------------
"哪些迁移合法"是业务规则，不是协议细节。放在路由或应用用例里写 if-else
会出现四份变体（四类素材各一份），且新增状态时必须四处同步改。
这里只有一处权威实现，四类素材共用同一张迁移表——它们的生命周期语义
本就相同（见 ``CollectionAssetStatus`` 的注释）。

终态语义
--------
``ARCHIVED`` 没有出边：归档后不可再启用。原因见
``CollectionAssetAction`` 的注释——素材内容变更应新建版本行，
不能让已被历史任务冻结引用过的定义复活成另一份内容。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.collection.enums import (
    CollectionAssetAction,
    CollectionAssetStatus,
)
from app.domain.collection.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

ASSET_TERMINAL: frozenset[CollectionAssetStatus] = frozenset({CollectionAssetStatus.ARCHIVED})

# 当前状态 -> {动作: 目标状态}。空映射表示终态，任何动作都被拒绝。
_ASSET_RULES: Mapping[CollectionAssetStatus, Mapping[str, CollectionAssetStatus]] = (
    MappingProxyType(
        {
            CollectionAssetStatus.DRAFT: {
                CollectionAssetAction.ACTIVATE.value: CollectionAssetStatus.ACTIVE,
            },
            CollectionAssetStatus.ACTIVE: {
                CollectionAssetAction.ARCHIVE.value: CollectionAssetStatus.ARCHIVED,
            },
            CollectionAssetStatus.ARCHIVED: {},
        }
    )
)


def _coerce_status(current: object) -> CollectionAssetStatus:
    """把当前状态归一成枚举；未知取值显式失败（不默认成功）。

    为什么必须在这里归一：调用方可能拿到字符串（例如仓储返回的未转换值）。
    若直接 ``_ASSET_RULES.get(current)``，未知字符串会落到 ``{}``，随后
    ``current.value`` 抛 ``AttributeError``——客户端看到 500 而不是领域错误。
    归一之后，未知状态与未知动作走同一条 ``InvalidStateTransitionError`` 路径。
    """
    try:
        return CollectionAssetStatus(current)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"未知素材状态：{current!r}") from exc


def asset_action_allowed(current: object, action: object) -> bool:
    status = _coerce_status(current)
    if not isinstance(action, str):
        return False
    return action in _ASSET_RULES.get(status, {})


def asset_target_status(
    current: object,
    action: object,
) -> CollectionAssetStatus:
    """返回动作后的目标状态；非法迁移抛 :class:`InvalidStateTransitionError`。

    不返回 ``None``、也不提供"默认成功"分支：未知状态或未知动作必须显式失败。
    """
    status = _coerce_status(current)
    name = action if isinstance(action, str) else ""
    target = _ASSET_RULES.get(status, {}).get(name)
    if target is None:
        raise InvalidStateTransitionError("采集素材", status.value, action)
    return target


def assert_asset_action(
    current: object,
    action: object,
) -> CollectionAssetStatus:
    return asset_target_status(current, action)


def allowed_asset_actions(current: object) -> frozenset[str]:
    status = _coerce_status(current)
    return frozenset(_ASSET_RULES.get(status, {}))


def validate_asset_action_name(action: object) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = {name for rules in _ASSET_RULES.values() for name in rules}
    if value not in known:
        raise DomainValidationError("未知状态动作")
    return value


def asset_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _ASSET_RULES.items()
        for target in rules.values()
    )


__all__ = [
    "ASSET_TERMINAL",
    "allowed_asset_actions",
    "asset_action_allowed",
    "asset_target_status",
    "asset_transition_pairs",
    "assert_asset_action",
    "validate_asset_action_name",
]
