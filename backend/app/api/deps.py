"""API 依赖：身份、数据库会话、项目上下文与权限。

路由必须通过 :func:`require_project_permission` 或
:class:`app.domain.identity.models.ProjectContext` 做服务端授权，
不得信任客户端传入的 ``project_id``。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from app.application.identity.access import ProjectAccessService
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, ForbiddenError
from app.domain.identity.models import (
    PlatformRole,
    Principal,
    ProjectAction,
    ProjectContext,
    ProjectScope,
)
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.identity import (
    SqlProjectMembershipRepository,
    SqlProjectRepository,
)

# 允许使用本地 Header 认证的来源地址。
# ``testclient`` 只可能来自 Starlette 进程内 ASGI 测试客户端，不会出现在真实网络中。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _is_loopback(request: Request) -> bool:
    """仅信任 ASGI Server 提供的对端地址，不读取任何转发头。"""
    client = request.client
    return client is not None and client.host in _LOOPBACK_HOSTS


def get_current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """阶段 0 身份适配器：本地 loopback 显式开关下才允许请求头模拟用户。

    安全约束：
    - 默认关闭（``DDP_LOCAL_HEADER_AUTH=false``），生产环境永不启用；
    - 只接受 loopback 来源，避免把开发便利暴露给远端；
    - 平台角色固定为 ``member``，任何请求头都不能提升平台权限。

    接入 JWT/OIDC 时应替换本函数实现，路由与服务层无需感知具体认证方式。
    """
    if not settings.is_local:
        raise AuthenticationError("非本地环境未配置真实认证，拒绝访问")
    if not settings.local_header_auth:
        raise AuthenticationError("本地 Header 认证未启用")
    if not _is_loopback(request):
        raise AuthenticationError("本地 Header 认证仅允许 loopback 来源")

    raw_user_id = request.headers.get("X-User-Id")
    if not raw_user_id:
        raise AuthenticationError("缺少 X-User-Id")
    try:
        user_id = UUID(raw_user_id)
    except ValueError as exc:
        raise AuthenticationError("X-User-Id 必须是 UUID") from exc

    # 平台角色不得来自请求头；本地开发一律按普通成员处理。
    return Principal(user_id=user_id, platform_role=PlatformRole.MEMBER)


DbSession = Annotated[Session, Depends(get_session)]
CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def get_project_access_service(
    session: DbSession,
) -> ProjectAccessService:
    return ProjectAccessService(
        projects=SqlProjectRepository(session),
        memberships=SqlProjectMembershipRepository(session),
    )


def get_project_context(
    project_id: Annotated[UUID, Path(description="项目 ID，服务端校验成员资格")],
    principal: CurrentPrincipal,
    access: Annotated[ProjectAccessService, Depends(get_project_access_service)],
) -> ProjectContext:
    return access.resolve_context(principal, project_id)


def _checked_scope(
    context: ProjectContext,
    action: ProjectAction,
) -> ProjectScope:
    if not context.can(action):
        raise ForbiddenError(f"当前角色无权执行操作：{action.value}")
    return ProjectScope.from_context(context)


def get_project_scope(
    context: Annotated[ProjectContext, Depends(get_project_context)],
) -> ProjectScope:
    """默认只读作用域：未显式声明 WRITE/DELETE 的路由按 READ 校验。"""
    return _checked_scope(context, ProjectAction.READ)


def get_write_scope(
    context: Annotated[ProjectContext, Depends(get_project_context)],
) -> ProjectScope:
    """写作用域：调用方必须显式依赖本函数，viewer 会被拒绝。"""
    return _checked_scope(context, ProjectAction.WRITE)


def get_delete_scope(
    context: Annotated[ProjectContext, Depends(get_project_context)],
) -> ProjectScope:
    """删除作用域：仅 owner/平台管理员可通过。"""
    return _checked_scope(context, ProjectAction.DELETE)


def get_scope(action: ProjectAction) -> Callable[..., ProjectScope]:
    """按动作生成项目作用域依赖，供路由显式声明所需权限。"""

    def dependency(
        context: Annotated[ProjectContext, Depends(get_project_context)],
    ) -> ProjectScope:
        return _checked_scope(context, action)

    return dependency


def require_project_permission(
    action: ProjectAction,
) -> Callable[..., ProjectContext]:
    """生成校验项目动作权限的 FastAPI 依赖。"""

    def dependency(
        context: Annotated[ProjectContext, Depends(get_project_context)],
    ) -> ProjectContext:
        if not context.can(action):
            raise ForbiddenError(f"当前角色无权执行操作：{action.value}")
        return context

    return dependency


def require_platform_admin(
    principal: CurrentPrincipal,
) -> Principal:
    if not principal.is_platform_admin:
        raise ForbiddenError("需要平台管理员权限")
    return principal
