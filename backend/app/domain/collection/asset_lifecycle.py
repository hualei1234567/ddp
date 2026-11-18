"""采集素材生命周期：字段可变性与状态动作的领域实现（N8）。

为什么需要这个模块
------------------
补上更新/删除路径后，"什么状态下能改什么字段"成为一条真正的业务规则。
它必须与状态迁移表一样只有一处权威实现——否则四类素材会各自演化出
"ACTIVE 能不能改 name"的不同答案，而任务冻结的版本快照就无从判断有效性。

两条规则，按字段是否参与执行语义区分
------------------------------------
**（1）描述性字段：``DRAFT`` / ``ACTIVE`` 都可改，``ARCHIVED`` 一律不可改。**

- ``description``：纯说明文字，不参与任何执行判断；
- ``name``：展示名。任务**不冻结**素材名称（任务上只冻结 ``sop_version`` /
  ``action_spec_version``，见 ``assets.py`` 的版本语义说明），改名不改变
  "任务按哪一版执行"；
- ``Scenario.scene_labels``：场景**不版本化**（见 ``assets.py`` 文档："场景是
  名词性描述，调整文案不需要冻结历史任务"），标签只用于检索与统计聚合，
  不进入任务的执行内容。

**（2）执行内容字段：不提供原地修改，任何状态都不开放。**

``CollectionSop.steps`` 与 ``ActionSpec.actions`` 是任务真正按之执行的内容。
任务的版本快照校验（``asset_references``）只比对"被引用那一行自身的版本列"，
因此**原地改内容而不改版本号**会让任务带着 v1 的快照指向已被改写的内容——
这正是本轮要防的"快照与内容不符"。

内容变更的合法路径是**新增版本行**（``(project_id, code, sop_version)`` 唯一
约束就是为此设计的，见迁移 0040）；旧草稿行可删除。这条规则也顺带避免了
"任务引用了 DRAFT 行、之后草稿被就地改写"的隐蔽漏洞——任务侧允许引用
DRAFT 素材是既有行为，本轮不改变它。

为什么 ``ARCHIVED`` 一律不可改
------------------------------
``ARCHIVED`` 是终态：它表达"这份定义已退场，只作为历史事实存在"。
允许改动会让归档失去意义，也让审计记录无法解释"归档后内容为何变了"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from app.domain.collection.asset_state_machines import (
    assert_asset_action,
    validate_asset_action_name,
)
from app.domain.collection.assets import (
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import (
    DomainValidationError,
    ResourceImmutableError,
)
from app.domain.collection.validation import coerce_enum

# 描述性字段：不参与执行语义，DRAFT / ACTIVE 均可修改。
DESCRIPTIVE_FIELDS: frozenset[str] = frozenset({"name", "description"})

# 四类素材各自允许 PATCH 的字段。
# 刻意不包含 ``steps`` / ``actions``：执行内容只能通过新增版本行变更，
# 否则任务冻结的版本快照会与行内容脱节（见模块文档）。
# 采集目标/质量阈值是计划元数据（不是任务冻结的执行内容），DRAFT / ACTIVE
# 均可调整；它们不参与执行语义，ARCHIVED 仍按终态规则一律不可改。
PLAN_PATCHABLE_FIELDS: frozenset[str] = DESCRIPTIVE_FIELDS | {
    "target_episode_count",
    "quality_thresholds",
}
SOP_PATCHABLE_FIELDS: frozenset[str] = DESCRIPTIVE_FIELDS
SCENARIO_PATCHABLE_FIELDS: frozenset[str] = DESCRIPTIVE_FIELDS | {"scene_labels"}
ACTION_SPEC_PATCHABLE_FIELDS: frozenset[str] = DESCRIPTIVE_FIELDS

AssetT = TypeVar("AssetT", CollectionPlan, CollectionSop, Scenario, ActionSpec)


def assert_patch_allowed(
    status: CollectionAssetStatus,
    changes: Mapping[str, object],
    *,
    patchable: frozenset[str],
    resource_name: str,
) -> None:
    """PATCH 前置校验：字段白名单 + 状态闸门。

    顺序固定为"先字段后状态"：客户端提交了不允许的字段属于请求错误（422），
    应优先于"当前状态不允许修改"（409）暴露，否则调用方会误以为改对字段就能过。
    """
    if not changes:
        raise DomainValidationError("PATCH 至少需要一个字段")
    unknown = set(changes) - patchable
    if unknown:
        raise DomainValidationError("请求包含不允许修改的字段")
    if status is CollectionAssetStatus.ARCHIVED:
        raise ResourceImmutableError(f"{resource_name}已归档，不允许修改")


def apply_update(
    asset: AssetT,
    changes: Mapping[str, object],
    *,
    now: datetime,
    actor_id: UUID,
) -> AssetT:
    return replace(
        asset,
        **dict(changes),
        updated_at=now,
        updated_by=actor_id,
    )


def assert_delete_allowed(
    status: CollectionAssetStatus,
    *,
    resource_name: str,
) -> None:
    """只允许删除 ``DRAFT``。

    为什么不是"任何状态都能删"：``ACTIVE`` 素材可能正被新任务挑选，
    ``ARCHIVED`` 是历史事实的唯一载体（任务的版本快照指向它）。删除会
    让审计里"任务引用过什么"变成悬空记录，因此只允许清理尚未生效的草稿。
    "被任务引用"这一条由仓储在删除前查引用、数据库 ``ON DELETE RESTRICT`` 兜底，
    因为它是跨聚合事实，不是单实体不变量。
    """
    if status is not CollectionAssetStatus.DRAFT:
        raise ResourceImmutableError(f"仅 DRAFT 状态的{resource_name}允许删除")


def apply_status_action(
    asset: AssetT,
    action: object,
    *,
    now: datetime,
    actor_id: UUID,
) -> tuple[AssetT, CollectionAssetStatus, CollectionAssetStatus]:
    """按具名动作迁移状态，返回 ``(新实体, 原状态, 新状态)``。

    调用方需要原状态写审计，因此不让它自己再读一次——少一次"读到的和迁移用的
    不是同一份"的机会。未知动作、未知状态、终态回退一律抛领域异常，
    不存在默认成功分支。
    """
    action_name = validate_asset_action_name(action)
    current = coerce_enum(asset.status, CollectionAssetStatus, "status")
    target = assert_asset_action(current, action_name)
    updated = replace(
        asset,
        status=target,
        updated_at=now,
        updated_by=actor_id,
    )
    return updated, current, target


__all__ = [
    "ACTION_SPEC_PATCHABLE_FIELDS",
    "DESCRIPTIVE_FIELDS",
    "PLAN_PATCHABLE_FIELDS",
    "SCENARIO_PATCHABLE_FIELDS",
    "SOP_PATCHABLE_FIELDS",
    "apply_status_action",
    "apply_update",
    "assert_delete_allowed",
    "assert_patch_allowed",
]
