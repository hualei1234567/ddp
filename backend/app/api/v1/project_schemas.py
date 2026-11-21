"""项目管理 API DTO（阶段 8）。

请求模型统一 ``extra="forbid"``：客户端不能提交 ``project_id`` / ``version`` /
``membership_role`` / ``allowed_actions`` 等服务端权威字段。

字段长度直接引用领域层常量（``PROJECT_*_MAX_LENGTH``）：契约与领域规则只允许
存在一处权威定义，Pydantic 只做"请求到达时尽早 422"的前置校验，领域层
``new_project`` / ``apply_patch`` 仍会再校验一次。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.projects.ports import UserProfile
from app.domain.identity.models import PlatformRole, Principal, ProjectRole
from app.domain.identity.projects import (
    PROJECT_CODE_MAX_LENGTH,
    PROJECT_DESCRIPTION_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    Project,
    ProjectMember,
    ProjectStatus,
    allowed_actions,
)

ProjectStatusLiteral = Literal["ACTIVE", "ARCHIVED"]
ProjectRoleLiteral = Literal["viewer", "editor", "owner"]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=PROJECT_CODE_MAX_LENGTH)
    name: str = Field(min_length=1, max_length=PROJECT_NAME_MAX_LENGTH)
    description: str | None = Field(
        default=None,
        max_length=PROJECT_DESCRIPTION_MAX_LENGTH,
    )


class ProjectPatch(BaseModel):
    """``code`` 不在其中：它是项目业务键，改名请用 ``name``。

    ``description`` 显式传 ``null`` 表示清空；``name`` / ``status`` 显式传 ``null``
    由领域层拒绝（422），不用"省略字段"和"置空"表达同一种意图。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROJECT_NAME_MAX_LENGTH,
    )
    description: str | None = Field(
        default=None,
        max_length=PROJECT_DESCRIPTION_MAX_LENGTH,
    )
    status: ProjectStatusLiteral | None = None


class ProjectOut(BaseModel):
    project_id: UUID
    code: str
    name: str
    description: str | None
    status: ProjectStatus
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    # 仅对当前认证主体有意义；平台管理员为 null（其权限不依赖成员关系）。
    membership_role: ProjectRole | None
    # 前端按钮渲染提示；服务端授权仍由依赖强制，前端不得当权限依据。
    allowed_actions: list[str]

    @classmethod
    def from_domain(
        cls,
        project: Project,
        *,
        role: ProjectRole | None,
        is_platform_admin: bool,
    ) -> ProjectOut:
        return cls(
            project_id=project.project_id,
            code=project.code,
            name=project.name,
            description=project.description,
            status=project.status,
            version=project.version,
            created_at=project.created_at,
            updated_at=project.updated_at,
            membership_role=role,
            allowed_actions=list(allowed_actions(role, is_platform_admin=is_platform_admin)),
        )


class ProjectMemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 本阶段没有"邀请"能力：用户目录由运维登记，客户端必须提供已存在的用户 ID。
    user_id: UUID
    role: ProjectRoleLiteral


class ProjectMemberRolePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ProjectRoleLiteral


class ProjectMemberOut(BaseModel):
    project_id: UUID
    user_id: UUID
    display_name: str
    role: ProjectRole
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, member: ProjectMember) -> ProjectMemberOut:
        # 显式逐字段取值：领域对象是 frozen dataclass，避免把整个对象塞进响应。
        return cls(
            project_id=member.project_id,
            user_id=member.user_id,
            display_name=member.display_name,
            role=member.role,
            created_at=member.created_at,
            updated_at=member.updated_at,
        )


class CurrentUserOut(BaseModel):
    """``GET /api/v1/me``：已认证主体自述。

    只有服务端认证结果能填充这些字段；请求头或查询参数里的 user_id 一律忽略。
    """

    user_id: UUID
    display_name: str
    platform_role: PlatformRole

    @classmethod
    def from_domain(cls, profile: UserProfile, principal: Principal) -> CurrentUserOut:
        return cls(
            user_id=profile.user_id,
            display_name=profile.display_name,
            platform_role=principal.platform_role,
        )


__all__ = [
    "CurrentUserOut",
    "ProjectCreate",
    "ProjectMemberAdd",
    "ProjectMemberOut",
    "ProjectMemberRolePatch",
    "ProjectOut",
    "ProjectPatch",
]
