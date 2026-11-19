"""通用血缘图查询：受控深度/方向的只读 BFS 与游标分页。

范围与约束
----------
- 只读：不写 ``lineage_node_refs`` / ``data_lineage_edges``，也不回填历史；
- 数据来源与既有逐节点血缘一致：持久化边 + 数据源链只读投影；
- 深度硬上限 :data:`MAX_GRAPH_DEPTH`（3），节点/边各有硬上限，
  超限时 ``truncated=True``，**不静默给出完整假象**；
- 防环：BFS 全局 ``visited``，每个节点最多展开一次；自环/互指不会死循环；
- 批量邻边：每个 BFS 层按块调用仓储端口一次，避免逐节点 N+1；
- 分页：按 ``(level, created_at, edge_id)`` 键集游标翻页，非法游标 422，
  不静默回到首页。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.episodes.lineage import PROJECTED_NODE_TYPES
from app.application.episodes.ports import LineageRepository
from app.application.episodes.provenance import (
    DEFAULT_PROJECTION_LIMIT,
    RawAssetOrigin,
    SourceProvenanceResolver,
)
from app.domain.episodes.enums import LineageDirection, LineageNodeType
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.lineage import LineageEdge
from app.domain.identity.models import ProjectScope

# 图查询的硬上限：深度 3 足以覆盖「数据源 → 导入 → 资产 → Episode」与
# 「Episode → 标注/评测/数据集版本」两类真实链路；再深只放大分支爆炸风险。
MAX_GRAPH_DEPTH = 3
MAX_GRAPH_NODES = 500
MAX_GRAPH_EDGES = 1000
MAX_GRAPH_LIMIT = 100
DEFAULT_GRAPH_LIMIT = 20
MAX_GRAPH_CURSOR_LENGTH = 512

# 单条 SQL 的节点条件块大小：SQLite 对表达式树与绑定参数数量敏感，分块
# 既保持批量（不是逐节点）语义，又避免一次拼出 500 个 OR 条件。
_ADJACENCY_BATCH_SIZE = 64

NodeKey = tuple[LineageNodeType, UUID]
EdgeCursor = tuple[int, datetime, UUID]


@dataclass(frozen=True, slots=True)
class LineageGraphNode:
    node_type: LineageNodeType
    node_id: UUID
    level: int


@dataclass(frozen=True, slots=True)
class LineageGraphPage:
    root: LineageGraphNode
    root_found: bool
    direction: LineageDirection
    depth: int
    nodes: list[LineageGraphNode]
    edges: list[LineageEdge]
    next_cursor: str | None
    truncated: bool
    raw_asset_sources: list[RawAssetOrigin]


@dataclass(frozen=True, slots=True)
class _Snapshot:
    levels: dict[NodeKey, int]
    edges: list[tuple[int, LineageEdge]]
    truncated: bool


class LineageGraphService:
    def __init__(
        self,
        lineage: LineageRepository,
        provenance: SourceProvenanceResolver,
    ) -> None:
        self._lineage = lineage
        self._provenance = provenance

    def query(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
        direction: LineageDirection | str = LineageDirection.OUT,
        depth: int = 1,
        cursor: str | None = None,
        limit: int = DEFAULT_GRAPH_LIMIT,
        root_verified: bool = False,
    ) -> LineageGraphPage:
        """从 ``(node_type, node_id)`` 起按方向做受控 BFS 并返回一页。

        ``root_verified`` 只对**投影节点**（``DATA_SOURCE`` /
        ``DATA_SOURCE_IMPORT``）有意义：它们不在 ``lineage_node_refs`` 里，
        调用方必须先用权威仓储确认归属并传 ``True``；否则返回空图。持久化
        节点的存在性以节点投影为准，忽略该参数。
        """
        normalized_type = _node_type(node_type)
        normalized_direction = _direction(direction)
        if not isinstance(node_id, UUID):
            raise DomainValidationError("node_id 必须是 UUID")
        if not 1 <= depth <= MAX_GRAPH_DEPTH:
            raise DomainValidationError(f"depth 必须在 1 到 {MAX_GRAPH_DEPTH} 之间")
        if not 1 <= limit <= MAX_GRAPH_LIMIT:
            raise DomainValidationError(f"limit 必须在 1 到 {MAX_GRAPH_LIMIT} 之间")
        # 游标先校验：即使根节点不存在也要对非法游标返回 422，不能让
        # 「空图」掩盖客户端传错的游标。
        after = _decode_cursor(cursor)

        root_key: NodeKey = (normalized_type, node_id)
        root = LineageGraphNode(node_type=normalized_type, node_id=node_id, level=0)
        registered = (
            self._lineage.get_node(scope, node_type=normalized_type, node_id=node_id) is not None
        )

        if normalized_type in PROJECTED_NODE_TYPES:
            if not root_verified:
                # 没有权威归属确认就不能把「查不到」渲染成「存在但无边」。
                return _empty_page(root, normalized_direction, depth)
        elif normalized_type is not LineageNodeType.RAW_ASSET and not registered:
            # 节点未登记即无边可走；这是正常中间态，不是 404。
            return _empty_page(root, normalized_direction, depth)

        snapshot = self._traverse(scope, root_key, normalized_direction, depth)
        root_found = True
        if normalized_type is LineageNodeType.RAW_ASSET and not registered:
            # RAW_ASSET 可能只有数据源链投影边而没有持久化边；没有边
            # 才说明它在当前项目的图里确实不存在。
            root_found = any(
                root_key in _neighbours(edge, LineageDirection.BOTH) for _, edge in snapshot.edges
            )
            if not root_found:
                return _empty_page(root, normalized_direction, depth)

        return self._page(
            scope,
            snapshot=snapshot,
            root=root,
            direction=normalized_direction,
            depth=depth,
            after=after,
            limit=limit,
        )

    # ---------- BFS ----------

    def _traverse(
        self,
        scope: ProjectScope,
        root: NodeKey,
        direction: LineageDirection,
        depth: int,
    ) -> _Snapshot:
        levels: dict[NodeKey, int] = {root: 0}
        edges: dict[UUID, tuple[int, LineageEdge]] = {}
        visited: set[NodeKey] = {root}
        frontier: list[NodeKey] = [root]
        truncated = False
        for level in range(depth):
            if not frontier:
                break
            remaining = MAX_GRAPH_EDGES - len(edges)
            if remaining <= 0:
                truncated = True
                break
            batch = self._incident_edges(scope, frontier, direction, remaining + 1)
            if len(batch) > remaining:
                truncated = True
                batch = batch[:remaining]
            new_keys: list[NodeKey] = []
            seen_new: set[NodeKey] = set()
            for edge in batch:
                edges.setdefault(edge.edge_id, (level, edge))
                for neighbour in _neighbours(edge, direction):
                    if neighbour in visited or neighbour in seen_new:
                        continue
                    seen_new.add(neighbour)
                    new_keys.append(neighbour)
            next_frontier: list[NodeKey] = []
            node_cap_hit = False
            for key in new_keys:
                if len(levels) >= MAX_GRAPH_NODES:
                    node_cap_hit = True
                    truncated = True
                    break
                levels[key] = level + 1
                visited.add(key)
                next_frontier.append(key)
            next_frontier.sort(key=_key_order)
            frontier = next_frontier
            if node_cap_hit:
                break
        return _Snapshot(levels=levels, edges=list(edges.values()), truncated=truncated)

    def _incident_edges(
        self,
        scope: ProjectScope,
        frontier: Sequence[NodeKey],
        direction: LineageDirection,
        want: int,
    ) -> list[LineageEdge]:
        """取当前层节点的邻边：持久化边分块批量查询 + 数据源链投影边。

        返回按 ``(created_at, edge_id)`` 排序的前 ``want`` 条；调用方传
        ``remaining + 1`` 用于判断是否触顶。重复边（同一层两个节点同时命中
        同一条边）按 ``edge_id`` 去重。
        """
        collected: dict[UUID, LineageEdge] = {}
        for start in range(0, len(frontier), _ADJACENCY_BATCH_SIZE):
            chunk = frontier[start : start + _ADJACENCY_BATCH_SIZE]
            for edge in self._lineage.list_edges_for_nodes(
                scope,
                nodes=chunk,
                direction=direction,
                limit=want,
            ):
                collected.setdefault(edge.edge_id, edge)
        for edge in self._projected_edges(scope, frontier, direction, want):
            collected.setdefault(edge.edge_id, edge)
        return sorted(collected.values(), key=_edge_order)[:want]

    def _projected_edges(
        self,
        scope: ProjectScope,
        frontier: Sequence[NodeKey],
        direction: LineageDirection,
        limit: int,
    ) -> list[LineageEdge]:
        """复用数据源链只读投影；投影本身已按层批量查询。"""
        source_ids = [
            node_id for node_type, node_id in frontier if node_type is LineageNodeType.DATA_SOURCE
        ]
        import_ids = [
            node_id
            for node_type, node_id in frontier
            if node_type is LineageNodeType.DATA_SOURCE_IMPORT
        ]
        asset_ids = [
            node_id for node_type, node_id in frontier if node_type is LineageNodeType.RAW_ASSET
        ]
        # 投影查询的 limit 与本次剩余边预算一致，避免在硬上限之外再静默截断；
        # 真正超出时由 ``_incident_edges`` 的统一排序与 ``truncated`` 表达。
        bounded = limit
        edges: list[LineageEdge] = []
        if direction in (LineageDirection.OUT, LineageDirection.BOTH):
            if source_ids:
                edges.extend(
                    self._provenance.source_to_import_edges(
                        scope, source_ids=source_ids, limit=bounded
                    )
                )
            if import_ids:
                edges.extend(
                    self._provenance.import_to_asset_edges(
                        scope, import_ids=import_ids, limit=bounded
                    )
                )
        if direction in (LineageDirection.IN, LineageDirection.BOTH):
            if asset_ids:
                edges.extend(
                    self._provenance.import_to_asset_edges(
                        scope, asset_ids=asset_ids, limit=bounded
                    )
                )
            if import_ids:
                edges.extend(
                    self._provenance.source_to_import_edges(
                        scope, import_ids=import_ids, limit=bounded
                    )
                )
        return edges

    # ---------- 分页 ----------

    def _page(
        self,
        scope: ProjectScope,
        *,
        snapshot: _Snapshot,
        root: LineageGraphNode,
        direction: LineageDirection,
        depth: int,
        after: EdgeCursor | None,
        limit: int,
    ) -> LineageGraphPage:
        ordered = sorted(
            (
                (level, edge)
                for level, edge in snapshot.edges
                if _endpoint_key(edge, "from") in snapshot.levels
                and _endpoint_key(edge, "to") in snapshot.levels
            ),
            key=lambda item: (item[0], item[1].created_at, item[1].edge_id),
        )
        if after is not None:
            ordered = [
                item for item in ordered if (item[0], item[1].created_at, item[1].edge_id) > after
            ]
        page_items = ordered[:limit]
        next_cursor = (
            _encode_cursor(page_items[-1][0], page_items[-1][1]) if len(ordered) > limit else None
        )
        root_key: NodeKey = (root.node_type, root.node_id)
        page_keys: set[NodeKey] = {root_key}
        for _level, edge in page_items:
            page_keys.add(_endpoint_key(edge, "from"))
            page_keys.add(_endpoint_key(edge, "to"))
        nodes = [
            LineageGraphNode(node_type=key[0], node_id=key[1], level=snapshot.levels[key])
            for key in sorted(
                page_keys,
                key=lambda key: (snapshot.levels[key], key[0].value, str(key[1])),
            )
        ]
        asset_ids = [node.node_id for node in nodes if node.node_type is LineageNodeType.RAW_ASSET]
        origins = self._provenance.origins(scope, asset_ids, limit=DEFAULT_PROJECTION_LIMIT)
        return LineageGraphPage(
            root=root,
            root_found=True,
            direction=direction,
            depth=depth,
            nodes=nodes,
            edges=[edge for _level, edge in page_items],
            next_cursor=next_cursor,
            truncated=snapshot.truncated,
            raw_asset_sources=origins,
        )


def _empty_page(
    root: LineageGraphNode,
    direction: LineageDirection,
    depth: int,
) -> LineageGraphPage:
    return LineageGraphPage(
        root=root,
        root_found=False,
        direction=direction,
        depth=depth,
        nodes=[],
        edges=[],
        next_cursor=None,
        truncated=False,
        raw_asset_sources=[],
    )


def _node_type(value: LineageNodeType | str) -> LineageNodeType:
    try:
        return LineageNodeType(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("node_type 取值非法") from exc


def _direction(value: LineageDirection | str) -> LineageDirection:
    try:
        return LineageDirection(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("direction 取值非法") from exc


def _endpoint_key(edge: LineageEdge, side: str) -> NodeKey:
    if side == "from":
        return (edge.from_node_type, edge.from_node_id)
    return (edge.to_node_type, edge.to_node_id)


def _neighbours(edge: LineageEdge, direction: LineageDirection) -> list[NodeKey]:
    if direction is LineageDirection.OUT:
        return [_endpoint_key(edge, "to")]
    if direction is LineageDirection.IN:
        return [_endpoint_key(edge, "from")]
    return [_endpoint_key(edge, "from"), _endpoint_key(edge, "to")]


def _key_order(key: NodeKey) -> tuple[str, str]:
    return (key[0].value, str(key[1]))


def _edge_order(edge: LineageEdge) -> tuple[datetime, str]:
    return (edge.created_at, str(edge.edge_id))


def _encode_cursor(level: int, edge: LineageEdge) -> str:
    payload = json.dumps(
        {
            "l": level,
            "t": edge.created_at.astimezone(UTC).isoformat(),
            "i": str(edge.edge_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> EdgeCursor | None:
    """解析键集游标；任何解析失败都是 422，不静默回到首页。"""
    if value is None or value == "":
        return None
    if len(value) > MAX_GRAPH_CURSOR_LENGTH:
        raise DomainValidationError("cursor 非法")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("游标结构非法")
        level = payload["l"]
        timestamp = payload["t"]
        edge_id = payload["i"]
        if isinstance(level, bool) or not isinstance(level, int):
            raise ValueError("游标层级非法")
        if not isinstance(timestamp, str) or not isinstance(edge_id, str):
            raise ValueError("游标字段类型非法")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("游标时间缺少时区")
        parsed_id = UUID(edge_id)
    except (
        binascii.Error,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise DomainValidationError("cursor 非法") from exc
    if not 0 <= level <= MAX_GRAPH_DEPTH:
        raise DomainValidationError("cursor 非法")
    return (level, parsed.astimezone(UTC), parsed_id)


__all__ = [
    "DEFAULT_GRAPH_LIMIT",
    "MAX_GRAPH_CURSOR_LENGTH",
    "MAX_GRAPH_DEPTH",
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_LIMIT",
    "MAX_GRAPH_NODES",
    "LineageGraphNode",
    "LineageGraphPage",
    "LineageGraphService",
]
