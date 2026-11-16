"""项目管理域的仓储端口。

所有成员读写都要求传入服务端构造的 :class:`ProjectScope`，仓储强制按
``project_id`` 过滤；``list_visible`` 是唯一"跨项目"的查询，它的作用域由
认证主体决定（普通用户 → 自己的成员关系；平台管理员 → 全部项目），
**不接受客户端传入的用户或项目白名单**。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.audit import (
    AuditRepository as _AuditRepository,
)
from app.application.audit import (
    OutboxRepository as _OutboxRepository,
)
from app.domain.identity.models import ProjectRole, ProjectScope
from app.domain.identity.projects import Project, ProjectMember, ProjectStatus


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectListing:
    """列表行：项目 + 当前用户在该项目中的角色（管理员为 ``None``）。"""

    project: Project
    role: ProjectRole | None


class ProjectRepository(Protocol):
    def get(self, project_id: UUID) -> Project | None: ...

    def get_by_code(self, code: str) -> Project | None: ...

    def add(self, project: Project) -> Project: ...

    def save(self, project: Project, *, expected_version: int) -> bool:
        """按乐观锁写回；命中 0 行返回 ``False``（不存在或版本已变）。"""
        ...

    def list_visible(
        self,
        *,
        user_id: UUID | None,
        status: ProjectStatus,
        after_code: str | None,
        limit: int,
    ) -> list[ProjectListing]:
        """``user_id=None`` 表示平台管理员视角（全部项目）。"""
        ...

    def lock_for_member_change(self, project_id: UUID) -> None:
        """锁住项目行，串行化项目内的成员变更（见用例层的并发说明）。"""
        ...


class ProjectMemberRepository(Protocol):
    def list(
        self,
        scope: ProjectScope,
        *,
        after_user_id: UUID | None,
        limit: int,
    ) -> list[ProjectMember]: ...

    def get(self, scope: ProjectScope, user_id: UUID) -> ProjectMember | None: ...

    def add(
        self,
        scope: ProjectScope,
        *,
        user_id: UUID,
        role: ProjectRole,
        now: datetime,
    ) -> ProjectMember: ...

    def update_role(
        self,
        scope: ProjectScope,
        user_id: UUID,
        *,
        role: ProjectRole,
        now: datetime,
    ) -> bool: ...

    def remove(self, scope: ProjectScope, user_id: UUID) -> bool: ...

    def count_owners(self, scope: ProjectScope) -> int: ...


class UserDirectory(Protocol):
    """用户目录存在性查询；本阶段不提供用户创建/邀请能力。"""

    def exists(self, user_id: UUID) -> bool: ...


AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository

__all__ = [
    "AuditRepository",
    "OutboxRepository",
    "ProjectListing",
    "ProjectMemberRepository",
    "ProjectRepository",
    "UnitOfWork",
    "UserDirectory",
]
