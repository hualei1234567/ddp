"""审计日志查询路由（只读）。

两个入口：

- ``GET /projects/{project_id}/audit-logs``：跨资源的分页检索；
- ``GET /projects/{project_id}/audit-logs/{resource_type}/{resource_id}``：
  单个资源的时间线，是「这个对象身上发生过什么」最常用的问法。

为什么保留时间线端点，而不是只让调用方拼通用过滤参数：资源时间线是追溯
场景的第一入口，独立路径让前端与外部审计工具不必知道 ``resource_type`` /
``resource_id`` 两个参数名，也让访问日志里能直接看出"有人在查某个对象"；
语义上它等价于通用端点固定这两个过滤条件。

权限：审计日志包含操作者身份与操作原因，不能对所有项目成员开放。这里复用
**项目负责人作用域** ``get_owner_scope``（owner 或平台管理员），与数据集归档、
密钥授予等治理动作同一口径；viewer/editor 一律 403。理由：可追溯数据本身
就是敏感信息，能读审计等于能看到全项目的操作画像与人员行为。

隔离与不泄露存在性：作用域硬绑 ``project_id``。时间线查别家项目的资源 ID
返回 200 + 空列表——与"本项目没有记录"响应完全一致，不泄露该 ID 是否存在。

只读：不提供 POST/PATCH/DELETE，审计记录不可篡改。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import DbSession, get_owner_scope
from app.api.v1.audit_log_schemas import AuditLogOut
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.schemas import PageOut
from app.application.audit import (
    MAX_QUERY_LIMIT,
    AuditLogQueryRepository,
    list_audit_logs,
)
from app.core.errors import ValidationFailedError
from app.domain.audit.models import AuditQuery, AuditResult
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.audit_logs import SqlAuditLogQueryRepository

router = APIRouter(
    prefix="/projects/{project_id}/audit-logs",
    tags=["audit-logs"],
)

# 审计读取要求项目负责人（或平台管理员）：只读但敏感。
AuditReadScope = Annotated[ProjectScope, Depends(get_owner_scope)]
# 资源类型是各业务用例写入的稳定短名（如 ``RAW_ASSET``、``INGESTION_JOB``）；
# 只限制长度，不枚举——枚举会与写入方脱节，新增资源类型不该改这里。
ResourceTypePath = Annotated[str, Path(min_length=1, max_length=64)]
ResourceTypeQuery = Annotated[str | None, Query(min_length=1, max_length=64)]
ActionQuery = Annotated[str | None, Query(min_length=1, max_length=64)]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_QUERY_LIMIT)]


def get_audit_log_repository(session: DbSession) -> AuditLogQueryRepository:
    return SqlAuditLogQueryRepository(session)


AuditRepositoryDep = Annotated[
    AuditLogQueryRepository,
    Depends(get_audit_log_repository),
]


def _require_aware(value: datetime | None, field: str) -> datetime | None:
    """时间过滤必须带时区。

    naive 时间无法确定是 UTC 还是本地时间，静默按某个时区解释会让追溯结果
    "看起来对、实际错一截"——审计场景不能接受这种模糊，直接 422。
    """
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailedError(f"{field} 必须携带时区，例如 2026-01-01T00:00:00Z")
    return value.astimezone(UTC)


def _build_query(
    *,
    resource_type: str | None,
    resource_id: UUID | None,
    action: str | None,
    actor_user_id: UUID | None,
    result: AuditResult | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
) -> AuditQuery:
    start = _require_aware(occurred_from, "occurred_from")
    end = _require_aware(occurred_to, "occurred_to")
    if start is not None and end is not None and start > end:
        raise ValidationFailedError("occurred_from 不能晚于 occurred_to")
    return AuditQuery.build(
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        actor_user_id=actor_user_id,
        result=result,
        occurred_from=start,
        occurred_to=end,
    )


def _page(
    scope: ProjectScope,
    repository: AuditLogQueryRepository,
    query: AuditQuery,
    *,
    cursor: str | None,
    limit: int,
) -> PageOut[AuditLogOut]:
    entries = list_audit_logs(
        repository,
        scope,
        query,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(entries, limit=limit, id_of=lambda entry: entry.audit_id)
    return PageOut(
        items=[AuditLogOut.from_entry(entry) for entry in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("", response_model=PageOut[AuditLogOut])
def list_audit_logs_endpoint(
    scope: AuditReadScope,
    repository: AuditRepositoryDep,
    resource_type: ResourceTypeQuery = None,
    resource_id: UUID | None = None,
    action: ActionQuery = None,
    actor_user_id: UUID | None = None,
    result: AuditResult | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[AuditLogOut]:
    """按条件分页检索审计日志，按发生时间倒序。

    全部条件在仓储层下推为 SQL；``occurred_from`` / ``occurred_to`` 为闭区间，
    必须带时区。``result`` 只接受 ``SUCCESS`` / ``FAILURE``（与表 CHECK 约束同源）。
    """
    query = _build_query(
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        actor_user_id=actor_user_id,
        result=result,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return _page(scope, repository, query, cursor=cursor, limit=limit)


@router.get(
    "/{resource_type}/{resource_id}",
    response_model=PageOut[AuditLogOut],
)
def get_resource_timeline(
    resource_type: ResourceTypePath,
    resource_id: UUID,
    scope: AuditReadScope,
    repository: AuditRepositoryDep,
    action: ActionQuery = None,
    actor_user_id: UUID | None = None,
    result: AuditResult | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[AuditLogOut]:
    """单个资源的时间线（发生时间倒序）。

    资源不属于当前项目时返回 200 + 空列表，与"本项目无该记录"不可区分。
    """
    query = _build_query(
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        actor_user_id=actor_user_id,
        result=result,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return _page(scope, repository, query, cursor=cursor, limit=limit)


__all__ = ["router"]
