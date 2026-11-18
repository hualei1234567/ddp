"""统一资产目录只读检索路由（N5-001）。

路径 ``GET /projects/{project_id}/assets/search`` 是**跨资源**检索视图，
不是任何一个既有资源列表的替代：各资源自身的过滤/详情仍走原端点。

- 只读：没有 POST/PATCH/DELETE；
- 项目隔离与权限由服务端依赖强制，客户端 ``project_id`` 只用于定位；
- ``q`` 必填且长度为 1..64；空白串由用例拒绝为 422；
- 分页为不透明复合游标，非法游标返回 422，不静默回到首页。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_project_scope
from app.api.v1.asset_catalog_dependencies import AssetCatalogServicesDep
from app.api.v1.asset_catalog_schemas import AssetCatalogEntryOut
from app.api.v1.schemas import PageOut
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["asset-catalog"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


@router.get("/assets/search", response_model=PageOut[AssetCatalogEntryOut])
def search_assets(
    scope: ReadScope,
    services: AssetCatalogServicesDep,
    q: Annotated[str, Query(min_length=1, max_length=64)],
    kind: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[AssetCatalogEntryOut]:
    """按资源标识前缀检索项目内资产。

    默认检索全部类型；``kind`` 非法返回 422。匹配走既有索引前缀，不做
    ``%关键词%`` 无界扫描。
    """
    page = services.catalog.search(
        scope,
        q=q,
        kind=kind,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[AssetCatalogEntryOut.from_domain(entry) for entry in page.items],
        next_cursor=page.next_cursor,
    )
