"""血缘登记、只追加写入与正/反向遍历用例。"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.ports import AuditRepository, OutboxRepository
from app.application.episodes.commands import AppendLineageEdgeCommand
from app.application.episodes.ports import LineageRepository, UnitOfWork
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


class LineageService:
    def __init__(
        self,
        lineage: LineageRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._lineage = lineage
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

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
    ) -> list[LineageEdge]:
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
        self._require_node(scope, normalized_type, node_id)
        queue: deque[tuple[LineageNodeType, UUID, int]] = deque([(normalized_type, node_id, 0)])
        visited: set[tuple[LineageNodeType, UUID]] = {(normalized_type, node_id)}
        edges: list[LineageEdge] = []
        seen_edges: set[UUID] = set()
        while queue:
            current_type, current_id, current_depth = queue.popleft()
            if current_depth >= depth or len(edges) >= limit:
                continue
            if direction == "forward":
                batch = self._lineage.list_edges_from(
                    scope,
                    node_type=current_type,
                    node_id=current_id,
                    limit=limit,
                )
                next_nodes = [(edge.to_node_type, edge.to_node_id) for edge in batch]
            else:
                batch = self._lineage.list_edges_to(
                    scope,
                    node_type=current_type,
                    node_id=current_id,
                    limit=limit,
                )
                next_nodes = [(edge.from_node_type, edge.from_node_id) for edge in batch]
            for edge in batch:
                if edge.edge_id not in seen_edges:
                    seen_edges.add(edge.edge_id)
                    edges.append(edge)
                    if len(edges) >= limit:
                        break
            for next_type, next_id in next_nodes:
                key = (next_type, next_id)
                if key not in visited:
                    visited.add(key)
                    queue.append((next_type, next_id, current_depth + 1))
        return edges

    def _require_node(
        self,
        scope: ProjectScope,
        node_type: LineageNodeType,
        node_id: UUID,
    ) -> None:
        if self._lineage.get_node(scope, node_type=node_type, node_id=node_id) is None:
            raise ResourceNotFoundError(f"血缘节点({node_type.value})", node_id)
