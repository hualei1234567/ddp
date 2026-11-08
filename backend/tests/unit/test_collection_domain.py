"""领域测试：采集任务/批次状态机与字段规则。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.domain.collection import (
    CollectionBatch,
    CollectionBatchStatus,
    CollectionTask,
    CollectionTaskStatus,
    DomainValidationError,
    InvalidStateTransitionError,
    TaskPriority,
    allowed_batch_actions,
    allowed_task_actions,
)
from app.domain.collection.state_machines import (
    batch_transition_pairs,
    task_transition_pairs,
)

_DOCS = Path(__file__).resolve().parents[3] / "docs/architecture/contract-draft/collection"
_MACHINES = json.loads((_DOCS / "state-machines.v1.json").read_text(encoding="utf-8"))
_TASK = _MACHINES["machines"]["CollectionTask"]
_BATCH = _MACHINES["machines"]["CollectionBatch"]


def _task(status: str = "DRAFT", **overrides: object) -> CollectionTask:
    values: dict[str, object] = {
        "task_id": uuid4(),
        "project_id": uuid4(),
        "code": "task-1",
        "name": "采集任务一",
        "status": status,
        "priority": TaskPriority.P1,
    }
    values.update(overrides)
    return CollectionTask(**values)  # type: ignore[arg-type]


def _batch(status: str = "PLANNED", **overrides: object) -> CollectionBatch:
    values: dict[str, object] = {
        "batch_id": uuid4(),
        "project_id": uuid4(),
        "task_id": uuid4(),
        "code": "batch-1",
        "sequence_no": 1,
        "status": status,
    }
    values.update(overrides)
    return CollectionBatch(**values)  # type: ignore[arg-type]


def _actions_targeting(machine: dict[str, object], target: str) -> set[str]:
    return {
        item["action"]
        for item in machine["transitions"]  # type: ignore[index]
        if item["to"] == target
    }


@pytest.mark.parametrize(
    "transition",
    _TASK["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_task_allowed_transitions_match_contract(transition: dict[str, object]) -> None:
    payload = {"reason": "测试原因"} if transition["requires_reason"] else {}
    updated = _task(str(transition["from"])).apply_action(
        str(transition["action"]),
        payload,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert updated.status == CollectionTaskStatus(str(transition["to"]))
    assert updated.version == 1


@pytest.mark.parametrize(
    "transition",
    _BATCH["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_batch_allowed_transitions_match_contract(transition: dict[str, object]) -> None:
    payload = {"reason": "测试原因"} if transition["requires_reason"] else {}
    updated = _batch(str(transition["from"])).apply_action(
        str(transition["action"]),
        payload,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert updated.status == CollectionBatchStatus(str(transition["to"]))
    assert updated.version == 1


@pytest.mark.parametrize(
    "example",
    _TASK["forbidden_examples"],
    ids=lambda item: f"{item['from']}-to-{item['to']}",
)
def test_task_forbidden_transitions_are_rejected(example: dict[str, str]) -> None:
    actions = _actions_targeting(_TASK, example["to"])
    assert actions
    for action in actions:
        with pytest.raises(InvalidStateTransitionError):
            _task(example["from"]).apply_action(action, {"reason": "测试原因"})


@pytest.mark.parametrize(
    "example",
    _BATCH["forbidden_examples"],
    ids=lambda item: f"{item['from']}-to-{item['to']}",
)
def test_batch_forbidden_transitions_are_rejected(example: dict[str, str]) -> None:
    actions = _actions_targeting(_BATCH, example["to"])
    if not actions:
        # 例如批次根本没有 COMPLETED 状态：目标本身不在合法状态集合内。
        assert example["to"] not in {status.value for status in CollectionBatchStatus}
        return
    for action in actions:
        with pytest.raises(InvalidStateTransitionError):
            _batch(example["from"]).apply_action(action, {"reason": "测试原因"})


def test_terminal_states_have_no_outgoing_actions() -> None:
    for status in CollectionTaskStatus:
        if status.value in _TASK["terminal"]:
            assert allowed_task_actions(status) == frozenset()
    for status in CollectionBatchStatus:
        if status.value in _BATCH["terminal"]:
            assert allowed_batch_actions(status) == frozenset()


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _task().apply_action("explode")


def test_reason_is_required_for_contract_actions() -> None:
    with pytest.raises(DomainValidationError):
        _task("PENDING_REVIEW").apply_action("return-draft")
    with pytest.raises(DomainValidationError):
        _batch("IN_PROGRESS").apply_action("pause")


def test_block_and_unblock_reason_fields() -> None:
    blocked = _task("IN_PROGRESS").apply_action("block", {"reason": "设备故障"})
    assert blocked.status is CollectionTaskStatus.BLOCKED
    assert blocked.status_reason == "设备故障"
    assert blocked.blocked_reason == "设备故障"

    unblocked = blocked.apply_action("unblock", {"reason": "故障已排除"})
    assert unblocked.status is CollectionTaskStatus.IN_PROGRESS
    assert unblocked.status_reason == "故障已排除"
    assert unblocked.blocked_reason is None


def test_assignment_and_actual_timestamps() -> None:
    assigned_to = uuid4()
    device_ids = (uuid4(), uuid4())
    assigned = _task("READY").apply_action(
        "assign",
        {
            "assigned_to": assigned_to,
            "assigned_device_ids": [*device_ids, device_ids[0]],
        },
    )
    assert assigned.assigned_to == assigned_to
    assert assigned.assigned_device_ids == device_ids

    started = assigned.apply_action("start", now=datetime(2026, 2, 1, tzinfo=UTC))
    assert started.actual_start == datetime(2026, 2, 1, tzinfo=UTC)


def test_field_validation_rejects_invalid_values() -> None:
    with pytest.raises(DomainValidationError):
        _task(target_episode_count=-1)
    with pytest.raises(DomainValidationError):
        _task(
            scheduled_start=datetime(2026, 2, 1, tzinfo=UTC),
            scheduled_end=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(DomainValidationError):
        _task(priority="P9")
    with pytest.raises(DomainValidationError):
        _batch(sequence_no=0)


def test_transition_pairs_are_finite_and_terminal_safe() -> None:
    task_pairs = task_transition_pairs()
    batch_pairs = batch_transition_pairs()
    assert ("DRAFT", "PENDING_REVIEW") in task_pairs
    assert ("COMPLETED", "IN_PROGRESS") not in task_pairs
    assert ("PLANNED", "READY") in batch_pairs
    assert ("CANCELLED", "READY") not in batch_pairs
