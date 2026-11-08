"""项目上下文解析用例。

所有访问项目数据的入口都必须先经过本服务；它负责：
1. 平台管理员仍需显式指定项目，且项目必须存在；
2. 普通用户必须存在项目成员资格；
3. 对无成员资格者不区分“项目不存在”，避免项目枚举。
"""

from __future__ import annotations

from uuid import UUID

from app.application.identity.ports import ProjectMembershipRepository, ProjectRepository
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.identity.models import Principal, ProjectContext


class ProjectAccessService:
    def __init__(
        self,
        projects: ProjectRepository,
        memberships: ProjectMembershipRepository,
    ) -> None:
        self._projects = projects
        self._memberships = memberships

    def resolve_context(self, principal: Principal, project_id: UUID) -> ProjectContext:
        if principal.is_platform_admin:
            if not self._projects.exists(project_id):
                raise NotFoundError("项目不存在")
            return ProjectContext(
                project_id=project_id,
                user_id=principal.user_id,
                platform_role=principal.platform_role,
                role=None,
            )

        role = self._memberships.role_for(project_id, principal.user_id)
        if role is None:
            raise ForbiddenError("无权访问该项目")
        return ProjectContext(
            project_id=project_id,
            user_id=principal.user_id,
            platform_role=principal.platform_role,
            role=role,
        )
