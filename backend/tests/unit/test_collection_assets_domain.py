"""采集素材领域单测 + 引用校验的应用层单测（COL-002）。

两类断言分开写：

- 领域实体的单实体不变量（必填、长度、状态取值、JSON 形状、标签去重）；
- ``assert_task_asset_references`` 的四条规则（存在性、版本成对、版本一致）。

后者用**假目录**（满足 ``CollectionAssetDirectory`` 协议的最小对象）而非数据库：
规则本身是纯逻辑，用数据库测会混入外键行为，反而看不出是"应用层没校验"还是
"数据库兜底拒绝了"。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.collection.asset_references import assert_task_asset_references
from app.domain.collection.assets import (
    SCENE_LABELS_MAX,
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import DomainValidationError, ResourceNotFoundError
from app.domain.collection.validation import JSON_MAX_BYTES, frozen_json_array
from app.domain.identity.models import ProjectScope

NOW = datetime(2026, 11, 16, tzinfo=UTC)


# --- 领域实体不变量 ---------------------------------------------------------
def test_plan_defaults_and_status_validation() -> None:
    plan = CollectionPlan(plan_id=uuid4(), project_id=uuid4(), code=" P-1 ", name="方案")
    assert plan.code == "P-1"
    assert plan.status is CollectionAssetStatus.DRAFT
    assert plan.plan_version == 1

    with pytest.raises(DomainValidationError):
        CollectionPlan(plan_id=uuid4(), project_id=uuid4(), code="p", name="n", plan_version=0)
    with pytest.raises(DomainValidationError):
        CollectionPlan(plan_id=uuid4(), project_id=uuid4(), code="p", name="n", status="PUBLISHED")
    with pytest.raises(DomainValidationError):
        CollectionPlan(plan_id=uuid4(), project_id=uuid4(), code="", name="n")


def test_plan_requires_uuid_identifier() -> None:
    with pytest.raises(DomainValidationError):
        CollectionPlan(plan_id="not-a-uuid", project_id=uuid4(), code="p", name="n")  # type: ignore[arg-type]


def test_sop_steps_are_frozen_and_bounded() -> None:
    sop = CollectionSop(
        sop_id=uuid4(),
        project_id=uuid4(),
        code="S-1",
        name="SOP",
        sop_version="v1",
        steps=({"index": 1}, {"index": 2}),
    )
    assert isinstance(sop.steps, tuple)
    assert len(sop.steps) == 2

    with pytest.raises(DomainValidationError):
        CollectionSop(
            sop_id=uuid4(),
            project_id=uuid4(),
            code="S-2",
            name="SOP",
            sop_version="v1",
            steps="not-a-list",  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        CollectionSop(
            sop_id=uuid4(),
            project_id=uuid4(),
            code="S-3",
            name="SOP",
            sop_version="",  # 版本号必填
        )


def test_scenario_labels_are_deduplicated_and_bounded() -> None:
    scenario = Scenario(
        scenario_id=uuid4(),
        project_id=uuid4(),
        code="SC-1",
        name="场景",
        scene_labels=["家庭", "家庭", " 桌面 "],
    )
    assert scenario.scene_labels == ("家庭", "桌面")

    with pytest.raises(DomainValidationError):
        Scenario(
            scenario_id=uuid4(),
            project_id=uuid4(),
            code="SC-2",
            name="场景",
            scene_labels="家庭",  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        Scenario(
            scenario_id=uuid4(),
            project_id=uuid4(),
            code="SC-3",
            name="场景",
            scene_labels=[f"标签{i}" for i in range(SCENE_LABELS_MAX + 1)],
        )


def test_action_spec_actions_validated_as_json_array() -> None:
    spec = ActionSpec(
        action_spec_id=uuid4(),
        project_id=uuid4(),
        code="A-1",
        name="动作",
        spec_version="v1",
        actions=({"code": "pick"},),
    )
    assert isinstance(spec.actions, tuple)
    with pytest.raises(DomainValidationError):
        ActionSpec(
            action_spec_id=uuid4(),
            project_id=uuid4(),
            code="A-2",
            name="动作",
            spec_version="v1",
            actions=({"blob": "x" * (JSON_MAX_BYTES + 1)},),  # type: ignore[arg-type]
        )


def test_frozen_json_array_rejects_nan_and_deep_nesting() -> None:
    with pytest.raises(DomainValidationError):
        frozen_json_array([{"v": float("nan")}], "steps", max_items=4)
    deeply_nested: object = {"v": 1}
    for _ in range(12):
        deeply_nested = {"n": deeply_nested}
    with pytest.raises(DomainValidationError):
        frozen_json_array([deeply_nested], "steps", max_items=4)
    assert frozen_json_array(None, "steps", max_items=4) == ()


# --- 任务引用校验 -----------------------------------------------------------
class _FakeDirectory:
    """最小假目录：只记录"哪些 ID/版本存在"，不碰数据库。"""

    def __init__(
        self,
        *,
        plans: set[UUID] | None = None,
        sops: dict[UUID, set[str]] | None = None,
        scenarios: set[UUID] | None = None,
        specs: dict[UUID, set[str]] | None = None,
    ) -> None:
        self._plans = plans or set()
        self._sops = sops or {}
        self._scenarios = scenarios or set()
        self._specs = specs or {}

    def plan_exists(self, scope: ProjectScope, plan_id: UUID) -> bool:
        return plan_id in self._plans

    def sop_exists(self, scope: ProjectScope, sop_id: UUID) -> bool:
        return sop_id in self._sops

    def sop_version_exists(self, scope: ProjectScope, *, sop_id: UUID, sop_version: str) -> bool:
        return sop_version in self._sops.get(sop_id, set())

    def scenario_exists(self, scope: ProjectScope, scenario_id: UUID) -> bool:
        return scenario_id in self._scenarios

    def action_spec_exists(self, scope: ProjectScope, spec_id: UUID) -> bool:
        return spec_id in self._specs

    def action_spec_version_exists(
        self, scope: ProjectScope, *, spec_id: UUID, spec_version: str
    ) -> bool:
        return spec_version in self._specs.get(spec_id, set())


def _scope() -> ProjectScope:
    return ProjectScope(project_id=uuid4(), actor_id=uuid4())


def _call(
    directory: object,
    *,
    plan_id: UUID | None = None,
    sop_id: UUID | None = None,
    sop_version: str | None = None,
    scenario_id: UUID | None = None,
    action_spec_id: UUID | None = None,
    action_spec_version: str | None = None,
) -> None:
    assert_task_asset_references(
        directory,  # type: ignore[arg-type]
        _scope(),
        plan_id=plan_id,
        sop_id=sop_id,
        sop_version=sop_version,
        scenario_id=scenario_id,
        action_spec_id=action_spec_id,
        action_spec_version=action_spec_version,
    )


def test_all_absent_references_pass() -> None:
    _call(_FakeDirectory())


def test_existing_same_project_references_pass() -> None:
    plan_id, sop_id, scenario_id, spec_id = uuid4(), uuid4(), uuid4(), uuid4()
    _call(
        _FakeDirectory(
            plans={plan_id},
            sops={sop_id: {"v1"}},
            scenarios={scenario_id},
            specs={spec_id: {"v1"}},
        ),
        plan_id=plan_id,
        sop_id=sop_id,
        sop_version="v1",
        scenario_id=scenario_id,
        action_spec_id=spec_id,
        action_spec_version="v1",
    )


@pytest.mark.parametrize("field", ["plan_id", "scenario_id", "sop_id", "action_spec_id"])
def test_missing_asset_raises_not_found(field: str) -> None:
    payload: dict[str, object] = {field: uuid4()}
    if field == "sop_id":
        payload["sop_version"] = "v1"
    if field == "action_spec_id":
        payload["action_spec_version"] = "v1"
    with pytest.raises(ResourceNotFoundError):
        _call(_FakeDirectory(), **payload)  # type: ignore[arg-type]


def test_version_must_accompany_id() -> None:
    sop_id = uuid4()
    directory = _FakeDirectory(sops={sop_id: {"v1"}})
    with pytest.raises(DomainValidationError):
        _call(directory, sop_id=sop_id)
    with pytest.raises(DomainValidationError):
        _call(directory, sop_version="v1")


def test_version_must_match_existing_row() -> None:
    sop_id, spec_id = uuid4(), uuid4()
    directory = _FakeDirectory(sops={sop_id: {"v1"}}, specs={spec_id: {"v1"}})
    with pytest.raises(DomainValidationError):
        _call(directory, sop_id=sop_id, sop_version="v9")
    with pytest.raises(DomainValidationError):
        _call(directory, action_spec_id=spec_id, action_spec_version="v9")


# --- N9-003：采集方案目标与质量阈值 -----------------------------------------
def test_plan_targets_default_to_unset() -> None:
    """缺省不是 0 / 空值伪装出来的目标：NULL 与 ``{}`` 都表示"未设定"。"""
    plan = CollectionPlan(plan_id=uuid4(), project_id=uuid4(), code="p", name="n")
    assert plan.target_episode_count is None
    assert dict(plan.quality_thresholds) == {}


def test_plan_targets_accept_registered_thresholds() -> None:
    plan = CollectionPlan(
        plan_id=uuid4(),
        project_id=uuid4(),
        code="p",
        name="n",
        target_episode_count=20,
        quality_thresholds={
            "min_selected_count": 20,
            "max_duplicated_count": 0,
            "min_total_duration_ns": 1_000_000_000,
            "max_blocking_issue_count": 0,
            "max_warning_count": 2.5,
        },
    )
    assert plan.target_episode_count == 20
    assert dict(plan.quality_thresholds)["min_total_duration_ns"] == 1_000_000_000


@pytest.mark.parametrize("value", [-1, True, 1.5, "20"])
def test_plan_target_episode_count_must_be_non_negative_int(value: object) -> None:
    with pytest.raises(DomainValidationError):
        CollectionPlan(
            plan_id=uuid4(),
            project_id=uuid4(),
            code="p",
            name="n",
            target_episode_count=value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "thresholds",
    [
        {"unknown_threshold": 1},
        {"min_selected_count": -1},
        {"min_selected_count": float("nan")},
        {"min_selected_count": float("inf")},
        {"min_selected_count": "10"},
        {"min_selected_count": True},
        {"min_selected_count": None},
    ],
)
def test_plan_quality_thresholds_are_structured_and_bounded(
    thresholds: dict[str, object],
) -> None:
    with pytest.raises(DomainValidationError):
        CollectionPlan(
            plan_id=uuid4(),
            project_id=uuid4(),
            code="p",
            name="n",
            quality_thresholds=thresholds,  # type: ignore[arg-type]
        )


def test_plan_quality_thresholds_are_frozen() -> None:
    source: dict[str, object] = {"min_selected_count": 5}
    plan = CollectionPlan(
        plan_id=uuid4(),
        project_id=uuid4(),
        code="p",
        name="n",
        quality_thresholds=source,
    )
    source["min_selected_count"] = 999
    assert dict(plan.quality_thresholds) == {"min_selected_count": 5}
