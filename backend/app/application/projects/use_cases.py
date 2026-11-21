"""项目管理与人员/团队用例（阶段 8）。

## 权限判定在哪里

服务端授权只有一处权威实现：``app.domain.identity.models.ProjectContext``
与 ``app.api.deps`` 的依赖。本模块**不重复实现**任何角色判断，只做两件事：

- 项目创建没有项目上下文，平台级权限（``PlatformRole.ADMIN``）在此显式校验；
- 其余方法接收已经过依赖校验的 ``ProjectScope``，只负责编排。

## 为什么没有 ``Idempotency-Key``

创建项目的幂等性由 ``projects.code`` 唯一约束天然保证：同一 code 的第二次
提交返回 409，不会产生第二行。成员变更同理——``(project_id, user_id)``
唯一，重复添加返回 409；改角色与移除是按值幂等的语义（同值不写库）。
因此本域不需要 ``idempotency_records``，也就没有"没有项目作用域就无法落幂等
记录"的循环依赖。

## 并发：为什么成员变更要锁项目行

"项目至少保留一名 owner"是两个语句的判定：先数 owner，再改角色。两个并发
请求各自降级不同的 owner 时，都能看到 2 名 owner 从而同时通过检查，提交后
项目变成 0 名 owner——成员管理权限永久无人持有。因此改角色/移除成员前先对
``projects`` 行加锁（PostgreSQL ``SELECT ... FOR UPDATE``；SQLite 忽略该子句，
但其写事务本身串行），把同一项目的成员写操作串成串行。

## 为什么没有删除项目

``projects.id`` 被全部业务表以 ``ON DELETE CASCADE`` 引用，硬删除会一次性
销毁该项目的采集、Episode、标注、数据集与审计记录，且不可恢复。本阶段只提供
``ARCHIVED`` 状态位（移除出默认列表），删除入口留空；清理数据必须走独立的、
逐条确认的运维流程。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.projects.commands import NewProject
from app.application.projects.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.projects.ports import (
    AuditRepository,
    OutboxRepository,
    ProjectListing,
    ProjectMemberRepository,
    ProjectRepository,
    UnitOfWork,
    UserDirectory,
    UserProfile,
)
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.domain.identity.models import Principal, ProjectRole, ProjectScope
from app.domain.identity.projects import (
    Project,
    ProjectMember,
    ProjectStatus,
    leaves_project_without_owner,
    new_project,
    parse_project_role,
    parse_project_status,
)

_PROJECT_RESOURCE_TYPE = "PROJECT"
_PROJECT_MEMBER_RESOURCE_TYPE = "PROJECT_MEMBER"

# 事件名必须是字面量：``scripts/check_outbox_events.py`` 用 AST 提取事件类型，
# f-string 会被判失败（R-13）。五个事件都是**留痕类**（dispatch=False），
# 没有消费者，写入即 ``RECORDED``。
PROJECT_CREATED_EVENT = "project.created"
PROJECT_UPDATED_EVENT = "project.updated"
PROJECT_MEMBER_ADDED_EVENT = "project.member_added"
PROJECT_MEMBER_ROLE_CHANGED_EVENT = "project.member_role_changed"
PROJECT_MEMBER_REMOVED_EVENT = "project.member_removed"

_PROJECT_LABEL = "项目"


class ProjectUseCases:
    def __init__(
        self,
        projects: ProjectRepository,
        members: ProjectMemberRepository,
        users: UserDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._projects = projects
        self._members = members
        self._users = users
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    # --- 当前主体 -----------------------------------------------------
    def current_user(self, principal: Principal) -> UserProfile:
        """回显已认证主体；只读，不创建账号、不改任何状态。

        令牌里的 ``user_id`` 不保证在用户目录中存在（外部签发），所以这里
        必须查目录：查不到就 404，而不是凭空返回一个只有 ID 的裸对象——
        「这个令牌属于谁」是审计与个人视图的基础，不能靠推断。
        """
        profile = self._users.get_profile(principal.user_id)
        if profile is None:
            raise NotFoundError("当前用户不在用户目录中")
        return profile

    # --- 项目 ---------------------------------------------------------
    def list_projects(
        self,
        principal: Principal,
        *,
        status: str = ProjectStatus.ACTIVE.value,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[ProjectListing]:
        bounded = normalize_limit(limit)
        project_status = _status_filter(status)
        items = self._projects.list_visible(
            # 平台管理员看全部项目；普通用户只看自己的成员关系。
            user_id=None if principal.is_platform_admin else principal.user_id,
            status=project_status,
            after_code=decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, key_of=lambda item: item.project.code)

    def create_project(
        self,
        principal: Principal,
        command: NewProject,
        *,
        request_id: str | None = None,
    ) -> Project:
        """创建项目，并把创建者登记为 owner。

        只有平台管理员可以创建：项目创建发生在任何项目上下文之外，普通成员的
        权限完全来自成员关系，没有"可创建项目"的既有信号。把创建者写成 owner
        保证项目有人能继续管理成员，而不是只能由平台管理员接手。
        """
        if not principal.is_platform_admin:
            raise ForbiddenError("仅平台管理员可以创建项目")

        now = datetime.now(UTC)
        try:
            project = new_project(
                project_id=uuid4(),
                code=command.code,
                name=command.name,
                description=command.description,
                now=now,
            )
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc

        # 先给出可读 409，再落库：唯一约束是最后一道兜底（并发窗口内仍可能命中，
        # 由全局 IntegrityError 处理器返回 409）。
        if self._projects.get_by_code(project.code) is not None:
            raise ConflictError("项目 code 已存在")
        if not self._users.exists(principal.user_id):
            # project_members.user_id 有外键；用户目录里没有这个人时，
            # 写成员关系必然失败。明确拒绝，而不是留下一个无人可管理的项目。
            raise ForbiddenError("当前用户未在用户目录中登记，无法创建项目")

        self._projects.add(project)
        scope = ProjectScope(project_id=project.project_id, actor_id=principal.user_id)
        owner = self._members.add(
            scope,
            user_id=principal.user_id,
            role=ProjectRole.OWNER,
            now=now,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_PROJECT_RESOURCE_TYPE,
            resource_id=project.project_id,
            action="create",
            from_status=None,
            to_status=project.status.value,
            reason=None,
            request_id=request_id,
            details={
                "code": project.code,
                "name": project.name,
                "owner_user_id": str(owner.user_id),
            },
            event_type=PROJECT_CREATED_EVENT,
            event_payload={
                "project_id": str(project.project_id),
                "code": project.code,
                "status": project.status.value,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return project

    def get_project(self, project_id: UUID) -> Project:
        """按 ID 读取；可见性已由路由依赖（成员/管理员）校验。"""
        project = self._projects.get(project_id)
        if project is None:
            raise NotFoundError("项目不存在")
        return project

    def get_member_role(self, project_id: UUID, user_id: UUID) -> ProjectRole | None:
        """当前主体在项目中的角色；用于响应里的 ``membership_role`` 提示。"""
        member = self._members.get(
            ProjectScope(project_id=project_id, actor_id=user_id),
            user_id,
        )
        return None if member is None else member.role

    def update_project(
        self,
        scope: ProjectScope,
        project_id: UUID,
        *,
        expected_version: int,
        changes: dict[str, object],
        actor_role: str | None = None,
        request_id: str | None = None,
    ) -> Project:
        current = self.get_project(project_id)
        if current.version != expected_version:
            raise ConflictError("项目已被修改，请刷新后重试")

        now = datetime.now(UTC)
        try:
            updated = current.apply_patch(changes, now=now)
        except ValueError as exc:
            # 领域消息是固定文案，不含客户端原始输入（不回声 code/name 原值）。
            raise ValidationFailedError(str(exc)) from exc

        if not self._projects.save(updated, expected_version=expected_version):
            # UPDATE 未命中：并发窗口内被其它请求改过版本。
            raise ConflictError("项目已被修改，请刷新后重试")

        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_PROJECT_RESOURCE_TYPE,
            resource_id=updated.project_id,
            action="update",
            from_status=current.status.value,
            to_status=updated.status.value,
            reason=None,
            request_id=request_id,
            actor_role=actor_role,
            details={"fields": sorted(changes), "version": updated.version},
            event_type=PROJECT_UPDATED_EVENT,
            event_payload={
                "project_id": str(updated.project_id),
                "fields": sorted(changes),
                "version": updated.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return updated

    # --- 成员 ---------------------------------------------------------
    def list_members(
        self,
        scope: ProjectScope,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[ProjectMember]:
        bounded = normalize_limit(limit)
        raw_cursor = decode_cursor(cursor)
        after_user_id = _parse_user_cursor(raw_cursor)
        items = self._members.list(
            scope,
            after_user_id=after_user_id,
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, key_of=lambda item: str(item.user_id))

    def add_member(
        self,
        scope: ProjectScope,
        *,
        user_id: UUID,
        role: str,
        actor_role: str | None = None,
        request_id: str | None = None,
    ) -> ProjectMember:
        parsed_role = _parse_role(role)
        if not self._users.exists(user_id):
            raise NotFoundError("用户不存在")
        if self._members.get(scope, user_id) is not None:
            raise ConflictError("该用户已是项目成员")

        now = datetime.now(UTC)
        member = self._members.add(
            scope,
            user_id=user_id,
            role=parsed_role,
            now=now,
        )
        _record_member_change(
            self._audit,
            self._outbox,
            scope,
            user_id=user_id,
            action="add",
            from_role=None,
            to_role=parsed_role,
            event_type=PROJECT_MEMBER_ADDED_EVENT,
            actor_role=actor_role,
            request_id=request_id,
            occurred_at=now,
        )
        self._uow.commit()
        return member

    def change_member_role(
        self,
        scope: ProjectScope,
        *,
        user_id: UUID,
        role: str,
        actor_role: str | None = None,
        request_id: str | None = None,
    ) -> ProjectMember:
        parsed_role = _parse_role(role)
        # 先锁项目行，再数 owner：否则并发的两次降级会各自看到 2 名 owner。
        self._projects.lock_for_member_change(scope.project_id)
        current = self._members.get(scope, user_id)
        if current is None:
            raise NotFoundError("项目成员不存在")
        if current.role is parsed_role:
            # 同值不写库、不产生审计噪声：PATCH 的幂等语义。
            return current

        owner_count = self._members.count_owners(scope)
        if leaves_project_without_owner(
            current_role=current.role,
            new_role=parsed_role,
            owner_count=owner_count,
        ):
            raise ConflictError("项目至少保留一名 owner")

        now = datetime.now(UTC)
        if not self._members.update_role(scope, user_id, role=parsed_role, now=now):
            raise NotFoundError("项目成员不存在")
        _record_member_change(
            self._audit,
            self._outbox,
            scope,
            user_id=user_id,
            action="update",
            from_role=current.role,
            to_role=parsed_role,
            event_type=PROJECT_MEMBER_ROLE_CHANGED_EVENT,
            actor_role=actor_role,
            request_id=request_id,
            occurred_at=now,
        )
        self._uow.commit()
        return ProjectMember(
            project_id=scope.project_id,
            user_id=user_id,
            display_name=current.display_name,
            role=parsed_role,
            created_at=current.created_at,
            updated_at=now,
        )

    def remove_member(
        self,
        scope: ProjectScope,
        *,
        user_id: UUID,
        actor_role: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self._projects.lock_for_member_change(scope.project_id)
        current = self._members.get(scope, user_id)
        if current is None:
            raise NotFoundError("项目成员不存在")

        owner_count = self._members.count_owners(scope)
        if leaves_project_without_owner(
            current_role=current.role,
            new_role=None,
            owner_count=owner_count,
        ):
            # 包括 owner 移除自己：项目必须始终有人能管理成员。
            raise ConflictError("项目至少保留一名 owner")

        now = datetime.now(UTC)
        if not self._members.remove(scope, user_id):
            raise NotFoundError("项目成员不存在")
        _record_member_change(
            self._audit,
            self._outbox,
            scope,
            user_id=user_id,
            action="remove",
            from_role=current.role,
            to_role=None,
            event_type=PROJECT_MEMBER_REMOVED_EVENT,
            actor_role=actor_role,
            request_id=request_id,
            occurred_at=now,
        )
        self._uow.commit()


def _record_member_change(
    audit: AuditRepository,
    outbox: OutboxRepository,
    scope: ProjectScope,
    *,
    user_id: UUID,
    action: str,
    from_role: ProjectRole | None,
    to_role: ProjectRole | None,
    event_type: str,
    actor_role: str | None,
    request_id: str | None,
    occurred_at: datetime,
) -> None:
    """成员变更的审计 + outbox 留痕：三个写路径共用一份。

    重复三遍会让"新增一个字段要改三处"，也是历史上事件类型漂移的成因之一。
    """
    payload: dict[str, object] = {
        "project_id": str(scope.project_id),
        "user_id": str(user_id),
    }
    if to_role is not None:
        payload["role"] = to_role.value
    record_change(
        audit,
        outbox,
        scope,
        resource_type=_PROJECT_MEMBER_RESOURCE_TYPE,
        resource_id=user_id,
        action=action,
        from_status=from_role.value if from_role is not None else None,
        to_status=to_role.value if to_role is not None else None,
        reason=None,
        request_id=request_id,
        actor_role=actor_role,
        details={"user_id": str(user_id), "role": to_role.value if to_role else None},
        event_type=event_type,
        event_payload=payload,
        occurred_at=occurred_at,
    )


def _status_filter(value: str) -> ProjectStatus:
    try:
        return parse_project_status(value)
    except ValueError as exc:
        raise ValidationFailedError("status 取值非法") from exc


def _parse_role(value: str) -> ProjectRole:
    try:
        return parse_project_role(value)
    except ValueError as exc:
        raise ValidationFailedError("role 取值非法") from exc


def _parse_user_cursor(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationFailedError("cursor 非法") from exc


__all__ = [
    "PROJECT_CREATED_EVENT",
    "PROJECT_MEMBER_ADDED_EVENT",
    "PROJECT_MEMBER_REMOVED_EVENT",
    "PROJECT_MEMBER_ROLE_CHANGED_EVENT",
    "PROJECT_UPDATED_EVENT",
    "ProjectUseCases",
]
