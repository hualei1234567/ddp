"""血缘节点投影与只追加边 SQLAlchemy 仓储。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import ConflictError, DomainValidationError, LineageConflictError
from app.domain.episodes.lineage import LineageEdge, LineageNodeRef
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import DataLineageEdgeModel, LineageNodeRefModel
from app.infrastructure.repositories._collection_sql import as_utc


class SqlLineageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_node(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
        now: datetime,
    ) -> LineageNodeRef:
        try:
            normalized_type = LineageNodeType(node_type)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("node_type 取值非法") from exc
        if not isinstance(node_id, UUID):
            raise DomainValidationError("node_id 必须是 UUID")
        existing = self.get_node(scope, node_type=normalized_type, node_id=node_id)
        if existing is not None:
            if not existing.is_active:
                self._session.execute(
                    update(LineageNodeRefModel)
                    .where(
                        LineageNodeRefModel.project_id == scope.project_id,
                        LineageNodeRefModel.node_type == normalized_type.value,
                        LineageNodeRefModel.node_id == node_id,
                    )
                    .values(is_active=True, updated_at=now)
                )
                self._session.flush()
                return LineageNodeRef(
                    project_id=scope.project_id,
                    node_type=normalized_type,
                    node_id=node_id,
                    is_active=True,
                    updated_at=now,
                )
            return existing
        row = LineageNodeRefModel(
            project_id=scope.project_id,
            node_type=normalized_type.value,
            node_id=node_id,
            is_active=True,
            updated_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            # 并发登记同一节点：读取既有投影。
            existing = self.get_node(scope, node_type=normalized_type, node_id=node_id)
            if existing is None:
                raise
            return existing
        return LineageNodeRef(
            project_id=scope.project_id,
            node_type=normalized_type,
            node_id=node_id,
            is_active=True,
            updated_at=now,
        )

    def get_node(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
    ) -> LineageNodeRef | None:
        try:
            normalized_type = LineageNodeType(node_type)
        except (TypeError, ValueError):
            return None
        row = self._session.scalar(
            select(LineageNodeRefModel).where(
                LineageNodeRefModel.project_id == scope.project_id,
                LineageNodeRefModel.node_type == normalized_type.value,
                LineageNodeRefModel.node_id == node_id,
            )
        )
        return self._from_node_row(row) if row is not None else None

    def mark_inactive(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType | str,
        node_id: UUID,
        now: datetime,
    ) -> None:
        try:
            normalized_type = LineageNodeType(node_type)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("node_type 取值非法") from exc
        result = self._session.execute(
            update(LineageNodeRefModel)
            .where(
                LineageNodeRefModel.project_id == scope.project_id,
                LineageNodeRefModel.node_type == normalized_type.value,
                LineageNodeRefModel.node_id == node_id,
            )
            .values(is_active=False, updated_at=now)
        )
        if result.rowcount == 0:
            raise ConflictError("血缘节点投影不存在")
        self._session.flush()

    def add_edge(self, scope: ProjectScope, edge: LineageEdge) -> LineageEdge:
        scope.ensure(edge.project_id)
        row = DataLineageEdgeModel(
            id=edge.edge_id,
            project_id=edge.project_id,
            from_node_type=edge.from_node_type.value,
            from_node_id=edge.from_node_id,
            to_node_type=edge.to_node_type.value,
            to_node_id=edge.to_node_id,
            relation=edge.relation.value,
            edge_metadata=dict(edge.metadata),
            created_by=scope.actor_id,
            created_at=edge.created_at,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(
                token in message
                for token in ("unique", "duplicate", "uq_", "foreign", "cycle", "linear")
            ):
                raise LineageConflictError("血缘边重复、跨项目或成环") from exc
            raise
        return edge

    def exists_edge(
        self,
        scope: ProjectScope,
        *,
        from_node_type: LineageNodeType,
        from_node_id: UUID,
        to_node_type: LineageNodeType,
        to_node_id: UUID,
        relation: LineageRelation,
    ) -> bool:
        row = self._session.scalar(
            select(DataLineageEdgeModel.id).where(
                DataLineageEdgeModel.project_id == scope.project_id,
                DataLineageEdgeModel.from_node_type == from_node_type.value,
                DataLineageEdgeModel.from_node_id == from_node_id,
                DataLineageEdgeModel.to_node_type == to_node_type.value,
                DataLineageEdgeModel.to_node_id == to_node_id,
                DataLineageEdgeModel.relation == relation.value,
            )
        )
        return row is not None

    def list_supersedes_edges(self, scope: ProjectScope) -> list[LineageEdge]:
        rows = self._session.scalars(
            select(DataLineageEdgeModel).where(
                DataLineageEdgeModel.project_id == scope.project_id,
                DataLineageEdgeModel.relation == LineageRelation.SUPERSEDES.value,
            )
        )
        return [self._from_edge_row(row) for row in rows]

    def list_edges_from(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
        limit: int = 100,
    ) -> list[LineageEdge]:
        rows = self._session.scalars(
            select(DataLineageEdgeModel)
            .where(
                DataLineageEdgeModel.project_id == scope.project_id,
                DataLineageEdgeModel.from_node_type == node_type.value,
                DataLineageEdgeModel.from_node_id == node_id,
            )
            .order_by(DataLineageEdgeModel.created_at, DataLineageEdgeModel.id)
            .limit(limit)
        )
        return [self._from_edge_row(row) for row in rows]

    def list_edges_to(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
        limit: int = 100,
    ) -> list[LineageEdge]:
        rows = self._session.scalars(
            select(DataLineageEdgeModel)
            .where(
                DataLineageEdgeModel.project_id == scope.project_id,
                DataLineageEdgeModel.to_node_type == node_type.value,
                DataLineageEdgeModel.to_node_id == node_id,
            )
            .order_by(DataLineageEdgeModel.created_at, DataLineageEdgeModel.id)
            .limit(limit)
        )
        return [self._from_edge_row(row) for row in rows]

    def _from_node_row(self, row: LineageNodeRefModel) -> LineageNodeRef:
        return LineageNodeRef(
            project_id=row.project_id,
            node_type=LineageNodeType(row.node_type),
            node_id=row.node_id,
            is_active=row.is_active,
            updated_at=as_utc(row.updated_at),
        )

    def _from_edge_row(self, row: DataLineageEdgeModel) -> LineageEdge:
        created_at = as_utc(row.created_at)
        if created_at is None:
            raise DomainValidationError("血缘边缺少 created_at")
        return LineageEdge(
            edge_id=row.id,
            project_id=row.project_id,
            from_node_type=LineageNodeType(row.from_node_type),
            from_node_id=row.from_node_id,
            to_node_type=LineageNodeType(row.to_node_type),
            to_node_id=row.to_node_id,
            relation=LineageRelation(row.relation),
            metadata=dict(row.edge_metadata or {}),
            created_by=row.created_by,
            created_at=created_at,
        )
