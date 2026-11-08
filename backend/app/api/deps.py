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


def get_current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """阶段 0 身份适配器：仅本地环境允许通过请求头模拟用户。

    生产环境默认拒绝；接入 JWT/OIDC 时应替换本函数实现，
    路由与服务层无需感知具体认证方式。
    """
    if not settings.is_local:
        raise AuthenticationError("未配置认证方式，拒绝访问")

    raw_user_id = request.headers.get("X-User-Id")
    if not raw_user_id:
        raise AuthenticationError("缺少 X-User-Id")
    try:
        user_id = UUID(raw_user_id)
    except ValueError as exc:
        raise AuthenticationError("X-User-Id 必须是 UUID") from exc

    raw_platform_role = request.headers.get(
        "X-Platform-Role",
        PlatformRole.MEMBER.value,
    )
    try:
        platform_role = PlatformRole(raw_platform_role)
    except ValueError as exc:
        raise AuthenticationError("X-Platform-Role 非法") from exc

    return Principal(user_id=user_id, platform_role=platform_role)


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


def get_project_scope(
    context: Annotated[ProjectContext, Depends(get_project_context)],
) -> ProjectScope:
    """把已校验上下文转换成仓储层必须携带的 ProjectScope。"""
    return ProjectScope.from_context(context)


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
