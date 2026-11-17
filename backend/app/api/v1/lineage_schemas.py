"""血缘与下载地址 HTTP DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.episodes.lineage import LineageTrace
from app.application.episodes.provenance import RawAssetOrigin, SourceLink
from app.domain.episodes.lineage import LineageEdge
from app.domain.episodes.provenance import RawAssetSourceStatus

# 对外契约为 forward/reverse；应用层沿用的是 forward/backward。
Direction = Literal["forward", "reverse"]


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


class DataSourceNodeOut(BaseModel):
    """``DATA_SOURCE`` 节点属性（只读投影，来自 ``data_sources`` 表）。"""

    model_config = ConfigDict(extra="forbid")

    data_source_id: UUID
    code: str
    name: str
    kind: str
    repo_id: str
    revision: str

    @classmethod
    def from_link(cls, link: SourceLink) -> DataSourceNodeOut:
        return cls(
            data_source_id=link.data_source_id,
            code=link.data_source_code,
            name=link.data_source_name,
            kind=link.data_source_kind,
            repo_id=link.repo_id,
            revision=link.revision,
        )


class DataSourceImportNodeOut(BaseModel):
    """``DATA_SOURCE_IMPORT`` 节点属性（只读投影，来自 ``data_source_imports``）。"""

    model_config = ConfigDict(extra="forbid")

    import_id: UUID
    status: str
    scope: str
    started_at: datetime | None
    finished_at: datetime | None
    imported_file_count: int | None

    @classmethod
    def from_link(cls, link: SourceLink) -> DataSourceImportNodeOut:
        return cls(
            import_id=link.import_id,
            status=link.import_status,
            scope=link.import_scope,
            started_at=link.import_started_at,
            finished_at=link.import_finished_at,
            imported_file_count=link.imported_file_count,
        )


class RawAssetSourceOut(BaseModel):
    """一条 RawAsset 的外部来源结论。

    ``status`` 区分三种情况，调用方不能只看 ``data_sources`` 是否为空：
    ``NO_EXTERNAL_SOURCE`` 是正常状态（手工上传/设备采集），
    ``SOURCE_LINK_MISSING`` 是数据不一致（声称导入但查不到媒体行），
    ``UNKNOWN_ASSET`` 表示资产在血缘图里但本项目查不到。
    """

    model_config = ConfigDict(extra="forbid")

    raw_asset_id: UUID
    status: RawAssetSourceStatus
    data_sources: list[DataSourceNodeOut]
    imports: list[DataSourceImportNodeOut]

    @classmethod
    def from_origin(cls, origin: RawAssetOrigin) -> RawAssetSourceOut:
        sources: dict[UUID, DataSourceNodeOut] = {}
        imports: dict[UUID, DataSourceImportNodeOut] = {}
        for link in origin.links:
            sources.setdefault(link.data_source_id, DataSourceNodeOut.from_link(link))
            imports.setdefault(link.import_id, DataSourceImportNodeOut.from_link(link))
        return cls(
            raw_asset_id=origin.raw_asset_id,
            status=origin.status,
            data_sources=list(sources.values()),
            imports=list(imports.values()),
        )


class LineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LineageEdgeOut]
    direction: str
    # 本次遍历中出现的 RawAsset 的来源结论；既有调用方可以忽略该字段。
    raw_asset_sources: list[RawAssetSourceOut] = Field(default_factory=list)


class DownloadUrlOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    expires_at: datetime


def to_internal_direction(direction: str) -> str:
    return "forward" if direction == "forward" else "backward"


def build_lineage_response(trace: LineageTrace, direction: str) -> LineageResponse:
    """把遍历结果转成 HTTP DTO；三个血缘端点共用同一份转换。"""
    return LineageResponse(
        items=[LineageEdgeOut.from_domain(edge) for edge in trace.edges],
        direction=direction,
        raw_asset_sources=[
            RawAssetSourceOut.from_origin(origin) for origin in trace.raw_asset_sources
        ],
    )
