"""血缘登记、只追加写入与正/反向遍历用例。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.ports import AuditRepository, OutboxRepository
from app.application.episodes.commands import AppendLineageEdgeCommand
from app.application.episodes.ports import LineageRepository, UnitOfWork
from app.application.episodes.provenance import RawAssetOrigin, SourceProvenanceResolver
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import (
    DomainValidationError,
    LineageConflictError,
    ResourceNotFoundError,
)
from app.domain.episodes.lineage import LineageEdge, validate_supersedes_linear
from app.domain.identity.models import ProjectScope

MAX_DEPTH = 5
MAX_LIMIT = 100

# 只读投影节点类型：事实存在数据源表里，永远不在 lineage_node_refs 里登记。
PROJECTED_NODE_TYPES = frozenset({LineageNodeType.DATA_SOURCE, LineageNodeType.DATA_SOURCE_IMPORT})

# 可能通过投影边参与遍历的节点类型。RAW_ASSET 虽会登记，但它挂着
# 「导入任务产出了我」这条投影边，所以"节点未登记"不能直接判定为无边。
PROJECTION_ENDPOINT_TYPES = PROJECTED_NODE_TYPES | {LineageNodeType.RAW_ASSET}


@dataclass(frozen=True, slots=True)
class LineageTrace:
    """一次遍历的结果：真实的血缘边（含数据源链投影边）与资产来源结论。"""

    edges: list[LineageEdge]
    raw_asset_sources: list[RawAssetOrigin]


class LineageService:
    def __init__(
        self,
        lineage: LineageRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
        provenance: SourceProvenanceResolver,
    ) -> None:
        self._lineage = lineage
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        # 数据源链是读时投影，不写血缘表；见 application/episodes/provenance.py。
        self._provenance = provenance

    def register_node(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
        request_id: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self._lineage.ensure_node(scope, node_type=node_type, node_id=node_id, now=now)
        self._uow.commit()

    def append_edge(
        self,
        scope: ProjectScope,
        command: AppendLineageEdgeCommand,
        *,
        request_id: str | None = None,
    ) -> LineageEdge:
        now = datetime.now(UTC)
        edge = LineageEdge(
            edge_id=uuid4(),
            project_id=scope.project_id,
            from_node_type=command.from_node_type,
            from_node_id=command.from_node_id,
            to_node_type=command.to_node_type,
            to_node_id=command.to_node_id,
            relation=command.relation,
            metadata=command.metadata,
            created_by=scope.actor_id,
            created_at=now,
        )
        self._require_node(scope, edge.from_node_type, edge.from_node_id)
        self._require_node(scope, edge.to_node_type, edge.to_node_id)
        if self._lineage.exists_edge(
            scope,
            from_node_type=edge.from_node_type,
            from_node_id=edge.from_node_id,
            to_node_type=edge.to_node_type,
            to_node_id=edge.to_node_id,
            relation=edge.relation,
        ):
            raise LineageConflictError("血缘边已存在，禁止重复追加")
        if edge.relation is LineageRelation.SUPERSEDES:
            validate_supersedes_linear(self._lineage.list_supersedes_edges(scope), edge)
        created = self._lineage.add_edge(scope, edge)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="LINEAGE_EDGE",
            resource_id=created.edge_id,
            action="append",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={
                "relation": created.relation.value,
                "from_node_type": created.from_node_type.value,
                "to_node_type": created.to_node_type.value,
            },
            event_type="ingestion.lineage.edge_appended",
            event_payload={
                "edge_id": str(created.edge_id),
                "relation": created.relation.value,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return created

    def traverse(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
        direction: str = "forward",
        depth: int = 1,
        limit: int = 20,
        require_node: bool = True,
    ) -> list[LineageEdge]:
        """只要边的便捷入口；语义与 :meth:`trace` 完全一致。"""
        return self.trace(
            scope,
            node_type=node_type,
            node_id=node_id,
            direction=direction,
            depth=depth,
            limit=limit,
            require_node=require_node,
        ).edges

    def trace(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
        direction: str = "forward",
        depth: int = 1,
        limit: int = 20,
        require_node: bool = True,
    ) -> LineageTrace:
        """从 ``node_id`` 起按方向 BFS 遍历血缘边，并解析资产来源。

        ``require_node`` 默认为 ``True``：节点不在血缘图里就抛
        :class:`ResourceNotFoundError`，防止调用方用"空图"差异探测资源存在性。

        仅当调用方**已经用权威来源校验过该资源属于当前项目**时，才可以传
        ``False``：此时"节点未登记"是合法中间态（例如 DatasetVersion 已存在但
        还没转 READY、尚未写 INCLUDED_IN 边），应当返回空列表而不是伪装成
        资源不存在。

        遍历同时走两类边，方向语义一致：

        - 持久化边：``data_lineage_edges``；
        - 数据源链投影边（只读、不落库）：``DATA_SOURCE --PRODUCED-->
          DATA_SOURCE_IMPORT --PRODUCED--> RAW_ASSET``，由
          ``data_source_media_assets`` 的既有外键在遍历时展开。

        投影按 BFS 层批量查询（每层最多两次），不随节点数增加而线性增加。
        返回的 ``raw_asset_sources`` 是本次遍历中出现的每条 RawAsset 的
        来源结论，用于区分「无外部数据源」与「来源关联缺失」。
        """
        if direction not in {"forward", "backward"}:
            raise DomainValidationError("direction 只能是 forward 或 backward")
        if not 1 <= depth <= MAX_DEPTH:
            raise DomainValidationError(f"depth 必须在 1 到 {MAX_DEPTH} 之间")
        if not 1 <= limit <= MAX_LIMIT:
            raise DomainValidationError(f"limit 必须在 1 到 {MAX_LIMIT} 之间")
        try:
            normalized_type = LineageNodeType(node_type)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("node_type 取值非法") from exc
        projected_root = normalized_type in PROJECTED_NODE_TYPES
        if require_node and projected_root:
            raise DomainValidationError(
                "投影节点不能按节点登记表校验，须由调用方用权威仓储确认归属"
            )
        if require_node:
            self._require_node(scope, normalized_type, node_id)
        elif (
            not projected_root
            and normalized_type not in PROJECTION_ENDPOINT_TYPES
            and self._lineage.get_node(scope, node_type=normalized_type, node_id=node_id) is None
        ):
            # 节点未登记即无边可走；调用方已确认资源存在，空图是正确结果。
            # （RAW_ASSET 例外：它可能只有数据源链投影边而没有持久化边。）
            return LineageTrace(edges=[], raw_asset_sources=[])
        visited: set[tuple[LineageNodeType, UUID]] = {(normalized_type, node_id)}
        frontier: list[tuple[LineageNodeType, UUID]] = [(normalized_type, node_id)]
        edges: list[LineageEdge] = []
        seen_edges: set[UUID] = set()
        for _level in range(depth):
            if not frontier or len(edges) >= limit:
                break
            projected = self._projected_edges(scope, frontier, direction, limit)
            next_frontier: list[tuple[LineageNodeType, UUID]] = []
            for current in frontier:
                batch = [
                    *self._persisted_edges(scope, current, direction, limit),
                    *projected.get(current, ()),
                ]
                for edge in batch:
                    if edge.edge_id not in seen_edges:
                        seen_edges.add(edge.edge_id)
                        edges.append(edge)
                        if len(edges) >= limit:
                            break
                for neighbour in _neighbours(batch, direction):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        next_frontier.append(neighbour)
            frontier = next_frontier
        asset_ids = [
            node_id for node_type, node_id in visited if node_type is LineageNodeType.RAW_ASSET
        ]
        return LineageTrace(
            edges=edges,
            raw_asset_sources=self._provenance.origins(scope, asset_ids, limit=limit),
        )

    def _persisted_edges(
        self,
        scope: ProjectScope,
        node: tuple[LineageNodeType, UUID],
        direction: str,
        limit: int,
    ) -> list[LineageEdge]:
        current_type, current_id = node
        if direction == "forward":
            return self._lineage.list_edges_from(
                scope, node_type=current_type, node_id=current_id, limit=limit
            )
        return self._lineage.list_edges_to(
            scope, node_type=current_type, node_id=current_id, limit=limit
        )

    def _projected_edges(
        self,
        scope: ProjectScope,
        frontier: list[tuple[LineageNodeType, UUID]],
        direction: str,
        limit: int,
    ) -> dict[tuple[LineageNodeType, UUID], list[LineageEdge]]:
        """按当前层节点批量展开数据源链，返回「入口节点 -> 边」映射。"""
        result: dict[tuple[LineageNodeType, UUID], list[LineageEdge]] = {}
        source_ids = [
            node for node_type, node in frontier if node_type is LineageNodeType.DATA_SOURCE
        ]
        import_ids = [
            node for node_type, node in frontier if node_type is LineageNodeType.DATA_SOURCE_IMPORT
        ]
        if direction == "forward":
            if source_ids:
                for edge in self._provenance.source_to_import_edges(
                    scope, source_ids=source_ids, limit=limit
                ):
                    result.setdefault((LineageNodeType.DATA_SOURCE, edge.from_node_id), []).append(
                        edge
                    )
            if import_ids:
                for edge in self._provenance.import_to_asset_edges(
                    scope, import_ids=import_ids, limit=limit
                ):
                    result.setdefault(
                        (LineageNodeType.DATA_SOURCE_IMPORT, edge.from_node_id), []
                    ).append(edge)
            return result
        asset_ids = [node for node_type, node in frontier if node_type is LineageNodeType.RAW_ASSET]
        if asset_ids:
            for edge in self._provenance.import_to_asset_edges(
                scope, asset_ids=asset_ids, limit=limit
            ):
                result.setdefault((LineageNodeType.RAW_ASSET, edge.to_node_id), []).append(edge)
        if import_ids:
            for edge in self._provenance.source_to_import_edges(
                scope, import_ids=import_ids, limit=limit
            ):
                result.setdefault((LineageNodeType.DATA_SOURCE_IMPORT, edge.to_node_id), []).append(
                    edge
                )
        return result

    def _require_node(
        self,
        scope: ProjectScope,
        node_type: LineageNodeType,
        node_id: UUID,
    ) -> None:
        if self._lineage.get_node(scope, node_type=node_type, node_id=node_id) is None:
            raise ResourceNotFoundError(f"血缘节点({node_type.value})", node_id)


def _neighbours(
    edges: Iterable[LineageEdge],
    direction: str,
) -> list[tuple[LineageNodeType, UUID]]:
    """按方向取边的下游/上游端点。"""
    if direction == "forward":
        return [(edge.to_node_type, edge.to_node_id) for edge in edges]
    return [(edge.from_node_type, edge.from_node_id) for edge in edges]
