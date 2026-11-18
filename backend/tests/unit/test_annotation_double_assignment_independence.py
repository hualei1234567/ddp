"""双标独立性：跨批次同一标注者不得重复标注同一 Episode。

覆盖两个容易回退的点：
1. 查全部历史分配（含终态），不能只看活动分配；
2. 引用的批次读不到时 fail-closed，不能静默跳过。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.domain.annotation.enums import AssignmentRole
from app.domain.annotation.errors import DomainValidationError, ItemAlreadyAssignedError

TASK_ID = uuid4()
CURRENT_BATCH_ID = uuid4()


class _Assignments:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def list_for_task(self, scope: object, *, task_id: UUID) -> list[object]:
        assert task_id == TASK_ID
        return self._items


class _Batches:
    def __init__(self, mapping: dict[UUID, object]) -> None:
        self._mapping = mapping

    def get(self, scope: object, batch_id: UUID) -> object | None:
        return self._mapping.get(batch_id)


def _service(assignments: _Assignments, batches: _Batches) -> AnnotationAssignmentUseCases:
    service = AnnotationAssignmentUseCases.__new__(AnnotationAssignmentUseCases)
    service._assignments = assignments
    service._batches = batches
    return service


def _assignment(assignee_id: UUID, batch_id: UUID, role: AssignmentRole) -> object:
    # 只用到这三个字段；status 刻意不提供，证明检查不依赖分配状态。
    return SimpleNamespace(assignee_id=assignee_id, batch_id=batch_id, assignment_role=role)


def _batch(batch_id: UUID, items: list[UUID]) -> object:
    return SimpleNamespace(batch_id=batch_id, item_refs=[str(item) for item in items])


def _check(
    assignments: _Assignments,
    batches: _Batches,
    *,
    episode_ids: list[UUID],
    assignee_id: UUID,
) -> None:
    _service(assignments, batches)._assert_double_annotation_independent(
        object(),
        task_id=TASK_ID,
        batch=_batch(CURRENT_BATCH_ID, episode_ids),
        assignee_id=assignee_id,
    )


def test_terminal_assignment_still_blocks_same_annotator() -> None:
    episode_id = uuid4()
    annotator = uuid4()
    other_batch_id = uuid4()
    assignments = _Assignments([_assignment(annotator, other_batch_id, AssignmentRole.ANNOTATOR)])
    batches = _Batches({other_batch_id: _batch(other_batch_id, [episode_id])})

    with pytest.raises(ItemAlreadyAssignedError):
        _check(assignments, batches, episode_ids=[episode_id], assignee_id=annotator)


def test_missing_referenced_batch_fails_closed() -> None:
    annotator = uuid4()
    assignments = _Assignments([_assignment(annotator, uuid4(), AssignmentRole.ANNOTATOR)])

    with pytest.raises(DomainValidationError):
        _check(assignments, _Batches({}), episode_ids=[uuid4()], assignee_id=annotator)


def test_disjoint_episodes_or_other_annotator_is_allowed() -> None:
    annotator = uuid4()
    other_batch_id = uuid4()
    assignments = _Assignments([_assignment(annotator, other_batch_id, AssignmentRole.ANNOTATOR)])
    batches = _Batches({other_batch_id: _batch(other_batch_id, [uuid4()])})

    _check(assignments, batches, episode_ids=[uuid4()], assignee_id=annotator)
    _check(assignments, batches, episode_ids=[uuid4()], assignee_id=uuid4())


def test_current_batch_is_ignored() -> None:
    episode_id = uuid4()
    annotator = uuid4()
    assignments = _Assignments([_assignment(annotator, CURRENT_BATCH_ID, AssignmentRole.ANNOTATOR)])

    _check(assignments, _Batches({}), episode_ids=[episode_id], assignee_id=annotator)
