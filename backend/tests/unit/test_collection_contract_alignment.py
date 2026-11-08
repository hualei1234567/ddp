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
