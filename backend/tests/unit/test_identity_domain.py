"""领域测试：权限矩阵与项目作用域。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import (
    PlatformRole,
    Principal,
    ProjectAction,
    ProjectContext,
    ProjectRole,
    ProjectScope,
)


@pytest.mark.parametrize(
    ("role", "action", "expected"),
    [
        (ProjectRole.VIEWER, ProjectAction.READ, True),
        (ProjectRole.VIEWER, ProjectAction.WRITE, False),
        (ProjectRole.VIEWER, ProjectAction.DELETE, False),
        (ProjectRole.VIEWER, ProjectAction.MANAGE_MEMBERS, False),
        (ProjectRole.EDITOR, ProjectAction.READ, True),
        (ProjectRole.EDITOR, ProjectAction.WRITE, True),
        (ProjectRole.EDITOR, ProjectAction.DELETE, False),
        (ProjectRole.EDITOR, ProjectAction.MANAGE_MEMBERS, False),
        (ProjectRole.OWNER, ProjectAction.READ, True),
        (ProjectRole.OWNER, ProjectAction.WRITE, True),
        (ProjectRole.OWNER, ProjectAction.DELETE, True),
        (ProjectRole.OWNER, ProjectAction.MANAGE_MEMBERS, True),
    ],
)
def test_role_permission_matrix(
    role: ProjectRole,
    action: ProjectAction,
    expected: bool,
) -> None:
    context = ProjectContext(
        project_id=uuid4(),
        user_id=uuid4(),
        role=role,
    )
    assert context.can(action) is expected


def test_context_without_role_denies_everything() -> None:
    context = ProjectContext(project_id=uuid4(), user_id=uuid4(), role=None)
    assert all(not context.can(action) for action in ProjectAction)


def test_platform_admin_can_all_actions() -> None:
    context = ProjectContext(
        project_id=uuid4(),
        user_id=uuid4(),
        platform_role=PlatformRole.ADMIN,
        role=None,
    )
    assert all(context.can(action) for action in ProjectAction)


def test_principal_admin_flag() -> None:
    assert Principal(uuid4(), PlatformRole.ADMIN).is_platform_admin
    assert not Principal(uuid4(), PlatformRole.MEMBER).is_platform_admin


def test_project_scope_accepts_same_project() -> None:
    project_id = uuid4()
    scope = ProjectScope(project_id=project_id)
    scope.ensure(project_id)


def test_project_scope_rejects_cross_project_resource() -> None:
    scope = ProjectScope(project_id=uuid4())
    with pytest.raises(CrossProjectAccessError):
        scope.ensure(uuid4())


def test_scope_from_context_uses_server_project_id() -> None:
    project_id = uuid4()
    context = ProjectContext(project_id=project_id, user_id=uuid4(), role=ProjectRole.OWNER)
    assert ProjectScope.from_context(context).project_id == project_id
