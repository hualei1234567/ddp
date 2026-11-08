"""接口测试：身份依赖、项目上下文与权限/跨项目隔离。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from app.api.deps import (
    get_current_principal,
    get_project_access_service,
    require_project_permission,
)
from app.application.identity.access import ProjectAccessService
from app.core.config import Settings, get_settings
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import (
    PlatformRole,
    Principal,
    ProjectAction,
    ProjectContext,
    ProjectRole,
)
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient

from tests.fakes import FakeMembershipRepository, FakeProjectRepository


def _build_client(
    *,
    principal: Principal | None = None,
    service: ProjectAccessService | None = None,
    settings: Settings | None = None,
) -> TestClient:
    app = create_app()

    @app.get("/test/me")
    def me(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> dict[str, str]:
        return {
            "user_id": str(principal.user_id),
            "platform_role": principal.platform_role.value,
        }

    @app.get("/test/projects/{project_id}/read")
    def read(
        context: Annotated[
            ProjectContext,
            Depends(require_project_permission(ProjectAction.READ)),
        ],
    ) -> dict[str, str | None]:
        return {
            "project_id": str(context.project_id),
            "role": context.role.value if context.role else None,
        }

    @app.post("/test/projects/{project_id}/write")
    def write(
        context: Annotated[
            ProjectContext,
            Depends(require_project_permission(ProjectAction.WRITE)),
        ],
    ) -> dict[str, str | None]:
        return {
            "project_id": str(context.project_id),
            "role": context.role.value if context.role else None,
        }

    @app.delete("/test/projects/{project_id}/members")
    def manage_members(
        context: Annotated[
            ProjectContext,
            Depends(require_project_permission(ProjectAction.MANAGE_MEMBERS)),
        ],
    ) -> dict[str, str | None]:
        return {"project_id": str(context.project_id)}

    @app.get("/test/cross-project")
    def cross_project() -> dict[str, str]:
        raise CrossProjectAccessError("模拟跨项目资源访问")

    if principal is not None:
        app.dependency_overrides[get_current_principal] = lambda: principal
    if service is not None:
        app.dependency_overrides[get_project_access_service] = lambda: service
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings

    return TestClient(app)


def _service(
    project_ids: set[UUID],
    roles: dict[tuple[UUID, UUID], ProjectRole] | None = None,
) -> ProjectAccessService:
    return ProjectAccessService(
        projects=FakeProjectRepository(project_ids),
        memberships=FakeMembershipRepository(roles),
    )


def test_local_header_identity_is_accepted() -> None:
    client = _build_client(settings=Settings(env="local"))
    user_id = uuid4()

    response = client.get("/test/me", headers={"X-User-Id": str(user_id)})

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id), "platform_role": "member"}


def test_missing_identity_header_is_rejected() -> None:
    client = _build_client(settings=Settings(env="local"))

    response = client.get("/test/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_invalid_user_id_is_rejected() -> None:
    client = _build_client(settings=Settings(env="local"))

    response = client.get("/test/me", headers={"X-User-Id": "not-a-uuid"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_invalid_platform_role_is_rejected() -> None:
    client = _build_client(settings=Settings(env="local"))

    response = client.get(
        "/test/me",
        headers={"X-User-Id": str(uuid4()), "X-Platform-Role": "root"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_non_local_environment_rejects_header_identity() -> None:
    client = _build_client(settings=Settings(env="production"))

    response = client.get("/test/me", headers={"X-User-Id": str(uuid4())})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_viewer_can_read_project() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.VIEWER}),
    )

    response = client.get(f"/test/projects/{project_id}/read")

    assert response.status_code == 200
    assert response.json()["project_id"] == str(project_id)


def test_viewer_cannot_write_project() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.VIEWER}),
    )

    response = client.post(f"/test/projects/{project_id}/write")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_editor_can_write_project() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.EDITOR}),
    )

    response = client.post(f"/test/projects/{project_id}/write")

    assert response.status_code == 200


def test_non_member_cannot_read_project() -> None:
    project_id = uuid4()
    client = _build_client(
        principal=Principal(uuid4()),
        service=_service({project_id}),
    )

    response = client.get(f"/test/projects/{project_id}/read")

    assert response.status_code == 403


def test_member_of_other_project_cannot_read_by_idor() -> None:
    """用户只属于项目 A，改用项目 B 的 ID 访问必须被拒绝。"""
    project_a = uuid4()
    project_b = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service(
            {project_a, project_b},
            {(project_a, user_id): ProjectRole.OWNER},
        ),
    )

    response = client.get(f"/test/projects/{project_b}/read")

    assert response.status_code == 403


def test_platform_admin_header_can_manage_existing_project() -> None:
    project_id = uuid4()
    client = _build_client(
        service=_service({project_id}),
        settings=Settings(env="local"),
    )

    response = client.delete(
        f"/test/projects/{project_id}/members",
        headers={
            "X-User-Id": str(uuid4()),
            "X-Platform-Role": PlatformRole.ADMIN.value,
        },
    )

    assert response.status_code == 200


def test_platform_admin_gets_not_found_for_missing_project() -> None:
    client = _build_client(
        principal=Principal(uuid4(), PlatformRole.ADMIN),
        service=_service(set()),
    )

    response = client.get(f"/test/projects/{uuid4()}/read")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_invalid_project_id_is_rejected() -> None:
    client = _build_client(
        principal=Principal(uuid4()),
        service=_service(set()),
    )

    response = client.get("/test/projects/not-a-uuid/read")

    assert response.status_code == 422


def test_cross_project_domain_error_maps_to_forbidden() -> None:
    client = _build_client(
        principal=Principal(uuid4()),
        service=_service(set()),
    )

    response = client.get("/test/cross-project")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
