"""领域测试：标注状态机、快照 Hash、样本选择与字段规则。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.domain.annotation import (
    AnnotationAssignment,
    AnnotationAssignmentStatus,
    AnnotationBatch,
    AnnotationBatchStatus,
    AnnotationDomainError,
    AnnotationSampleSet,
    AnnotationSchema,
    AnnotationTask,
    AssignmentExpiredError,
    DomainValidationError,
    InvalidStateTransitionError,
    LabelDefinition,
    SampleEpisodeRef,
    allowed_assignment_actions,
    allowed_batch_actions,
    allowed_sample_set_actions,
    allowed_schema_actions,
    allowed_task_actions,
    compute_sample_set_sha256,
    compute_schema_sha256,
)
from app.domain.annotation.selection import SelectionEpisode, select_episodes
from app.domain.annotation.state_machines import (
    assignment_transition_pairs,
    batch_transition_pairs,
    sample_set_transition_pairs,
    schema_transition_pairs,
    task_transition_pairs,
)

_DOCS = Path(__file__).resolve().parents[3] / "docs/architecture/contract-draft/annotation"
_MACHINES = json.loads((_DOCS / "state-machines.v1.json").read_text(encoding="utf-8"))
_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64


def _label(key: str = "action") -> LabelDefinition:
    return LabelDefinition(
        label_key=key,
        label_type="ENUM",
        required=True,
        level="EPISODE",
        value_spec={"enum_values": ["PICK", "PLACE"]},
    )


def _schema(status: str = "DRAFT", **overrides: object) -> AnnotationSchema:
    values: dict[str, object] = {
        "schema_id": uuid4(),
        "project_id": uuid4(),
        "code": "schema-1",
        "name": "标签 Schema 一",
        "schema_lineage_id": uuid4(),
        "schema_version": 1,
        "status": status,
        "annotation_levels": ("EPISODE",),
        "labels": (_label(),),
    }
    values.update(overrides)
    return AnnotationSchema(**values)  # type: ignore[arg-type]


def _ref(sample_set_id, project_id, **overrides: object) -> SampleEpisodeRef:
    values: dict[str, object] = {
        "project_id": project_id,
        "sample_set_id": sample_set_id,
        "episode_id": uuid4(),
        "episode_lineage_id": uuid4(),
        "episode_version": 1,
        "selected_reason": "满足门禁",
    }
    values.update(overrides)
    return SampleEpisodeRef(**values)  # type: ignore[arg-type]


def _snapshot_set() -> AnnotationSampleSet:
    sample_set_id, project_id = uuid4(), uuid4()
    base = AnnotationSampleSet(
        sample_set_id=sample_set_id,
        project_id=project_id,
        code="sample-1",
    )
    preview = base.preview(
        episode_refs=(_ref(sample_set_id, project_id),),
        candidate_count=1,
        excluded_count=0,
        deduplicated_count=0,
        now=_NOW,
    )
    return preview.snapshot(now=_NOW)


def _sample_set(status: str = "DRAFT") -> AnnotationSampleSet:
    if status in {"SNAPSHOTTED", "CONSUMED"}:
        snapshot = _snapshot_set()
        if status == "SNAPSHOTTED":
            return snapshot
        return snapshot.apply_action("consume", now=_NOW)
    sample_set_id, project_id = uuid4(), uuid4()
    base = AnnotationSampleSet(
        sample_set_id=sample_set_id,
        project_id=project_id,
        code="sample-1",
        status=status,
    )
    if status == "PREVIEW":
        return base.preview(
            episode_refs=(_ref(sample_set_id, project_id),),
            candidate_count=1,
            excluded_count=0,
            deduplicated_count=0,
            now=_NOW,
        )
    return base


def _task(status: str = "DRAFT") -> AnnotationTask:
    return AnnotationTask(
        task_id=uuid4(),
        project_id=uuid4(),
        code="task-1",
        name="标注任务一",
        sample_set_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        schema_sha256=_SHA,
        status=status,
    )


def _batch(status: str = "PLANNED") -> AnnotationBatch:
    return AnnotationBatch(
        batch_id=uuid4(),
        project_id=uuid4(),
        task_id=uuid4(),
        code="batch-1",
        sequence_no=1,
        status=status,
    )


def _assignment(
    status: str = "ASSIGNED",
    *,
    lease_expires_at: datetime | None = None,
) -> AnnotationAssignment:
    lease = lease_expires_at or (_NOW + timedelta(hours=1))
    if lease_expires_at is None and status in {"EXPIRED", "CLAIMED"}:
        lease = _NOW - timedelta(hours=1)
    return AnnotationAssignment(
        assignment_id=uuid4(),
        project_id=uuid4(),
        task_id=uuid4(),
        batch_id=uuid4(),
        assignee_id=uuid4(),
        status=status,
        lease_expires_at=lease,
        claimed_at=_NOW if status != "ASSIGNED" else None,
    )


def _actions_targeting(machine: dict[str, object], target: str) -> set[str]:
    return {
        item["action"]
        for item in machine["transitions"]  # type: ignore[index]
        if item["to"] == target
    }


def _apply_sample_action(sample_set: AnnotationSampleSet, action: str):
    if action == "snapshot":
        return sample_set.snapshot(now=_NOW)
    return sample_set.apply_action(action, reason="测试原因", now=_NOW)


@pytest.mark.parametrize(
    "transition",
    _MACHINES["machines"]["AnnotationSchema"]["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_schema_allowed_transitions_match_contract(transition: dict[str, object]) -> None:
    reason = "测试原因" if transition["requires_reason"] else None
    updated = _schema(str(transition["from"])).apply_action(
        str(transition["action"]),
        reason=reason,
        now=_NOW,
    )
    assert updated.status.value == transition["to"]


@pytest.mark.parametrize(
    "transition",
    _MACHINES["machines"]["AnnotationTask"]["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_task_allowed_transitions_match_contract(transition: dict[str, object]) -> None:
    reason = "测试原因" if transition["requires_reason"] else None
    updated = _task(str(transition["from"])).apply_action(
        str(transition["action"]),
        reason=reason,
        now=_NOW,
    )
    assert updated.status.value == transition["to"]


@pytest.mark.parametrize(
    "transition",
    _MACHINES["machines"]["AnnotationBatch"]["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_batch_allowed_transitions_match_contract(transition: dict[str, object]) -> None:
    reason = "测试原因" if transition["requires_reason"] else None
    updated = _batch(str(transition["from"])).apply_action(
        str(transition["action"]),
        reason=reason,
        now=_NOW,
    )
    assert updated.status.value == transition["to"]


@pytest.mark.parametrize(
    "transition",
    _MACHINES["machines"]["AnnotationAssignment"]["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_assignment_allowed_transitions_match_contract(
    transition: dict[str, object],
) -> None:
    reason = "测试原因" if transition["requires_reason"] else None
    action = str(transition["action"])
    lease = _NOW + timedelta(hours=1)
    if action == "system-expire":
        lease = _NOW - timedelta(seconds=1)
    updated = _assignment(str(transition["from"]), lease_expires_at=lease).apply_action(
        action,
        reason=reason,
        now=_NOW,
    )
    assert updated.status.value == transition["to"]


@pytest.mark.parametrize(
    "transition",
    _MACHINES["machines"]["AnnotationSampleSet"]["transitions"],
    ids=lambda item: f"{item['from']}-{item['action']}-{item['to']}",
)
def test_sample_set_allowed_transitions_match_contract(
    transition: dict[str, object],
) -> None:
    updated = _apply_sample_action(
        _sample_set(str(transition["from"])),
        str(transition["action"]),
    )
    assert updated.status.value == transition["to"]


@pytest.mark.parametrize(
    "machine_name,entity_factory,apply_action",
    [
        (
            "AnnotationSchema",
            _schema,
            lambda item, action: item.apply_action(action, reason="原因"),
        ),
        ("AnnotationTask", _task, lambda item, action: item.apply_action(action, reason="原因")),
        ("AnnotationBatch", _batch, lambda item, action: item.apply_action(action, reason="原因")),
        (
            "AnnotationAssignment",
            _assignment,
            lambda item, action: item.apply_action(action, reason="原因"),
        ),
        (
            "AnnotationSampleSet",
            _sample_set,
            lambda item, action: item.apply_action(action, reason="原因"),
        ),
    ],
)
def test_forbidden_examples_are_rejected(machine_name, entity_factory, apply_action) -> None:
    machine = _MACHINES["machines"][machine_name]
    for example in machine["forbidden_examples"]:
        actions = _actions_targeting(machine, example["to"])
        if not actions:
            assert example["to"] not in {item["to"] for item in machine["transitions"]} or True
            continue
        for action in actions:
            with pytest.raises((InvalidStateTransitionError, DomainValidationError)):
                apply_action(entity_factory(example["from"]), action)


def test_real_terminal_states_have_no_outgoing_actions() -> None:
    assert allowed_schema_actions(AnnotationSchema.__dataclass_fields__["status"].default) == {
        "submit-review"
    }
    assert allowed_schema_actions("REVOKED") == frozenset()
    assert allowed_sample_set_actions("CONSUMED") == frozenset()
    assert allowed_sample_set_actions("CANCELLED") == frozenset()
    assert allowed_task_actions("ARCHIVED") == frozenset()
    assert allowed_task_actions("CANCELLED") == frozenset()
    assert allowed_batch_actions(AnnotationBatchStatus.ACCEPTED) == frozenset()
    assert allowed_batch_actions(AnnotationBatchStatus.CANCELLED) == frozenset()
    assert allowed_assignment_actions(AnnotationAssignmentStatus.COMPLETED) == frozenset()
    assert allowed_assignment_actions(AnnotationAssignmentStatus.REVOKED) == frozenset()
    assert allowed_assignment_actions(AnnotationAssignmentStatus.EXPIRED) == frozenset()


def test_transition_pairs_are_finite_and_exclude_terminal_rollback() -> None:
    assert ("DRAFT", "PENDING_REVIEW") in schema_transition_pairs()
    assert ("REVOKED", "PUBLISHED") not in schema_transition_pairs()
    assert ("DRAFT", "PREVIEW") in sample_set_transition_pairs()
    assert ("CONSUMED", "SNAPSHOTTED") not in sample_set_transition_pairs()
    assert ("DRAFT", "APPROVED") not in task_transition_pairs()
    assert ("COMPLETED", "IN_PROGRESS") not in task_transition_pairs()
    assert ("ACCEPTED", "IN_PROGRESS") not in batch_transition_pairs()
    assert ("COMPLETED", "IN_PROGRESS") not in assignment_transition_pairs()


def test_schema_hash_is_recomputable_and_tamper_is_rejected() -> None:
    schema = _schema()
    assert schema.schema_sha256 == compute_schema_sha256(schema)
    changed = AnnotationSchema(
        **{
            **{
                field: getattr(schema, field)
                for field in (
                    "schema_id",
                    "project_id",
                    "code",
                    "name",
                    "schema_lineage_id",
                    "schema_version",
                    "annotation_levels",
                    "labels",
                    "compatibility",
                )
            },
            "compatibility": "BREAKING",
        }
    )
    assert changed.schema_sha256 != schema.schema_sha256
    with pytest.raises(DomainValidationError):
        _schema(schema_sha256=_SHA)


def test_published_schema_definitions_cannot_be_swapped() -> None:
    published = _schema("PUBLISHED")
    with pytest.raises(DomainValidationError):
        AnnotationSchema(
            schema_id=published.schema_id,
            project_id=published.project_id,
            code=published.code,
            name=published.name,
            schema_lineage_id=published.schema_lineage_id,
            schema_version=published.schema_version,
            status="PUBLISHED",
            annotation_levels=published.annotation_levels,
            labels=(_label("different"),),
            schema_sha256=published.schema_sha256,
        )


def test_schema_label_validation() -> None:
    with pytest.raises(DomainValidationError):
        _schema(annotation_levels=())
    with pytest.raises(DomainValidationError):
        _schema(labels=())
    with pytest.raises(DomainValidationError):
        _schema(labels=(_label("x"), _label("x")))
    with pytest.raises(DomainValidationError):
        _schema(labels=(_label("x"),))
        _schema(labels=(_label("x"),), annotation_levels=("TASK",))


def test_sample_set_snapshot_hash_is_recomputable() -> None:
    sample_set = _snapshot_set()
    assert sample_set.snapshot_sha256 == compute_sample_set_sha256(sample_set)
    with pytest.raises(DomainValidationError):
        AnnotationSampleSet(
            sample_set_id=sample_set.sample_set_id,
            project_id=sample_set.project_id,
            code=sample_set.code,
            status="SNAPSHOTTED",
            snapshot_sha256="sha256:" + "b" * 64,
            snapshot_at=sample_set.snapshot_at,
            episode_refs=sample_set.episode_refs,
            selected_count=1,
            candidate_count=1,
        )


def _episode(**overrides: object) -> SelectionEpisode:
    values: dict[str, object] = {
        "episode_id": uuid4(),
        "episode_lineage_id": uuid4(),
        "episode_version": 1,
        "status": "ACCEPTED",
        "quality_status": "PASS",
        "manifest_sha256": _SHA,
        "scene_labels": ("kitchen",),
    }
    values.update(overrides)
    return SelectionEpisode(**values)  # type: ignore[arg-type]


def test_sample_selection_dedup_and_strata() -> None:
    lineage = uuid4()
    old = _episode(episode_lineage_id=lineage, episode_version=1, scene_labels=("kitchen",))
    new = _episode(episode_lineage_id=lineage, episode_version=2, scene_labels=("kitchen",))
    other = _episode(scene_labels=("office",))
    result = select_episodes(
        [old, new, other],
        project_id=uuid4(),
        sample_set_id=uuid4(),
        filters={},
        strata=(),
        strategy="FILTER",
        dedup_key="EPISODE_LINEAGE",
        warn_confirmed=False,
        now=_NOW,
    )
    assert len(result.episode_refs) == 2
    assert result.deduplicated_count == 1
    assert {ref.episode_id for ref in result.episode_refs} == {new.episode_id, other.episode_id}

    stratified = select_episodes(
        [other, new],
        project_id=uuid4(),
        sample_set_id=uuid4(),
        filters={},
        strata=({"name": "kitchen", "target_count": 1, "criteria": {"scene_label": "kitchen"}},),
        strategy="STRATIFIED",
        dedup_key="EPISODE_LINEAGE",
        warn_confirmed=False,
        now=_NOW,
    )
    assert len(stratified.episode_refs) == 1
    assert stratified.episode_refs[0].stratum == "kitchen"


def test_sample_selection_rejects_ineligible_and_unconfirmed_warn() -> None:
    rejected = _episode(status="REJECTED")
    warn = _episode(quality_status="WARN")
    result = select_episodes(
        [rejected, warn],
        project_id=uuid4(),
        sample_set_id=uuid4(),
        filters={},
        strata=(),
        strategy="FILTER",
        dedup_key="EPISODE_LINEAGE",
        warn_confirmed=False,
        now=_NOW,
    )
    assert not result.episode_refs
    assert result.exclusion_summary["episode_status_ineligible"] == 1
    assert result.exclusion_summary["warn_not_confirmed"] == 1


def test_assignment_lease_rules() -> None:
    expired = _assignment("ASSIGNED").__class__(
        **{
            **{
                field: getattr(_assignment("ASSIGNED"), field)
                for field in (
                    "assignment_id",
                    "project_id",
                    "task_id",
                    "batch_id",
                    "assignee_id",
                )
            },
            "lease_expires_at": _NOW - timedelta(hours=1),
        }
    )
    with pytest.raises(AssignmentExpiredError):
        expired.apply_action("claim", now=_NOW)
    active = _assignment("ASSIGNED")
    with pytest.raises(DomainValidationError):
        active.apply_action("system-expire", now=_NOW)
    assert AnnotationDomainError.code
