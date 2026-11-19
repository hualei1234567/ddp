"""采集素材生命周期领域单测（N8）。

三类断言：

1. **状态机**：``DRAFT → ACTIVE → ARCHIVED`` 合法，``ARCHIVED`` 无出边，
   未知动作/未知状态显式失败（不存在"默认成功"分支）；
2. **字段可变性**：``code`` 与执行内容（``steps`` / ``actions``）不可改，
   描述性字段在 ``DRAFT`` / ``ACTIVE`` 可改、``ARCHIVED`` 不可改；
3. **实体的 ``__post_init__`` 在重建时仍然生效**：``apply_update`` 把
   ``name=""`` 传进去必须被拒绝，而不是绕过校验写进实体。

全部是纯逻辑，不碰数据库——数据库只是兜底，规则本身必须能脱离存储验证。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.collection.asset_lifecycle import (
    ACTION_SPEC_PATCHABLE_FIELDS,
    DESCRIPTIVE_FIELDS,
    PLAN_PATCHABLE_FIELDS,
    SCENARIO_PATCHABLE_FIELDS,
    SOP_PATCHABLE_FIELDS,
    apply_status_action,
    apply_update,
    assert_delete_allowed,
    assert_patch_allowed,
)
from app.domain.collection.asset_state_machines import (
    ASSET_TERMINAL,
    allowed_asset_actions,
    assert_asset_action,
    asset_transition_pairs,
    validate_asset_action_name,
)
from app.domain.collection.assets import (
    CollectionPlan,
    Scenario,
)
from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceImmutableError,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)
ACTOR = uuid4()


def _plan(status: CollectionAssetStatus = CollectionAssetStatus.DRAFT) -> CollectionPlan:
    return CollectionPlan(
        plan_id=uuid4(),
        project_id=uuid4(),
        code="PLAN-1",
        name="方案",
        status=status,
    )


# --- 状态机 -----------------------------------------------------------------
def test_legal_transitions_follow_lifecycle() -> None:
    draft = _plan(CollectionAssetStatus.DRAFT)
    active, from_status, to_status = apply_status_action(draft, "activate", now=NOW, actor_id=ACTOR)
    assert from_status is CollectionAssetStatus.DRAFT
    assert to_status is CollectionAssetStatus.ACTIVE
    assert active.status is CollectionAssetStatus.ACTIVE
    assert active.updated_by == ACTOR

    archived, from_status, to_status = apply_status_action(
        active, "archive", now=NOW, actor_id=ACTOR
    )
    assert from_status is CollectionAssetStatus.ACTIVE
    assert to_status is CollectionAssetStatus.ARCHIVED
    assert archived.status is CollectionAssetStatus.ARCHIVED


def test_archived_is_terminal() -> None:
    archived = _plan(CollectionAssetStatus.ARCHIVED)
    assert ASSET_TERMINAL == frozenset({CollectionAssetStatus.ARCHIVED})
    assert allowed_asset_actions(CollectionAssetStatus.ARCHIVED) == frozenset()
    for action in ("activate", "archive"):
        with pytest.raises(InvalidStateTransitionError) as excinfo:
            apply_status_action(archived, action, now=NOW, actor_id=ACTOR)
        assert excinfo.value.code == "state_transition_invalid"


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (CollectionAssetStatus.DRAFT, "archive"),
        (CollectionAssetStatus.ACTIVE, "activate"),
    ],
)
def test_skipping_steps_is_rejected(status: CollectionAssetStatus, action: str) -> None:
    """不能跳过中间状态：DRAFT 直接 archive、ACTIVE 再 activate 都必须失败。"""
    with pytest.raises(InvalidStateTransitionError):
        assert_asset_action(status, action)


def test_unknown_action_is_rejected_explicitly() -> None:
    with pytest.raises(DomainValidationError):
        validate_asset_action_name("publish")
    with pytest.raises(DomainValidationError):
        validate_asset_action_name("")
    with pytest.raises(DomainValidationError):
        validate_asset_action_name(None)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        apply_status_action(
            _plan(),
            "publish",
            now=NOW,
            actor_id=ACTOR,
        )


def test_unknown_status_does_not_default_to_success() -> None:
    """未知状态必须失败：不能因为"不认识"就当合法迁移放行。"""
    with pytest.raises(DomainValidationError):
        assert_asset_action("PUBLISHED", "activate")  # type: ignore[arg-type]


def test_transition_pairs_are_minimal_and_database_ready() -> None:
    """触发器白名单只能是这两条；多一条就意味着数据库层放宽了规则。"""
    assert asset_transition_pairs() == frozenset({("DRAFT", "ACTIVE"), ("ACTIVE", "ARCHIVED")})


# --- 字段可变性 -------------------------------------------------------------
def test_patchable_fields_exclude_code_and_execution_content() -> None:
    for patchable in (
        PLAN_PATCHABLE_FIELDS,
        SOP_PATCHABLE_FIELDS,
        SCENARIO_PATCHABLE_FIELDS,
        ACTION_SPEC_PATCHABLE_FIELDS,
    ):
        assert "code" not in patchable
        assert "steps" not in patchable
        assert "actions" not in patchable
    # 采集目标/阈值是计划元数据（N9-003），可改但不得混入执行内容语义。
    assert PLAN_PATCHABLE_FIELDS == DESCRIPTIVE_FIELDS | {
        "target_episode_count",
        "quality_thresholds",
    }
    assert SCENARIO_PATCHABLE_FIELDS == DESCRIPTIVE_FIELDS | {"scene_labels"}


@pytest.mark.parametrize(
    "status",
    [CollectionAssetStatus.DRAFT, CollectionAssetStatus.ACTIVE],
)
def test_descriptive_fields_editable_before_archive(status: CollectionAssetStatus) -> None:
    assert_patch_allowed(
        status,
        {"name": "新名"},
        patchable=PLAN_PATCHABLE_FIELDS,
        resource_name="采集方案",
    )


def test_archived_asset_is_immutable() -> None:
    with pytest.raises(ResourceImmutableError):
        assert_patch_allowed(
            CollectionAssetStatus.ARCHIVED,
            {"description": "x"},
            patchable=PLAN_PATCHABLE_FIELDS,
            resource_name="采集方案",
        )


def test_unknown_patch_field_is_validation_error_before_status_check() -> None:
    """字段错误（422）优先于状态错误（409），否则调用方会误以为改对字段就能过。"""
    with pytest.raises(DomainValidationError):
        assert_patch_allowed(
            CollectionAssetStatus.ARCHIVED,
            {"code": "X"},
            patchable=PLAN_PATCHABLE_FIELDS,
            resource_name="采集方案",
        )


def test_empty_patch_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        assert_patch_allowed(
            CollectionAssetStatus.DRAFT,
            {},
            patchable=PLAN_PATCHABLE_FIELDS,
            resource_name="采集方案",
        )


def test_apply_update_revalidates_entity_invariants() -> None:
    """重建实体时 ``__post_init__`` 必须仍然生效，不能绕过领域校验。"""
    plan = _plan()
    with pytest.raises(DomainValidationError):
        apply_update(plan, {"name": "  "}, now=NOW, actor_id=ACTOR)

    updated = apply_update(plan, {"name": " 新名 "}, now=NOW, actor_id=ACTOR)
    assert updated.name == "新名"
    assert updated.version == plan.version  # 版本由仓储推进，不在领域层编造


def test_scene_labels_patch_goes_through_domain_dedup() -> None:
    scenario = Scenario(
        scenario_id=uuid4(),
        project_id=uuid4(),
        code="SC-1",
        name="场景",
        scene_labels=("家庭",),
    )
    updated = apply_update(
        scenario,
        {"scene_labels": ["桌面", "桌面", "家庭"]},
        now=NOW,
        actor_id=ACTOR,
    )
    assert tuple(updated.scene_labels) == ("桌面", "家庭")


def test_sop_and_action_spec_share_the_same_mutation_semantics() -> None:
    """四类素材共用同一套规则；差异只在可改字段（场景多一个标签字段）。"""
    for patchable in (SOP_PATCHABLE_FIELDS, ACTION_SPEC_PATCHABLE_FIELDS):
        assert_patch_allowed(
            CollectionAssetStatus.ACTIVE,
            {"description": "说明"},
            patchable=patchable,
            resource_name="素材",
        )
        with pytest.raises(DomainValidationError):
            assert_patch_allowed(
                CollectionAssetStatus.DRAFT,
                {"steps": []},
                patchable=patchable,
                resource_name="素材",
            )


# --- 删除闸门 ---------------------------------------------------------------
def test_delete_only_allowed_for_draft() -> None:
    assert_delete_allowed(CollectionAssetStatus.DRAFT, resource_name="采集方案")
    for status in (CollectionAssetStatus.ACTIVE, CollectionAssetStatus.ARCHIVED):
        with pytest.raises(ResourceImmutableError):
            assert_delete_allowed(status, resource_name="采集方案")
