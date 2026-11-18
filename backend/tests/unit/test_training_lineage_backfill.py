"""``TRAINED_ON`` 血缘回填用例的单元测试（不依赖数据库）。

用内存假实现替代候选扫描、版本读取、血缘写入与审计，只验证编排事实：

- dry-run 只产出计划，不写节点/边/审计/outbox；
- ``--apply`` 走既有 recorder 写边，metadata 标注 ``backfilled``，边沿用引用
  原始创建时间；
- 已存在的边（``ensure_edge`` 返回 ``False``）不重复写、不写审计；
- 源版本不在本项目（跨项目脏引用）时跳过且不造节点；
- 批次数上限与墙钟超时真正截断本轮。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.lineage_backfill import (
    ACTION_PLANNED,
    TrainingLineageBackfill,
)
from app.application.datasets.training_ports import LineageBackfillCandidate
from app.domain.datasets.errors import DomainValidationError
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.identity.models import ProjectScope

_NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
_REF_TIME = datetime(2026, 11, 1, 8, 30, tzinfo=UTC)


def _candidate(project_id: UUID, version_id: UUID, *, run_id: str | None = None):
    return LineageBackfillCandidate(
        project_id=project_id,
        external_ref_id=uuid4(),
        dataset_version_id=version_id,
        training_delivery_id=uuid4(),
        training_request_revision_id=uuid4(),
        adapter_id="legacy-adapter",
        external_run_id=run_id or f"run-{uuid4().hex[:8]}",
        execution_mode="REMOTE",
        created_at=_REF_TIME,
        created_by=uuid4(),
    )


class _FakeSource:
    """模拟数据库反连接：已经写过边的候选不再出现在缺失集合里。"""

    def __init__(
        self,
        candidates: list[LineageBackfillCandidate],
        *,
        is_missing=None,
    ) -> None:
        self._candidates = sorted(candidates, key=lambda item: item.external_ref_id)
        self._is_missing = is_missing or (lambda _item: True)

    def list_missing_trained_on(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 200,
    ) -> list[LineageBackfillCandidate]:
        remaining = [
            item
            for item in self._candidates
            if (after_id is None or item.external_ref_id > after_id) and self._is_missing(item)
        ]
        return remaining[:limit]


class _FakeVersions:
    def __init__(self, known: set[tuple[UUID, UUID]] | None = None) -> None:
        self._known = known or set()

    def get(self, scope: ProjectScope, version_id: UUID) -> object | None:
        return object() if (scope.project_id, version_id) in self._known else None


@dataclass
class _EdgeCall:
    from_node_type: LineageNodeType
    from_node_id: UUID
    to_node_type: LineageNodeType
    to_node_id: UUID
    relation: LineageRelation
    metadata: dict[str, object]


@dataclass
class _FakeLineage:
    nodes: list[tuple[LineageNodeType, UUID]] = field(default_factory=list)
    edges: list[_EdgeCall] = field(default_factory=list)

    def ensure_node(
        self,
        scope: ProjectScope,
        *,
        node_type: object,
        node_id: UUID,
        now: datetime,
    ) -> object:
        self.nodes.append((LineageNodeType(node_type), node_id))
        return SimpleNamespace(node_type=node_type, node_id=node_id)

    def ensure_edge(
        self,
        scope: ProjectScope,
        *,
        from_node_type: object,
        from_node_id: UUID,
        to_node_type: object,
        to_node_id: UUID,
        relation: object,
        metadata: Any,
        now: datetime,
    ) -> bool:
        key = (
            LineageNodeType(from_node_type),
            from_node_id,
            LineageNodeType(to_node_type),
            to_node_id,
            LineageRelation(relation),
        )
        if any(
            (
                item.from_node_type,
                item.from_node_id,
                item.to_node_type,
                item.to_node_id,
                item.relation,
            )
            == key
            for item in self.edges
        ):
            return False
        self.edges.append(
            _EdgeCall(
                from_node_type=LineageNodeType(from_node_type),
                from_node_id=from_node_id,
                to_node_type=LineageNodeType(to_node_type),
                to_node_id=to_node_id,
                relation=LineageRelation(relation),
                metadata=dict(metadata),
            )
        )
        return True


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[object] = []

    def record(self, scope: ProjectScope, record: object) -> None:
        self.records.append(record)


class _FakeOutbox:
    def __init__(self) -> None:
        self.events: list[object] = []

    def enqueue(self, scope: ProjectScope, event: object) -> None:
        self.events.append(event)


class _FakeUow:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _build(
    *,
    candidates: list[LineageBackfillCandidate],
    known: set[tuple[UUID, UUID]],
    lineage: _FakeLineage | None = None,
    monotonic=None,
    anti_join: bool = True,
):
    fake_lineage = lineage or _FakeLineage()
    audit = _FakeAudit()
    outbox = _FakeOutbox()
    uow = _FakeUow()
    missing = (
        (
            lambda item: not any(
                edge.to_node_id == item.external_ref_id
                and edge.relation is LineageRelation.TRAINED_ON
                for edge in fake_lineage.edges
            )
        )
        if anti_join
        else (lambda _item: True)
    )
    use_case = TrainingLineageBackfill(
        candidates=_FakeSource(candidates, is_missing=missing),
        versions=_FakeVersions(known),
        lineage=DatasetVersionLineageRecorder(fake_lineage),
        audit=audit,
        outbox=outbox,
        uow=uow,
        **({} if monotonic is None else {"monotonic": monotonic}),
    )
    return use_case, fake_lineage, audit, outbox, uow


def test_dry_run_reports_but_writes_nothing() -> None:
    project_id = uuid4()
    version_id = uuid4()
    candidates = [_candidate(project_id, version_id), _candidate(project_id, version_id)]
    known = {(project_id, version_id)}
    use_case, lineage, audit, outbox, uow = _build(candidates=candidates, known=known)

    report = use_case.run(apply=False, limit=10, max_batches=2, timeout_seconds=None, now=_NOW)

    assert report.applied is False
    assert report.examined == 2
    assert report.planned == 2
    assert report.created == 0
    assert lineage.nodes == []
    assert lineage.edges == []
    assert audit.records == []
    assert outbox.events == []
    assert uow.commits == 0
    assert all(entry.action == ACTION_PLANNED for entry in report.entries)
    described = report.entries[0].describe()
    assert str(project_id) in described
    assert f"DATASET_VERSION {version_id}" in described
    assert f"TRAINING_RUN {report.entries[0].external_ref_id}" in described


def test_apply_creates_edge_with_audit_and_original_time() -> None:
    project_id = uuid4()
    version_id = uuid4()
    candidate = _candidate(project_id, version_id)
    use_case, lineage, audit, outbox, uow = _build(
        candidates=[candidate],
        known={(project_id, version_id)},
    )

    report = use_case.run(apply=True, limit=10, max_batches=1, timeout_seconds=None, now=_NOW)

    assert report.created == 1
    assert len(lineage.edges) == 1
    edge = lineage.edges[0]
    assert edge.relation is LineageRelation.TRAINED_ON
    assert edge.from_node_type is LineageNodeType.DATASET_VERSION
    assert edge.from_node_id == version_id
    assert edge.to_node_type is LineageNodeType.TRAINING_RUN
    assert edge.to_node_id == candidate.external_ref_id
    assert edge.metadata["backfilled"] is True
    assert edge.metadata["external_run_id"] == candidate.external_run_id
    assert edge.metadata["training_delivery_id"] == str(candidate.training_delivery_id)
    # 节点先登记、边后写；节点用引用原始创建时间。
    assert lineage.nodes == [
        (LineageNodeType.DATASET_VERSION, version_id),
        (LineageNodeType.TRAINING_RUN, candidate.external_ref_id),
    ]
    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.action == "backfill-lineage"  # type: ignore[attr-defined]
    assert record.details["backfilled"] is True  # type: ignore[attr-defined]
    assert record.details["edge_created_at"] == _REF_TIME.isoformat()  # type: ignore[attr-defined]
    assert len(outbox.events) == 1
    assert outbox.events[0].event_type == "training.external_reference.lineage_backfilled"  # type: ignore[attr-defined]
    assert uow.commits == 1
    assert uow.rollbacks == 0


def test_existing_edge_is_skipped_without_audit() -> None:
    project_id = uuid4()
    version_id = uuid4()
    candidate = _candidate(project_id, version_id)
    lineage = _FakeLineage()
    lineage.edges.append(
        _EdgeCall(
            from_node_type=LineageNodeType.DATASET_VERSION,
            from_node_id=version_id,
            to_node_type=LineageNodeType.TRAINING_RUN,
            to_node_id=candidate.external_ref_id,
            relation=LineageRelation.TRAINED_ON,
            metadata={},
        )
    )
    use_case, lineage, audit, outbox, uow = _build(
        candidates=[candidate],
        known={(project_id, version_id)},
        lineage=lineage,
        # 模拟并发：扫描时边还不存在，写入前另一执行方已写入。
        anti_join=False,
    )

    report = use_case.run(apply=True, limit=10, max_batches=1, timeout_seconds=None, now=_NOW)

    assert report.created == 0
    assert report.skipped == 1
    assert report.entries[0].reason == "edge_exists"
    assert len(lineage.edges) == 1  # 未重复写
    assert audit.records == []
    assert outbox.events == []
    assert uow.commits == 0
    assert uow.rollbacks == 1


def test_cross_project_version_is_skipped_without_writes() -> None:
    project_id = uuid4()
    foreign_version_id = uuid4()  # 属于别的项目，本项目读不到
    use_case, lineage, audit, outbox, _uow = _build(
        candidates=[_candidate(project_id, foreign_version_id)],
        known=set(),
    )

    report = use_case.run(apply=True, limit=10, max_batches=1, timeout_seconds=None, now=_NOW)

    assert report.skipped == 1
    assert report.entries[0].reason == "dataset_version_missing"
    assert lineage.nodes == []
    assert lineage.edges == []
    assert audit.records == []
    assert outbox.events == []


def test_batch_limit_and_max_batches_bound_one_run() -> None:
    project_id = uuid4()
    version_id = uuid4()
    candidates = [_candidate(project_id, version_id) for _ in range(3)]
    known = {(project_id, version_id)}
    use_case, lineage, _audit, _outbox, _uow = _build(candidates=candidates, known=known)

    first = use_case.run(apply=True, limit=1, max_batches=1, timeout_seconds=None, now=_NOW)
    assert first.examined == 1
    assert first.created == 1
    assert len(lineage.edges) == 1

    second = use_case.run(apply=True, limit=1, max_batches=1, timeout_seconds=None, now=_NOW)
    assert second.examined == 1
    assert second.created == 1
    assert len(lineage.edges) == 2
    assert lineage.edges[0].to_node_id != lineage.edges[1].to_node_id


def test_timeout_stops_between_batches() -> None:
    project_id = uuid4()
    version_id = uuid4()
    # 第一次调用确定 deadline，随后立即超过 deadline。
    ticks = iter([0.0, 100.0])
    use_case, lineage, _audit, _outbox, _uow = _build(
        candidates=[_candidate(project_id, version_id)],
        known={(project_id, version_id)},
        monotonic=lambda: next(ticks),
    )

    report = use_case.run(apply=True, limit=10, max_batches=1, timeout_seconds=10, now=_NOW)

    assert report.timed_out is True
    assert report.examined == 0
    assert lineage.edges == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
        ({"max_batches": 0}, "max_batches"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
    ],
)
def test_invalid_bounds_are_rejected(kwargs: dict[str, object], message: str) -> None:
    use_case, _lineage, _audit, _outbox, _uow = _build(candidates=[], known=set())
    with pytest.raises(DomainValidationError, match=message):
        use_case.run(apply=False, **kwargs)  # type: ignore[arg-type]
