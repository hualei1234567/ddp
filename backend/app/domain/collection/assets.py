"""采集素材实体：采集方案、SOP、场景、动作库（COL-002）。

背景（为什么必须新建）
----------------------
迁移 0007 给 ``collection_tasks`` 建了 ``plan_id`` / ``sop_id`` / ``scenario_id`` /
``action_spec_id`` 等六个可空列，但从未建过对应的四张表，也没有外键。
结果是这四个列可以写入**任意 UUID**——跨项目 ID、不存在的"幽灵引用"都能落库，
数据库层面无法拒绝。本模块补齐这四类实体的领域规则，迁移 0040 补齐表与外键。

版本语义（与任务的六个字段配套，必须成对理解）
----------------------------------------------
- ``CollectionPlan.plan_version``：方案自身的业务版本号（整数）。方案内容变更 =
  新版本行，沿用仓库既有做法（参考 ``annotation_schemas.schema_version``）。
- ``CollectionSop.sop_version``：SOP 的业务版本号（文本）。
- ``ActionSpec.spec_version``：动作库的业务版本号（文本）。
- ``Scenario`` **不**版本化：场景是名词性描述（家庭 / 仓储 / 桌面…），
  调整文案不需要冻结历史任务。

任务上的 ``sop_version`` / ``action_spec_version`` 是**任务创建时冻结的版本快照**：
SOP 或动作库后续升版不会改写历史任务，这正是两处都要存"id + version"的原因。

领域层只保证单实体不变量（必填、长度、状态取值、JSON 形状）；"引用必须同项目
存在"是跨对象前置条件，由应用层校验 + 数据库复合外键兜底。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import DomainValidationError
from app.domain.collection.validation import (
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    coerce_enum,
    frozen_json_array,
    frozen_mapping,
    normalized_text,
    optional_non_negative_int,
    optional_text,
    optional_uuid,
    positive_int,
)
from app.domain.datasets.requirement_match import QUALITY_THRESHOLD_KEYS

DESCRIPTION_MAX_LENGTH = 2000
SCENE_LABELS_MAX = 32
SCENE_LABEL_MAX_LENGTH = 64
STEPS_MAX = 64
ACTIONS_MAX = 64


def _required_uuid(value: object, field_name: str) -> UUID:
    """必填 UUID；``optional_uuid`` 只做类型判定，这里补"不得为空"。"""
    resolved = optional_uuid(value, field_name)
    if resolved is None:
        raise DomainValidationError(f"{field_name} 必填")
    return resolved


def _version_text(value: object, field_name: str) -> str:
    """版本号是业务标识（``v1`` / ``2026-11``），不是数字，故按文本校验。"""
    return normalized_text(value, field_name, max_length=VERSION_TEXT_MAX_LENGTH)


@dataclass(frozen=True, slots=True)
class CollectionPlan:
    """采集方案：项目内可被多个任务复用的采集定义，按整型版本演进。"""

    plan_id: UUID
    project_id: UUID
    code: str
    name: str
    plan_version: int = 1
    status: CollectionAssetStatus | str = CollectionAssetStatus.DRAFT
    description: str | None = None
    owner_id: UUID | None = None
    # 采集目标与质量阈值只是**计划**，不是执行保证，也不构成自动验收依据；
    # 实际是否达标由质量运行与人工审核结论判定。
    target_episode_count: int | None = None
    quality_thresholds: Mapping[str, object] = field(default_factory=dict)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        _required_uuid(self.plan_id, "plan_id")
        _required_uuid(self.project_id, "project_id")
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(self, "plan_version", positive_int(self.plan_version, "plan_version"))
        object.__setattr__(
            self, "status", coerce_enum(self.status, CollectionAssetStatus, "status")
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, "description", max_length=DESCRIPTION_MAX_LENGTH),
        )
        object.__setattr__(self, "owner_id", optional_uuid(self.owner_id, "owner_id"))
        object.__setattr__(
            self,
            "target_episode_count",
            optional_non_negative_int(self.target_episode_count, "target_episode_count"),
        )
        object.__setattr__(
            self,
            "quality_thresholds",
            _plan_quality_thresholds(self.quality_thresholds),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))


@dataclass(frozen=True, slots=True)
class CollectionSop:
    """标准作业程序；``steps`` 是有序步骤清单，任务冻结 ``sop_version`` 时使用。"""

    sop_id: UUID
    project_id: UUID
    code: str
    name: str
    sop_version: str
    steps: Sequence[Mapping[str, object]] = ()
    status: CollectionAssetStatus | str = CollectionAssetStatus.DRAFT
    description: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        _required_uuid(self.sop_id, "sop_id")
        _required_uuid(self.project_id, "project_id")
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(self, "sop_version", _version_text(self.sop_version, "sop_version"))
        object.__setattr__(
            self, "steps", frozen_json_array(self.steps, "steps", max_items=STEPS_MAX)
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, CollectionAssetStatus, "status")
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, "description", max_length=DESCRIPTION_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))


@dataclass(frozen=True, slots=True)
class Scenario:
    """采集场景（家庭 / 仓储 / 桌面…）；不版本化，``scene_labels`` 是自由标签。"""

    scenario_id: UUID
    project_id: UUID
    code: str
    name: str
    scene_labels: Sequence[str] = ()
    status: CollectionAssetStatus | str = CollectionAssetStatus.DRAFT
    description: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        _required_uuid(self.scenario_id, "scenario_id")
        _required_uuid(self.project_id, "project_id")
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(self, "scene_labels", _scene_labels(self.scene_labels))
        object.__setattr__(
            self, "status", coerce_enum(self.status, CollectionAssetStatus, "status")
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, "description", max_length=DESCRIPTION_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """动作库条目；``actions`` 是有序动作定义清单，任务冻结 ``spec_version`` 时使用。"""

    action_spec_id: UUID
    project_id: UUID
    code: str
    name: str
    spec_version: str
    actions: Sequence[Mapping[str, object]] = ()
    status: CollectionAssetStatus | str = CollectionAssetStatus.DRAFT
    description: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        _required_uuid(self.action_spec_id, "action_spec_id")
        _required_uuid(self.project_id, "project_id")
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "spec_version",
            _version_text(self.spec_version, "spec_version"),
        )
        object.__setattr__(
            self,
            "actions",
            frozen_json_array(self.actions, "actions", max_items=ACTIONS_MAX),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, CollectionAssetStatus, "status")
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, "description", max_length=DESCRIPTION_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))


def _plan_quality_thresholds(value: object) -> Mapping[str, object]:
    """采集方案的质量阈值：有界 JSON + 既有登记键 + 有限非负数值。

    键集合复用 :data:`app.domain.datasets.requirement_match.QUALITY_THRESHOLD_KEYS`，
    不在这里另立一套质量词汇；未登记的键一律拒绝，避免客户端写入永远不会被
    任何质量报告核对的"装饰性阈值"。

    这里只做**计划字段**校验：阈值是采集目标，不是执行保证，也不参与自动验收。
    实际达标与否由质量运行与人工审核结论判定。
    """
    if value is None:
        return frozen_mapping({}, "quality_thresholds")
    mapping = frozen_mapping(value, "quality_thresholds")
    unknown = sorted(set(mapping) - QUALITY_THRESHOLD_KEYS)
    if unknown:
        raise DomainValidationError("quality_thresholds 包含未登记的阈值键")
    for key in sorted(mapping):
        item = mapping[key]
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise DomainValidationError(f"quality_thresholds 的 {key} 必须是数字")
        number = float(item)
        if not math.isfinite(number) or number < 0:
            raise DomainValidationError(f"quality_thresholds 的 {key} 必须是有限非负数")
    return mapping


def _scene_labels(value: object) -> tuple[str, ...]:
    """场景标签：去重、保序、非空字符串；拒绝把单个字符串当标签序列。"""
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list | tuple | set | frozenset):
        raise DomainValidationError("scene_labels 必须是字符串列表")
    labels: list[str] = []
    seen: set[str] = set()
    for item in value:
        label = normalized_text(item, "scene_labels", max_length=SCENE_LABEL_MAX_LENGTH)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    if len(labels) > SCENE_LABELS_MAX:
        raise DomainValidationError(f"scene_labels 不得超过 {SCENE_LABELS_MAX} 个")
    return tuple(labels)


__all__ = [
    "ACTIONS_MAX",
    "QUALITY_THRESHOLD_KEYS",
    "ActionSpec",
    "DESCRIPTION_MAX_LENGTH",
    "SCENE_LABELS_MAX",
    "SCENE_LABEL_MAX_LENGTH",
    "STEPS_MAX",
    "CollectionPlan",
    "CollectionSop",
    "Scenario",
]
