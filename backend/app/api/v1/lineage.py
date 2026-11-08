"""血缘查询与 RawAsset 下载地址 HTTP 路由。

这两个端点不带项目路径前缀，项目上下文由服务端校验的 ``project_id``
查询参数确定；仓储查询仍强制项目过滤，跨项目资源统一 404。
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_query_read_scope
from app.api.v1.episode_dependencies import EpisodeUseCasesDep
from app.api.v1.lineage_schemas import DownloadUrlOut, LineageEdgeOut, LineageResponse
from app.domain.episodes.enums import LineageNodeType
from app.domain.identity.models import ProjectScope

router = APIRouter(tags=["lineage"])

ReadScope = Annotated[ProjectScope, Depends(get_query_read_scope)]
Direction = Literal["forward", "reverse"]


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
    edges = use_cases.lineage.traverse(
        scope,
        node_type=LineageNodeType.EPISODE,
        node_id=episode_id,
        direction=_internal_direction(direction),
        depth=depth,
        limit=limit,
    )
    return LineageResponse(
        items=[LineageEdgeOut.from_domain(edge) for edge in edges],
        direction=direction,
    )


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
    edges = use_cases.lineage.traverse(
        scope,
        node_type=LineageNodeType.RAW_ASSET,
        node_id=asset_id,
        direction=_internal_direction(direction),
        depth=depth,
        limit=limit,
    )
    return LineageResponse(
        items=[LineageEdgeOut.from_domain(edge) for edge in edges],
        direction=direction,
    )


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


def _internal_direction(direction: str) -> str:
    # 应用层沿用 forward/backward 命名；对外契约为 forward/reverse。
    return "forward" if direction == "forward" else "backward"
