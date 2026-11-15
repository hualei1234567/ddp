"""接口测试：身份依赖、项目上下文与权限/跨项目隔离。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

import pytest
from app.api.deps import (
    get_current_principal,
    get_delete_scope,
    get_project_access_service,
    get_project_scope,
    get_scope,
    get_write_scope,
    require_project_permission,
)
from app.application.identity.access import ProjectAccessService
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import (
    PlatformRole,
    Principal,
    ProjectAction,
    ProjectContext,
    ProjectRole,
    ProjectScope,
)
from app.main import create_app
from fastapi import Depends, Request
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

    @app.get("/test/projects/{project_id}/read-scope")
    def read_scope(
        scope: Annotated[ProjectScope, Depends(get_project_scope)],
    ) -> dict[str, str]:
        return {
            "project_id": str(scope.project_id),
            "actor_id": str(scope.actor_id),
        }

    @app.post("/test/projects/{project_id}/write-scope")
    def write_scope(
        scope: Annotated[ProjectScope, Depends(get_write_scope)],
    ) -> dict[str, str]:
        return {
            "project_id": str(scope.project_id),
            "actor_id": str(scope.actor_id),
        }

    @app.delete("/test/projects/{project_id}/resource")
    def delete_resource(
        scope: Annotated[
            ProjectScope,
            Depends(get_scope(ProjectAction.DELETE)),
        ],
    ) -> dict[str, str]:
        return {
            "project_id": str(scope.project_id),
            "actor_id": str(scope.actor_id),
        }

    @app.delete("/test/projects/{project_id}/delete-scope")
    def delete_scope(
        scope: Annotated[ProjectScope, Depends(get_delete_scope)],
    ) -> dict[str, str]:
        return {
            "project_id": str(scope.project_id),
            "actor_id": str(scope.actor_id),
        }

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


def _local_with_header_auth() -> Settings:
    return Settings(env="local", local_header_auth=True)


def test_local_header_identity_is_accepted_when_explicitly_enabled() -> None:
    client = _build_client(settings=_local_with_header_auth())
    user_id = uuid4()

    response = client.get("/test/me", headers={"X-User-Id": str(user_id)})

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id), "platform_role": "member"}


def test_local_header_auth_is_disabled_by_default() -> None:
    client = _build_client(settings=Settings(env="local"))

    response = client.get("/test/me", headers={"X-User-Id": str(uuid4())})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_platform_role_header_is_ignored() -> None:
    """平台角色不得来自请求头，伪造 admin 仍只能是普通成员。"""
    client = _build_client(settings=_local_with_header_auth())

    response = client.get(
        "/test/me",
        headers={
            "X-User-Id": str(uuid4()),
            "X-Platform-Role": PlatformRole.ADMIN.value,
        },
    )

    assert response.status_code == 200
    assert response.json()["platform_role"] == PlatformRole.MEMBER.value


def test_forged_admin_header_cannot_escalate_project_permission() -> None:
    project_id = uuid4()
    client = _build_client(
        service=_service({project_id}),
        settings=_local_with_header_auth(),
    )

    response = client.delete(
        f"/test/projects/{project_id}/members",
        headers={
            "X-User-Id": str(uuid4()),
            "X-Platform-Role": PlatformRole.ADMIN.value,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_local_header_auth_rejects_non_loopback_client() -> None:
    settings = _local_with_header_auth()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"x-user-id", str(uuid4()).encode())],
        "client": ("10.0.0.9", 54321),
    }

    with pytest.raises(AuthenticationError, match="loopback"):
        get_current_principal(Request(scope), settings)


def test_missing_identity_header_is_rejected() -> None:
    client = _build_client(settings=_local_with_header_auth())

    response = client.get("/test/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_invalid_user_id_is_rejected() -> None:
    client = _build_client(settings=_local_with_header_auth())

    response = client.get("/test/me", headers={"X-User-Id": "not-a-uuid"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_non_local_environment_rejects_header_identity() -> None:
    client = _build_client(
        settings=Settings(env="production", local_header_auth=True),
    )

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


def test_default_project_scope_requires_read_permission() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.VIEWER}),
    )

    response = client.get(f"/test/projects/{project_id}/read-scope")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": str(project_id),
        "actor_id": str(user_id),
    }


def test_non_member_cannot_get_read_scope() -> None:
    project_id = uuid4()
    client = _build_client(
        principal=Principal(uuid4()),
        service=_service({project_id}),
    )

    response = client.get(f"/test/projects/{project_id}/read-scope")

    assert response.status_code == 403


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


def test_viewer_write_scope_is_forbidden() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.VIEWER}),
    )

    response = client.post(f"/test/projects/{project_id}/write-scope")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_viewer_delete_scope_is_forbidden() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.VIEWER}),
    )

    response = client.delete(f"/test/projects/{project_id}/delete-scope")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_viewer_cannot_delete_resource() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.VIEWER}),
    )

    response = client.delete(f"/test/projects/{project_id}/resource")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_editor_can_write_scope_but_cannot_delete() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.EDITOR}),
    )

    write_response = client.post(f"/test/projects/{project_id}/write-scope")
    delete_response = client.delete(f"/test/projects/{project_id}/resource")

    assert write_response.status_code == 200
    assert write_response.json()["actor_id"] == str(user_id)
    assert delete_response.status_code == 403


def test_owner_can_delete_scope() -> None:
    project_id = uuid4()
    user_id = uuid4()
    client = _build_client(
        principal=Principal(user_id),
        service=_service({project_id}, {(project_id, user_id): ProjectRole.OWNER}),
    )

    response = client.delete(f"/test/projects/{project_id}/resource")

    assert response.status_code == 200
    assert response.json()["actor_id"] == str(user_id)


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


def test_platform_admin_principal_can_manage_existing_project() -> None:
    project_id = uuid4()
    client = _build_client(
        principal=Principal(uuid4(), PlatformRole.ADMIN),
        service=_service({project_id}),
    )

    response = client.delete(f"/test/projects/{project_id}/members")

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


def test_cross_project_domain_error_maps_to_project_scope_denied() -> None:
    """跨项目访问必须用专用码，不能与普通权限拒绝混为一谈。

    ``project_scope_denied`` 是**安全信号**：有人在访问不属于当前项目的资源，
    通常意味着 ID 枚举或客户端 bug，应当单独告警与计数。若映射成泛化的
    ``forbidden``，真正的越权探测会淹没在日常的权限拒绝里。

    该码在契约（`packages/contracts/schemas/error-codes.v1.json` 的
    `reused_codes`）与阶段 4 错误码注册表里早就定义，此前实现从未产生它。
    """
    client = _build_client(
        principal=Principal(uuid4()),
        service=_service(set()),
    )

    response = client.get("/test/cross-project")

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "project_scope_denied"
    # 文案不得回显被访问的资源标识（存在性信息本身就不该泄漏）。
    assert "模拟跨项目资源访问" not in error["message"]
    assert error["request_id"]
