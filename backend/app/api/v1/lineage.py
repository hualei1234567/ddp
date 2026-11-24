"""血缘查询与 RawAsset 下载地址 HTTP 路由。

Episode / RawAsset 两个血缘端点不带项目路径前缀，项目上下文由服务端校验的
``project_id`` 查询参数确定；数据源血缘端点按项目路径定位。三者都强制项目
过滤，跨项目资源统一 404。

三个端点共用同一套遍历与 DTO 组装（``app.application.episodes.lineage`` /
``app.api.v1.lineage_schemas``），不各自实现一份。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_scope, get_query_read_scope
from app.api.v1.datasource_routes import DataSourceServicesDep
from app.api.v1.episode_dependencies import EpisodeUseCasesDep
from app.api.v1.lineage_schemas import (
    Direction,
    DownloadUrlOut,
    LineageGraphResponse,
    LineageResponse,
    build_lineage_graph_response,
    build_lineage_response,
    to_internal_direction,
)
from app.application.episodes.graph import MAX_GRAPH_CURSOR_LENGTH, MAX_GRAPH_DEPTH, MAX_GRAPH_LIMIT
from app.domain.episodes.enums import LineageDirection, LineageNodeType
from app.domain.episodes.errors import ResourceNotFoundError
from app.domain.identity.models import ProjectScope

router = APIRouter(tags=["lineage"])

ReadScope = Annotated[ProjectScope, Depends(get_query_read_scope)]
PathReadScope = Annotated[ProjectScope, Depends(get_project_scope)]


@router.get("/episodes/{episode_id}/lineage", response_model=LineageResponse)
def get_episode_lineage(
    episode_id: UUID,
    scope: ReadScope,
    use_cases: EpisodeUseCasesDep,
    direction: Direction = "forward",
    depth: Annotated[int, Query(ge=1, le=5)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LineageResponse:
    # 先校验 Episode 存在且属于当前项目，再遍历血缘，避免枚举空节点差异。
    use_cases.episodes.get_episode(scope, episode_id)
    trace = use_cases.lineage.trace(
        scope,
        node_type=LineageNodeType.EPISODE,
        node_id=episode_id,
        direction=to_internal_direction(direction),
        depth=depth,
        limit=limit,
    )
    return build_lineage_response(trace, direction)


@router.get("/raw-assets/{asset_id}/lineage", response_model=LineageResponse)
def get_raw_asset_lineage(
    asset_id: UUID,
    scope: ReadScope,
    use_cases: EpisodeUseCasesDep,
    direction: Direction = "forward",
    depth: Annotated[int, Query(ge=1, le=5)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LineageResponse:
    use_cases.assets.get_asset(scope, asset_id)
    trace = use_cases.lineage.trace(
        scope,
        node_type=LineageNodeType.RAW_ASSET,
        node_id=asset_id,
        direction=to_internal_direction(direction),
        depth=depth,
        limit=limit,
        # 资产归属已由上面的权威查询确认：还没被任何 Episode 引用的资产
        # 本来就没有血缘节点，此时应返回空图（并在 raw_asset_sources 里
        # 给出「无外部数据源」等结论），而不是伪装成资产不存在。
        require_node=False,
    )
    return build_lineage_response(trace, direction)


@router.get(
    "/projects/{project_id}/data-sources/{data_source_id}/lineage",
    response_model=LineageResponse,
)
def get_data_source_lineage(
    data_source_id: UUID,
    scope: PathReadScope,
    data_sources: DataSourceServicesDep,
    lineage_use_cases: EpisodeUseCasesDep,
    direction: Direction = "forward",
    depth: Annotated[int, Query(ge=1, le=5)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LineageResponse:
    """数据源的下游血缘：它发起了哪些导入任务、产出了哪些 RawAsset/Episode。

    数据源节点是**只读投影**（事实在 ``data_sources`` 表，不在血缘节点表），
    所以先用数据源仓储确认归属——跨项目数据源一律 404，不泄露存在性——
    再以 ``require_node=False`` 遍历；"节点未登记"是正常状态，返回空图而不是 404。
    """
    data_sources.get_source(scope, data_source_id)
    trace = lineage_use_cases.lineage.trace(
        scope,
        node_type=LineageNodeType.DATA_SOURCE,
        node_id=data_source_id,
        direction=to_internal_direction(direction),
        depth=depth,
        limit=limit,
        require_node=False,
    )
    return build_lineage_response(trace, direction)


@router.get(
    "/projects/{project_id}/lineage/graph",
    response_model=LineageGraphResponse,
)
def get_lineage_graph(
    scope: PathReadScope,
    use_cases: EpisodeUseCasesDep,
    data_sources: DataSourceServicesDep,
    node_type: LineageNodeType,
    node_id: UUID,
    direction: LineageDirection = LineageDirection.OUT,
    depth: Annotated[int, Query(ge=1, le=MAX_GRAPH_DEPTH)] = 1,
    cursor: Annotated[str | None, Query(max_length=MAX_GRAPH_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_GRAPH_LIMIT)] = 20,
) -> LineageGraphResponse:
    """从任意节点出发的受控图查询（只读）。

    - ``direction``：``OUT`` 下游 / ``IN`` 上游 / ``BOTH`` 两侧；
    - ``depth`` 硬上限 3，节点与边另有服务端硬上限，触顶时 ``truncated=true``；
    - ``DATA_SOURCE`` / ``DATA_SOURCE_IMPORT`` / ``UPLOAD_SESSION`` 是只读投影
      节点，先用权威仓储确认归属；跨项目一律 404，不泄露存在性；
    - 其他节点未登记时返回空图（``root_found=false``）而不是 404——节点尚未
      进入血缘图是正常中间态，用状态码差异反而会变成存在性探针。
    """
    root_verified = False
    if node_type is LineageNodeType.DATA_SOURCE:
        data_sources.get_source(scope, node_id)
        root_verified = True
    elif node_type is LineageNodeType.DATA_SOURCE_IMPORT:
        data_sources.get_import(scope, node_id)
        root_verified = True
    elif node_type is LineageNodeType.UPLOAD_SESSION:
        # 上传会话同样是只读投影节点（事实在 ``upload_sessions`` +
        # ``raw_asset_links``），必须先确认归属，否则跨项目查询会变成探针。
        if use_cases.sessions is None or not use_cases.sessions.session_exists(scope, node_id):
            raise ResourceNotFoundError("上传会话", node_id)
        root_verified = True
    page = use_cases.lineage_graph.query(
        scope,
        node_type=node_type,
        node_id=node_id,
        direction=direction,
        depth=depth,
        cursor=cursor,
        limit=limit,
        root_verified=root_verified,
    )
    return build_lineage_graph_response(page)


@router.get("/raw-assets/{asset_id}/download-url", response_model=DownloadUrlOut)
def get_raw_asset_download_url(
    asset_id: UUID,
    request: Request,
    scope: ReadScope,
    use_cases: EpisodeUseCasesDep,
) -> DownloadUrlOut:
    signed = use_cases.downloads.create_download_url(
        scope,
        asset_id,
        request_id=_request_id(request),
    )
    return DownloadUrlOut(url=signed.url, expires_at=signed.expires_at)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
