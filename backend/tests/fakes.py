"""测试用仓储替身（显式标注：非真实数据库）。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from uuid import UUID

from app.domain.identity.models import ProjectRole


class FakeProjectRepository:
    def __init__(self, project_ids: Iterable[UUID] = ()) -> None:
        self._project_ids = set(project_ids)

    def exists(self, project_id: UUID) -> bool:
        return project_id in self._project_ids


class FakeMembershipRepository:
    def __init__(
        self,
        roles: Mapping[tuple[UUID, UUID], ProjectRole] | None = None,
    ) -> None:
        self._roles = dict(roles or {})

    def role_for(self, project_id: UUID, user_id: UUID) -> ProjectRole | None:
        return self._roles.get((project_id, user_id))
