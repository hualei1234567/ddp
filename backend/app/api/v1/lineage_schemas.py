"""血缘与下载地址 HTTP DTO。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.episodes.lineage import LineageEdge


class LineageEdgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: UUID
    project_id: UUID
    from_node_type: str
    from_node_id: UUID
    to_node_type: str
    to_node_id: UUID
    relation: str
    metadata: dict[str, object]
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, edge: LineageEdge) -> LineageEdgeOut:
        return cls(
            edge_id=edge.edge_id,
            project_id=edge.project_id,
            from_node_type=edge.from_node_type.value,
            from_node_id=edge.from_node_id,
            to_node_type=edge.to_node_type.value,
            to_node_id=edge.to_node_id,
            relation=edge.relation.value,
            metadata=dict(edge.metadata),
            created_by=edge.created_by,
            created_at=edge.created_at,
        )


class LineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LineageEdgeOut]
    direction: str


class DownloadUrlOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    expires_at: datetime
