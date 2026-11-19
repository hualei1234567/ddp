"""通用血缘图查询用例的单元测试（不碰 HTTP / 数据库）。

覆盖：
- 深度硬上限与参数校验（越界一律领域校验错误，由 API 映射 422）；
- 防环：互指边不会死循环、每节点只展开一次；
- 批量邻边：一层节点按块批量查询，调用次数不随节点数线性增长；
- 节点/边硬上限触发 ``truncated``；
- 非法游标不静默回首页。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.episodes.graph import (
    MAX_GRAPH_DEPTH,
    MAX_GRAPH_NODES,
    LineageGraphService,
)
from app.domain.episodes.enums import LineageDirection, LineageNodeType, LineageRelation
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.lineage import LineageEdge
from app.domain.identity.models import ProjectScope

_NOW = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
_SCOPE = ProjectScope(project_id=uuid4(), actor_id=uuid4())


class _FakeLineage:
    """按方向过滤预置边的假仓储；记录每次调用的节点块大小。"""

    def __init__(self, edges: list[LineageEdge], *, registered: bool = True) -> None:
        self.edges = edges
        self.registered = registered
        self.calls: list[tuple[tuple[LineageNodeType, UUID], ...]] = []

    def get_node(self, scope: ProjectScope, *, node_type: LineageNodeType, node_id: UUID):
        return object() if self.registered else None

    def list_edges_for_nodes(
        self,
        scope: ProjectScope,
        *,
        nodes: tuple[tuple[LineageNodeType, UUID], ...],
        direction: LineageDirection,
        limit: int,
    ) -> list[LineageEdge]:
        self.calls.append(tuple(nodes))
        node_set = set(nodes)
        selected: list[LineageEdge] = []
        for edge in self.edges:
            from_key = (edge.from_node_type, edge.from_node_id)
            to_key = (edge.to_node_type, edge.to_node_id)
            if direction is LineageDirection.OUT and from_key in node_set:
                selected.append(edge)
            elif direction is LineageDirection.IN and to_key in node_set:
                selected.append(edge)
            elif direction is LineageDirection.BOTH and (
                from_key in node_set or to_key in node_set
            ):
                selected.append(edge)
        selected.sort(key=lambda edge: (edge.created_at, edge.edge_id))
        return selected[:limit]


class _NoProvenance:
    def source_to_import_edges(self, scope, *, source_ids=(), import_ids=(), limit=100):
        return []

    def import_to_asset_edges(self, scope, *, import_ids=(), asset_ids=(), limit=100):
        return []

    def origins(self, scope, asset_ids, *, limit=100):
        return []


def _service(lineage: _FakeLineage) -> LineageGraphService:
    # 投影解析器在图查询里只做数据源链展开；本文件的图不含投影节点。
    return LineageGraphService(lineage, _NoProvenance())  # type: ignore[arg-type]


def _edge(
    index: int,
    from_type: LineageNodeType,
    from_id: UUID,
    to_type: LineageNodeType,
    to_id: UUID,
    relation: LineageRelation,
) -> LineageEdge:
    return LineageEdge(
        edge_id=uuid4(),
        project_id=_SCOPE.project_id,
        from_node_type=from_type,
        from_node_id=from_id,
        to_node_type=to_type,
        to_node_id=to_id,
        relation=relation,
        created_by=_SCOPE.actor_id,
        created_at=_NOW + timedelta(seconds=index),
    )


def test_depth_above_hard_cap_is_rejected() -> None:
    service = _service(_FakeLineage([]))
    with pytest.raises(DomainValidationError):
        service.query(
            _SCOPE,
            node_type=LineageNodeType.EPISODE,
            node_id=uuid4(),
            depth=MAX_GRAPH_DEPTH + 1,
        )


def test_limit_out_of_range_is_rejected() -> None:
    service = _service(_FakeLineage([]))
    with pytest.raises(DomainValidationError):
        service.query(_SCOPE, node_type=LineageNodeType.EPISODE, node_id=uuid4(), limit=0)


def test_invalid_cursor_is_rejected() -> None:
    root_id = uuid4()
    service = _service(_FakeLineage([], registered=True))
    with pytest.raises(DomainValidationError):
        service.query(
            _SCOPE,
            node_type=LineageNodeType.EPISODE,
            node_id=root_id,
            cursor="not-a-cursor",
        )


def test_unregistered_root_returns_empty_graph() -> None:
    service = _service(_FakeLineage([], registered=False))
    page = service.query(_SCOPE, node_type=LineageNodeType.EPISODE, node_id=uuid4())
    assert page.root_found is False
    assert page.nodes == []
    assert page.edges == []


def test_mutual_edges_do_not_loop() -> None:
    a, b = uuid4(), uuid4()
    edges = [
        _edge(
            0,
            LineageNodeType.EPISODE,
            a,
            LineageNodeType.EPISODE,
            b,
            LineageRelation.INVALIDATES,
        ),
        _edge(
            1,
            LineageNodeType.EPISODE,
            b,
            LineageNodeType.EPISODE,
            a,
            LineageRelation.INVALIDATES,
        ),
    ]
    service = _service(_FakeLineage(edges))
    page = service.query(
        _SCOPE,
        node_type=LineageNodeType.EPISODE,
        node_id=a,
        direction=LineageDirection.BOTH,
        depth=3,
        limit=100,
    )
    assert page.root_found is True
    assert {node.node_id for node in page.nodes} == {a, b}
    assert len(page.edges) == 2
    assert page.next_cursor is None


def test_adjacency_is_batched_per_level_not_per_node() -> None:
    root = uuid4()
    children = [uuid4() for _ in range(100)]
    leaves = [uuid4() for _ in range(100)]
    edges: list[LineageEdge] = []
    for index, (child, leaf) in enumerate(zip(children, leaves, strict=True)):
        edges.append(
            _edge(
                index,
                LineageNodeType.EPISODE,
                root,
                LineageNodeType.EPISODE,
                child,
                LineageRelation.INVALIDATES,
            )
        )
        edges.append(
            _edge(
                100 + index,
                LineageNodeType.EPISODE,
                child,
                LineageNodeType.DATASET_VERSION,
                leaf,
                LineageRelation.INVALIDATES,
            )
        )
    lineage = _FakeLineage(edges)
    service = _service(lineage)
    page = service.query(
        _SCOPE,
        node_type=LineageNodeType.EPISODE,
        node_id=root,
        direction=LineageDirection.OUT,
        depth=2,
        limit=100,
    )
    # 本页按 limit=100 只返回第一层的 100 条边（root + 100 个子节点）。
    assert len(page.nodes) == 101
    assert page.next_cursor is not None
    # 第 0 层 1 次 + 第 1 层 100 个节点按 64 一块 = 2 次，共 3 次；
    # 逐节点实现会是 101 次。
    assert len(lineage.calls) == 3
    assert max(len(block) for block in lineage.calls) <= 64


def test_node_cap_sets_truncated() -> None:
    root = uuid4()
    edges = [
        _edge(
            index,
            LineageNodeType.EPISODE,
            root,
            LineageNodeType.ANNOTATION,
            uuid4(),
            LineageRelation.ANNOTATED_BY,
        )
        for index in range(MAX_GRAPH_NODES + 5)
    ]
    service = _service(_FakeLineage(edges))
    page = service.query(
        _SCOPE,
        node_type=LineageNodeType.EPISODE,
        node_id=root,
        direction=LineageDirection.OUT,
        depth=1,
        limit=100,
    )
    assert page.truncated is True
    assert len(page.edges) == 100
    assert page.next_cursor is not None


def test_edge_cap_sets_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application.episodes import graph as graph_module

    monkeypatch.setattr(graph_module, "MAX_GRAPH_EDGES", 5)
    root = uuid4()
    edges = [
        _edge(
            index,
            LineageNodeType.EPISODE,
            root,
            LineageNodeType.ANNOTATION,
            uuid4(),
            LineageRelation.ANNOTATED_BY,
        )
        for index in range(6)
    ]
    service = _service(_FakeLineage(edges))
    page = service.query(
        _SCOPE,
        node_type=LineageNodeType.EPISODE,
        node_id=root,
        direction=LineageDirection.OUT,
        depth=1,
        limit=100,
    )
    assert page.truncated is True
    assert len(page.edges) == 5
    assert page.next_cursor is None
