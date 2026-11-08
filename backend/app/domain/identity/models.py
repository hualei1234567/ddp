"""身份与项目上下文领域模型。

本模块只包含纯 Python 规则，不依赖 FastAPI、SQLAlchemy 或数据库。
权限模型遵循 ADR-0004：平台角色 + 项目角色 + 显式动作，默认拒绝。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from app.domain.identity.errors import CrossProjectAccessError


class PlatformRole(StrEnum):
    """平台级角色。"""

    MEMBER = "member"
    ADMIN = "admin"


class ProjectRole(StrEnum):
    """项目内角色。"""

    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"


class ProjectAction(StrEnum):
    """需要在项目上下文内授权的动作。"""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    VALIDATE = "validate"
    MANAGE_MEMBERS = "manage_members"


# 显式权限矩阵：未列出的动作一律拒绝，禁止“未知动作默认放行”。
# VALIDATE 表示可以担任标定记录的校验人；VIEWER 只读，不得校验。
ROLE_PERMISSIONS: Mapping[ProjectRole, frozenset[ProjectAction]] = MappingProxyType(
    {
        ProjectRole.VIEWER: frozenset({ProjectAction.READ}),
        ProjectRole.EDITOR: frozenset(
            {
                ProjectAction.READ,
                ProjectAction.WRITE,
                ProjectAction.VALIDATE,
            }
        ),
        ProjectRole.OWNER: frozenset(
            {
                ProjectAction.READ,
                ProjectAction.WRITE,
                ProjectAction.DELETE,
                ProjectAction.VALIDATE,
                ProjectAction.MANAGE_MEMBERS,
            }
        ),
    }
)


def role_allows(role: ProjectRole | None, action: ProjectAction) -> bool:
    """判断项目角色是否拥有动作权限；无角色一律拒绝。"""
    if role is None:
        return False
    return action in ROLE_PERMISSIONS[role]


@dataclass(frozen=True, slots=True)
class Principal:
    """已认证主体（阶段 0 由本地身份适配器构造）。"""

    user_id: UUID
    platform_role: PlatformRole = PlatformRole.MEMBER

    @property
    def is_platform_admin(self) -> bool:
        return self.platform_role is PlatformRole.ADMIN


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """服务端校验后的项目上下文。

    ``role`` 为 ``None`` 仅表示平台管理员在显式访问某个已存在项目；
    普通成员没有项目角色时不应构造该对象。
    """

    project_id: UUID
    user_id: UUID
    platform_role: PlatformRole = PlatformRole.MEMBER
    role: ProjectRole | None = None

    @property
    def is_platform_admin(self) -> bool:
        return self.platform_role is PlatformRole.ADMIN

    @property
    def actor_id(self) -> UUID:
        """当前主体 ID；所有写入审计字段都必须从这里派生。"""
        return self.user_id

    def can(self, action: ProjectAction) -> bool:
        if self.is_platform_admin:
            return True
        return role_allows(self.role, action)


@dataclass(frozen=True, slots=True)
class ProjectScope:
    """用于 Repository / 用例的服务端项目作用域。

    所有按 ID 读写资源的地方都必须先调用 :meth:`ensure`，
    禁止依赖客户端声明的 ``project_id``。

    ``actor_id`` 来自服务端认证后的 :class:`ProjectContext`，
    仓储必须据此写入 ``created_by`` / ``updated_by``，不接受调用方另行传入。
    """

    project_id: UUID
    actor_id: UUID

    @classmethod
    def from_context(cls, context: ProjectContext) -> ProjectScope:
        return cls(project_id=context.project_id, actor_id=context.actor_id)

    def ensure(self, project_id: UUID) -> None:
        if project_id != self.project_id:
            raise CrossProjectAccessError(
                f"资源不属于当前项目：expected={self.project_id}, actual={project_id}"
            )
