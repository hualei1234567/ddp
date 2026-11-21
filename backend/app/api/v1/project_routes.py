"""项目与项目成员 HTTP 路由（阶段 8 项目管理 / 人员与团队）。

路由只做协议适配（路径、请求体、``If-Match``、状态码），业务规则在
``app.application.projects.use_cases``，权限判定复用 ``app.api.deps`` 中
既有的 ``require_project_permission`` / ``require_platform_admin``。

**没有 ``DELETE /projects/{project_id}``**：``projects.id`` 被全部业务表以
``ON DELETE CASCADE`` 引用，硬删除会连带销毁项目全部数据。下线用
``PATCH {"status": "ARCHIVED"}``；理由见用例模块文档。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import CurrentPrincipal, require_platform_admin, require_project_permission
from app.api.v1.preconditions import require_expected_version
from app.api.v1.project_dependencies import ProjectServicesDep
from app.api.v1.project_schemas import (
    CurrentUserOut,
    ProjectCreate,
    ProjectMemberAdd,
    ProjectMemberOut,
    ProjectMemberRolePatch,
    ProjectOut,
    ProjectPatch,
)
from app.api.v1.schemas import PageOut
from app.application.projects import NewProject
from app.domain.identity.models import (
    Principal,
    ProjectAction,
    ProjectContext,
    ProjectScope,
)

router = APIRouter(prefix="/projects", tags=["projects"])
# ``/me`` 与项目无关（它回答"我是谁"），但复用同一组依赖与用户目录仓储，
# 因此挂在本模块而不是新开一个只有一条路由的文件。
me_router = APIRouter(tags=["platform"])


@me_router.get("/me", response_model=CurrentUserOut)
def get_current_user(
    principal: CurrentPrincipal,
    services: ProjectServicesDep,
) -> CurrentUserOut:
    """返回已认证主体；身份只来自服务端认证，不接受客户端传入的 user_id。"""
    profile = services.projects.current_user(principal)
    return CurrentUserOut.from_domain(profile, principal)


ReadContext = Annotated[
    ProjectContext,
    Depends(require_project_permission(ProjectAction.READ)),
]
ManageMembersContext = Annotated[
    ProjectContext,
    Depends(require_project_permission(ProjectAction.MANAGE_MEMBERS)),
]
PlatformAdmin = Annotated[Principal, Depends(require_platform_admin)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
StatusQuery = Annotated[str, Query(pattern="^(ACTIVE|ARCHIVED)$")]


@router.get("", response_model=PageOut[ProjectOut])
def list_projects(
    principal: CurrentPrincipal,
    services: ProjectServicesDep,
    status: StatusQuery = "ACTIVE",
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[ProjectOut]:
    """当前用户可见的项目；平台管理员可见全部，普通成员只看自己的成员关系。"""
    page = services.projects.list_projects(
        principal,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[
            ProjectOut.from_domain(
                item.project,
                role=item.role,
                is_platform_admin=principal.is_platform_admin,
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    request: Request,
    principal: PlatformAdmin,
    services: ProjectServicesDep,
) -> ProjectOut:
    """创建项目；仅平台管理员，创建者自动成为该项目的 owner。"""
    project = services.projects.create_project(
        principal,
        NewProject(
            code=payload.code,
            name=payload.name,
            description=payload.description,
        ),
        request_id=_request_id(request),
    )
    # 创建者必定是 owner（见用例），因此这里直接按 owner 返回。
    return ProjectOut.from_domain(
        project,
        role=services.projects.get_member_role(project.project_id, principal.user_id),
        is_platform_admin=principal.is_platform_admin,
    )


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: UUID,
    context: ReadContext,
    services: ProjectServicesDep,
) -> ProjectOut:
    project = services.projects.get_project(project_id)
    return ProjectOut.from_domain(
        project,
        role=context.role,
        is_platform_admin=context.is_platform_admin,
    )


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: UUID,
    payload: ProjectPatch,
    request: Request,
    context: ManageMembersContext,
    services: ProjectServicesDep,
    expected_version: ExpectedVersion,
) -> ProjectOut:
    """改名 / 改描述 / 归档；``code`` 不可改，需要 ``If-Match: "<version>"``。"""
    changes = payload.model_dump(exclude_unset=True)
    updated = services.projects.update_project(
        ProjectScope.from_context(context),
        project_id,
        expected_version=expected_version,
        changes=changes,
        actor_role=context.role.value if context.role is not None else None,
        request_id=_request_id(request),
    )
    return ProjectOut.from_domain(
        updated,
        role=context.role,
        is_platform_admin=context.is_platform_admin,
    )


@router.get("/{project_id}/members", response_model=PageOut[ProjectMemberOut])
def list_members(
    context: ReadContext,
    services: ProjectServicesDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[ProjectMemberOut]:
    page = services.projects.list_members(
        ProjectScope.from_context(context),
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[ProjectMemberOut.from_domain(member) for member in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberOut,
    status_code=201,
)
def add_member(
    payload: ProjectMemberAdd,
    request: Request,
    context: ManageMembersContext,
    services: ProjectServicesDep,
) -> ProjectMemberOut:
    """添加成员；重复添加返回 409，用户不存在返回 404。"""
    member = services.projects.add_member(
        ProjectScope.from_context(context),
        user_id=payload.user_id,
        role=payload.role,
        actor_role=context.role.value if context.role is not None else None,
        request_id=_request_id(request),
    )
    return ProjectMemberOut.from_domain(member)


@router.patch(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMemberOut,
)
def patch_member_role(
    user_id: UUID,
    payload: ProjectMemberRolePatch,
    request: Request,
    context: ManageMembersContext,
    services: ProjectServicesDep,
) -> ProjectMemberOut:
    """调整成员角色；同值不写库，降级最后一名 owner 返回 409。"""
    member = services.projects.change_member_role(
        ProjectScope.from_context(context),
        user_id=user_id,
        role=payload.role,
        actor_role=context.role.value if context.role is not None else None,
        request_id=_request_id(request),
    )
    return ProjectMemberOut.from_domain(member)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
def remove_member(
    user_id: UUID,
    request: Request,
    context: ManageMembersContext,
    services: ProjectServicesDep,
) -> Response:
    """移除成员；移除最后一名 owner（包括移除自己）返回 409。"""
    services.projects.remove_member(
        ProjectScope.from_context(context),
        user_id=user_id,
        actor_role=context.role.value if context.role is not None else None,
        request_id=_request_id(request),
    )
    return Response(status_code=204)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
