"""项目与成员仓储的 SQLAlchemy 实现。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectRole
from app.infrastructure.db.models.identity import Project, ProjectMember


class SqlProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, project_id: UUID) -> bool:
        return self._session.get(Project, project_id) is not None


class SqlProjectMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def role_for(self, project_id: UUID, user_id: UUID) -> ProjectRole | None:
        raw_role = self._session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        if raw_role is None:
            return None
        try:
            return ProjectRole(raw_role)
        except ValueError as exc:
            # 数据库出现未知角色时显式失败，绝不默认放行。
            raise ValueError(f"数据库中项目角色非法：{raw_role!r}") from exc
