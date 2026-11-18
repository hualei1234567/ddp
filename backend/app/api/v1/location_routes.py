"""场地 HTTP 路由（N9 多基地）。

路由只做协议适配（路径、请求体、``If-Match``、``Idempotency-Key``），业务规则
在 ``app/application/locations/use_cases.py``；项目隔离与权限由服务端依赖强制。

形状与 ``collection_asset_routes.py`` 一致（``/projects/{project_id}/...``）：
``POST`` 创建 + ``GET`` 列表 + ``GET`` 详情；额外提供 ``PATCH`` 修改
（素材没有 update，但场地会被设备与任务长期引用，改名/挪地址是真实需求）。

**没有 ``DELETE``**：三处复合外键引用本表，删除被引用的场地会被数据库拒绝。
理由与替代路径见用例模块头注释。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.location_dependencies import LocationServicesDep
from app.api.v1.location_schemas import (
    LocationCreate,
    LocationOut,
    LocationPatch,
    SiteOverviewOut,
)
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import PageOut
from app.application.locations import NewLocation
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["locations"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


@router.get("/sites/overview", response_model=PageOut[SiteOverviewOut])
def list_site_overview(
    scope: ReadScope,
    services: LocationServicesDep,
    kind: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[SiteOverviewOut]:
    """多基地资源只读概览：按场地聚合设备/传感器/采集任务数。

    路径用 ``sites`` 而不是 ``locations``：这是面向"基地运营"的聚合视图，
    不是场地档案本身；也避免与 ``/locations/{location_id}`` 的 UUID 路径参数
    产生匹配歧义。数据源仍是同一张 ``locations`` 表，包含全部 ``kind``。

    只读：没有 POST/PATCH/DELETE，也不做调度与计费。
    """
    page = services.locations.list_site_overview(
        scope,
        kind=kind,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[SiteOverviewOut.from_domain(entry) for entry in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/locations", response_model=PageOut[LocationOut])
def list_locations(
    scope: ReadScope,
    services: LocationServicesDep,
    kind: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[LocationOut]:
    page = services.locations.list_locations(scope, kind=kind, cursor=cursor, limit=limit)
    return PageOut(
        items=[LocationOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/locations", response_model=LocationOut, status_code=201)
def create_location(
    payload: LocationCreate,
    request: Request,
    scope: WriteScope,
    services: LocationServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> LocationOut:
    location = services.locations.create_location(
        scope,
        NewLocation(
            code=payload.code,
            name=payload.name,
            kind=payload.kind,
            address=payload.address,
            notes=payload.notes,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return LocationOut.from_domain(location)


@router.get("/locations/{location_id}", response_model=LocationOut)
def get_location(
    location_id: UUID,
    scope: ReadScope,
    services: LocationServicesDep,
) -> LocationOut:
    return LocationOut.from_domain(services.locations.get_location(scope, location_id))


@router.patch("/locations/{location_id}", response_model=LocationOut)
def patch_location(
    location_id: UUID,
    payload: LocationPatch,
    request: Request,
    scope: WriteScope,
    services: LocationServicesDep,
    expected_version: ExpectedVersion,
) -> LocationOut:
    # 只提交显式出现的字段：``exclude_unset`` 让"未提供"与"显式置 null"
    # 严格区分——否则调用方无法清空一个填错的地址/备注。
    changes = payload.model_dump(exclude_unset=True)
    updated = services.locations.update_location(
        scope,
        location_id,
        expected_version=expected_version,
        changes=changes,
        request_id=_request_id(request),
    )
    return LocationOut.from_domain(updated)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
