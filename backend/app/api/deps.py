"""API 依赖：身份、数据库会话、项目上下文与权限。

路由必须通过 :func:`require_project_permission` 或
:class:`app.domain.identity.models.ProjectContext` 做服务端授权，
不得信任客户端传入的 ``project_id``。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Query, Request
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
from app.infrastructure.auth.jwt import TokenValidationError, decode_hs256_token
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.identity import (
    SqlProjectMembershipRepository,
    SqlProjectRepository,
)

# 允许使用本地 Header 认证的来源地址。
# ``testclient`` 只可能来自 Starlette 进程内 ASGI 测试客户端，不会出现在真实网络中。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

# 对外统一措辞，避免通过错误消息区分过期、签名错误等细节。
_INVALID_TOKEN_MESSAGE = "无效或已过期的访问令牌"  # noqa: S105 - 错误提示，非凭据


def _is_loopback(request: Request) -> bool:
    """仅信任 ASGI Server 提供的对端地址，不读取任何转发头。"""
    client = request.client
    return client is not None and client.host in _LOOPBACK_HOSTS


def extract_bearer_token(request: Request) -> str | None:
    """提取 Bearer token；存在 Authorization 但格式错误时直接拒绝。

    一旦客户端声明了 ``Authorization``，就不得再回退到 X-User-Id，
    避免本地 Header 后门被用来掩盖错误的令牌。

    公开为模块级函数是为了让 ``/metrics`` 的静态令牌校验复用**同一套**头部
    解析语义（大小写、分隔符、空白），而不是在运维端点上再写一份。
    """
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None
    scheme, separator, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not credentials.strip():
        raise AuthenticationError("Authorization 头格式错误，应为 Bearer <token>")
    return credentials.strip()


def _principal_from_token(token: str, settings: Settings) -> Principal:
    try:
        claims = decode_hs256_token(
            token,
            secret=settings.jwt_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway_seconds=settings.jwt_leeway_seconds,
        )
        user_id = UUID(claims.subject)
        platform_role = PlatformRole(claims.platform_role)
    except (TokenValidationError, ValueError) as exc:
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE) from exc
    return Principal(user_id=user_id, platform_role=platform_role)


def get_current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """认证适配器：优先 Bearer JWT；本地 Header 仅在测试/loopback 下作为后门。

    安全约束：
    - 任何环境都接受完整的 HS256 Bearer JWT；
    - 本地 Header 认证默认关闭，只允许 ``local`` 环境、显式开关与 loopback 三者同时成立；
    - 平台角色只能来自经过签名的 ``platform_role`` 声明，请求头不能提升权限；
    - 非本地环境缺失令牌一律 401，不存在隐式放行。
    """
    token = extract_bearer_token(request)
    if token is not None:
        return _principal_from_token(token, settings)

    if not settings.is_local:
        raise AuthenticationError("缺少访问令牌")
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


def get_query_project_context(
    project_id: Annotated[UUID, Query(description="项目 ID，服务端校验成员资格")],
    principal: CurrentPrincipal,
    access: Annotated[ProjectAccessService, Depends(get_project_access_service)],
) -> ProjectContext:
    """无项目路径前缀的资源（血缘/下载）使用查询参数声明项目。

    客户端提交的 project_id 只是定位上下文，服务端仍按成员资格与资源项目
    归属双重校验，不信任其用于跨项目读取。
    """
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


def get_query_read_scope(
    context: Annotated[ProjectContext, Depends(get_query_project_context)],
) -> ProjectScope:
    """查询参数定位项目的只读作用域。"""
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
