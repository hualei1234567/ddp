"""应用层测试：项目上下文解析与默认拒绝。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.application.identity.access import ProjectAccessService
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.identity.models import PlatformRole, Principal, ProjectAction, ProjectRole

from tests.fakes import FakeMembershipRepository, FakeProjectRepository


def _service(
    project_ids: set,
    roles: dict | None = None,
) -> ProjectAccessService:
    return ProjectAccessService(
        projects=FakeProjectRepository(project_ids),
        memberships=FakeMembershipRepository(roles),
    )


def test_member_resolves_project_role() -> None:
    project_id = uuid4()
    user_id = uuid4()
    service = _service({project_id}, {(project_id, user_id): ProjectRole.EDITOR})

    context = service.resolve_context(Principal(user_id), project_id)

    assert context.project_id == project_id
    assert context.role is ProjectRole.EDITOR
    assert context.can(ProjectAction.WRITE)
    assert not context.can(ProjectAction.DELETE)


def test_non_member_is_forbidden_even_if_project_exists() -> None:
    project_id = uuid4()
    service = _service({project_id})

    with pytest.raises(ForbiddenError):
        service.resolve_context(Principal(uuid4()), project_id)


def test_non_member_gets_same_error_for_missing_project() -> None:
    """不区分项目不存在与无成员资格，避免项目枚举。"""
    service = _service(set())

    with pytest.raises(ForbiddenError):
        service.resolve_context(Principal(uuid4()), uuid4())


def test_platform_admin_resolves_existing_project_without_membership() -> None:
    project_id = uuid4()
    service = _service({project_id})
    principal = Principal(uuid4(), PlatformRole.ADMIN)

    context = service.resolve_context(principal, project_id)

    assert context.role is None
    assert context.is_platform_admin
    assert context.can(ProjectAction.MANAGE_MEMBERS)


def test_platform_admin_gets_not_found_for_missing_project() -> None:
    principal = Principal(uuid4(), PlatformRole.ADMIN)
    service = _service(set())

    with pytest.raises(NotFoundError):
        service.resolve_context(principal, uuid4())
