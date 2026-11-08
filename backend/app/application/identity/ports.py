"""项目访问所需的仓储端口（由 infrastructure 实现）。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectRole


class ProjectRepository(Protocol):
    def exists(self, project_id: UUID) -> bool:
        """仅判断项目是否存在，不返回可用于越权的详细信息。"""


class ProjectMembershipRepository(Protocol):
    def role_for(self, project_id: UUID, user_id: UUID) -> ProjectRole | None:
        """查询用户在指定项目中的角色；无成员资格返回 ``None``。"""
