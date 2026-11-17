"""领域单测：项目实体、状态/角色解析与"最后一名 owner"规则。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.identity.models import ProjectRole
from app.domain.identity.projects import (
    PROJECT_CODE_MAX_LENGTH,
    PROJECT_DESCRIPTION_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    Project,
    ProjectStatus,
    allowed_actions,
    leaves_project_without_owner,
    new_project,
    normalize_project_code,
    normalize_project_description,
    normalize_project_name,
    parse_project_role,
    parse_project_status,
)

NOW = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def _new(**overrides: object) -> Project:
    values: dict[str, object] = {
        "project_id": uuid4(),
        "code": "prj-1",
        "name": "项目一",
        "description": None,
    }
    values.update(overrides)
    return new_project(now=NOW, **values)  # type: ignore[arg-type]


def test_new_project_defaults_to_active_with_version_one() -> None:
    project = _new(description="  说明  ")
    assert project.status is ProjectStatus.ACTIVE
    assert project.version == 1
    assert project.description == "说明"
    assert project.created_at == NOW
    assert project.updated_at == NOW


def test_code_and_name_are_trimmed() -> None:
    assert normalize_project_code("  prj-1  ") == "prj-1"
    assert normalize_project_name("  项目  ") == "项目"


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_code_or_name_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_project_code(value)
    with pytest.raises(ValueError):
        normalize_project_name(value)


def test_length_limits() -> None:
    assert normalize_project_code("x" * PROJECT_CODE_MAX_LENGTH) == "x" * PROJECT_CODE_MAX_LENGTH
    with pytest.raises(ValueError):
        normalize_project_code("x" * (PROJECT_CODE_MAX_LENGTH + 1))
    assert normalize_project_name("x" * PROJECT_NAME_MAX_LENGTH)
    with pytest.raises(ValueError):
        normalize_project_name("x" * (PROJECT_NAME_MAX_LENGTH + 1))
    assert normalize_project_description("x" * PROJECT_DESCRIPTION_MAX_LENGTH)
    with pytest.raises(ValueError):
        normalize_project_description("x" * (PROJECT_DESCRIPTION_MAX_LENGTH + 1))


def test_blank_description_normalizes_to_none() -> None:
    assert normalize_project_description("   ") is None
    assert normalize_project_description(None) is None


def test_version_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Project(
            project_id=uuid4(),
            code="p",
            name="n",
            description=None,
            status=ProjectStatus.ACTIVE,
            version=0,
        )


def test_apply_patch_updates_fields_and_bumps_version() -> None:
    project = _new()
    patched = project.apply_patch(
        {"name": "  新名字 ", "description": None, "status": "ARCHIVED"},
        now=NOW,
    )
    assert patched.name == "新名字"
    assert patched.description is None
    assert patched.status is ProjectStatus.ARCHIVED
    assert patched.version == 2
    # 原实体不可变。
    assert project.name == "项目一"
    assert project.version == 1


def test_apply_patch_rejects_empty_unknown_and_null_name() -> None:
    project = _new()
    with pytest.raises(ValueError):
        project.apply_patch({}, now=NOW)
    with pytest.raises(ValueError):
        project.apply_patch({"code": "other"}, now=NOW)
    with pytest.raises(ValueError):
        project.apply_patch({"name": None}, now=NOW)
    with pytest.raises(ValueError):
        project.apply_patch({"status": None}, now=NOW)


def test_apply_patch_rejects_unknown_status_value() -> None:
    with pytest.raises(ValueError):
        _new().apply_patch({"status": "DELETED"}, now=NOW)


@pytest.mark.parametrize(
    ("current_role", "new_role", "owner_count", "expected"),
    [
        (ProjectRole.OWNER, ProjectRole.EDITOR, 1, True),
        (ProjectRole.OWNER, ProjectRole.EDITOR, 2, False),
        (ProjectRole.OWNER, None, 1, True),
        (ProjectRole.OWNER, None, 2, False),
        (ProjectRole.OWNER, ProjectRole.OWNER, 1, False),
        (ProjectRole.EDITOR, None, 1, False),
        (ProjectRole.EDITOR, ProjectRole.VIEWER, 1, False),
        (ProjectRole.VIEWER, ProjectRole.OWNER, 1, False),
    ],
)
def test_leaves_project_without_owner(
    current_role: ProjectRole,
    new_role: ProjectRole | None,
    owner_count: int,
    expected: bool,
) -> None:
    assert (
        leaves_project_without_owner(
            current_role=current_role,
            new_role=new_role,
            owner_count=owner_count,
        )
        is expected
    )


def test_owner_count_must_be_positive() -> None:
    with pytest.raises(ValueError):
        leaves_project_without_owner(
            current_role=ProjectRole.OWNER,
            new_role=None,
            owner_count=0,
        )


def test_allowed_actions_follow_role_matrix() -> None:
    assert allowed_actions(None, is_platform_admin=False) == ()
    assert allowed_actions(ProjectRole.VIEWER, is_platform_admin=False) == ("read",)
    assert allowed_actions(ProjectRole.EDITOR, is_platform_admin=False) == (
        "read",
        "write",
        "validate",
    )
    assert allowed_actions(ProjectRole.OWNER, is_platform_admin=False) == (
        "read",
        "write",
        "delete",
        "validate",
        "manage_members",
    )
    assert allowed_actions(ProjectRole.VIEWER, is_platform_admin=True) == (
        "read",
        "write",
        "delete",
        "validate",
        "manage_members",
    )


def test_parse_helpers_reject_unknown_values() -> None:
    assert parse_project_status("ARCHIVED") is ProjectStatus.ARCHIVED
    assert parse_project_role("editor") is ProjectRole.EDITOR
    with pytest.raises(ValueError):
        parse_project_status("DELETED")
    with pytest.raises(ValueError):
        parse_project_role("admin")
