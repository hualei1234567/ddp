"""契约一致性测试：领域状态机/枚举与阶段 2 JSON Schema 对齐。"""

from __future__ import annotations

import json
from pathlib import Path

from app.api.v1.collection_schemas import (
    CollectionBatchOut,
    CollectionTaskCreate,
    CollectionTaskOut,
)
from app.domain.collection.enums import (
    CollectionBatchStatus,
    CollectionTaskStatus,
)
from app.domain.collection.state_machines import (
    BATCH_TERMINAL,
    TASK_TERMINAL,
    batch_transition_pairs,
    task_transition_pairs,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIR = ROOT / "docs/architecture/contract-draft/collection"


def _json(name: str) -> dict[str, object]:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


MACHINES = _json("state-machines.v1.json")["machines"]
TASK_MACHINE = MACHINES["CollectionTask"]
BATCH_MACHINE = MACHINES["CollectionBatch"]


def test_task_state_machine_matches_contract() -> None:
    assert {status.value for status in CollectionTaskStatus} == set(TASK_MACHINE["states"])
    assert {status.value for status in TASK_TERMINAL} == set(TASK_MACHINE["terminal"])
    assert CollectionTaskStatus(TASK_MACHINE["initial"]).value == "DRAFT"
    contract_pairs = {(item["from"], item["to"]) for item in TASK_MACHINE["transitions"]}
    assert task_transition_pairs() == contract_pairs


def test_batch_state_machine_matches_contract() -> None:
    assert {status.value for status in CollectionBatchStatus} == set(BATCH_MACHINE["states"])
    assert {status.value for status in BATCH_TERMINAL} == set(BATCH_MACHINE["terminal"])
    assert CollectionBatchStatus(BATCH_MACHINE["initial"]).value == "PLANNED"
    contract_pairs = {(item["from"], item["to"]) for item in BATCH_MACHINE["transitions"]}
    assert batch_transition_pairs() == contract_pairs


def test_response_models_cover_required_contract_fields() -> None:
    task_schema = _json("collection-task.v1.schema.json")
    batch_schema = _json("collection-batch.v1.schema.json")
    assert set(task_schema["required"]) <= set(CollectionTaskOut.model_fields)
    assert set(batch_schema["required"]) <= set(CollectionBatchOut.model_fields)


def test_create_schema_required_fields_match_contract() -> None:
    task_schema = _json("collection-task.v1.schema.json")
    create_schema = {
        "required": [
            "code",
            "name",
            "priority",
            "target_episode_count",
            "target_duration_sec",
        ]
    }
    assert set(create_schema["required"]) <= set(task_schema["properties"])
    assert set(CollectionTaskCreate.model_json_schema()["required"]) == set(
        create_schema["required"]
    )


# --- N8：素材生命周期与写路径 DTO -------------------------------------------
def test_asset_state_machine_matches_domain_lifecycle() -> None:
    """素材状态机是 N8 新引入的能力，契约补充时必须与领域层逐项一致。

    契约文件暂未声明素材路径（见交付报告），因此这里不对 JSON Schema 做断言，
    只钉死"合法迁移集合"与"终态"——这两项一旦漂移，客户端会按错误的
    ``allowed_actions`` 渲染按钮。
    """
    from app.domain.collection.asset_state_machines import (
        ASSET_TERMINAL,
        allowed_asset_actions,
        asset_transition_pairs,
    )
    from app.domain.collection.enums import CollectionAssetAction, CollectionAssetStatus

    assert {status.value for status in CollectionAssetStatus} == {"DRAFT", "ACTIVE", "ARCHIVED"}
    assert asset_transition_pairs() == frozenset({("DRAFT", "ACTIVE"), ("ACTIVE", "ARCHIVED")})
    assert ASSET_TERMINAL == frozenset({CollectionAssetStatus.ARCHIVED})
    assert allowed_asset_actions(CollectionAssetStatus.DRAFT) == frozenset({"activate"})
    assert allowed_asset_actions(CollectionAssetStatus.ACTIVE) == frozenset({"archive"})
    assert allowed_asset_actions(CollectionAssetStatus.ARCHIVED) == frozenset()
    assert {action.value for action in CollectionAssetAction} == {"activate", "archive"}


def test_asset_patch_models_exclude_immutable_fields() -> None:
    """``code`` 与执行内容不能出现在 PATCH 模型里。

    这不是风格问题：字段一旦出现在模型里，客户端就会提交、而后被服务端
    拒绝或静默忽略；不出现才能让 422 明确指向"这个字段不在这里改"。
    """
    from app.api.v1.collection_asset_schemas import (
        ActionSpecPatch,
        CollectionPlanPatch,
        CollectionSopPatch,
        ScenarioPatch,
    )

    shared_forbidden = {"code", "status", "version", "project_id", "steps", "actions"}
    for model in (CollectionPlanPatch, CollectionSopPatch, ScenarioPatch, ActionSpecPatch):
        assert not shared_forbidden & set(model.model_fields), model.__name__

    # 版本列是"内容版本"锚点，同样不可经 PATCH 修改。
    for model, field in (
        (CollectionPlanPatch, "plan_version"),
        (CollectionSopPatch, "sop_version"),
        (ActionSpecPatch, "spec_version"),
    ):
        assert field not in model.model_fields

    assert "scene_labels" in ScenarioPatch.model_fields
    for model in (CollectionPlanPatch, CollectionSopPatch, ActionSpecPatch):
        assert "scene_labels" not in model.model_fields
