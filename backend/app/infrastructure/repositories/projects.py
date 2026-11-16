"""项目与项目成员的 SQLAlchemy 仓储。

项目表没有 ``project_id`` 列（它自身就是项目），因此不能复用
``ProjectScopedAuditMixin``。成员表则强制按服务端 ``ProjectScope`` 过滤，
跨项目读写不可能发生。

``save`` 用 ``WHERE id = :id AND version = :expected`` 的 UPDATE 实现乐观锁：
命中 0 行返回 ``False``，由用例统一映射为 409，而不是在仓储里构造 HTTP 语义。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.application.projects.ports import ProjectListing
from app.domain.identity.models import ProjectRole, ProjectScope
from app.domain.identity.projects import Project, ProjectMember, ProjectStatus
from app.infrastructure.db.models.identity import Project as ProjectModel
from app.infrastructure.db.models.identity import ProjectMember as ProjectMemberModel
from app.infrastructure.db.models.identity import User as UserModel

_FETCH_LIMIT_MAX = 101


def _check_limit(limit: int) -> None:
    if limit < 1 or limit > _FETCH_LIMIT_MAX:
        raise ValueError(f"limit 必须在 1 到 {_FETCH_LIMIT_MAX} 之间")


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 不保留时区；读回时按 UTC 解释，避免响应里出现无时区时间。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _role(raw: str) -> ProjectRole:
    try:
        return ProjectRole(raw)
    except ValueError as exc:
        # 数据库出现未知角色时显式失败，绝不默认放行（与成员仓储仓储同一口径）。
        raise ValueError(f"数据库中项目角色非法：{raw!r}") from exc


def _project(row: ProjectModel) -> Project:
    return Project(
        project_id=row.id,
        code=row.code,
        name=row.name,
        description=row.description,
        status=ProjectStatus(row.status),
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


class SqlProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: UUID) -> Project | None:
        row = self._session.get(ProjectModel, project_id)
        return None if row is None else _project(row)

    def get_by_code(self, code: str) -> Project | None:
        row = self._session.scalar(select(ProjectModel).where(ProjectModel.code == code.strip()))
        return None if row is None else _project(row)

    def add(self, project: Project) -> Project:
        self._session.add(
            ProjectModel(
                id=project.project_id,
                code=project.code,
                name=project.name,
                description=project.description,
                status=project.status.value,
                version=project.version,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
        )
        self._session.flush()
        return project

    def save(self, project: Project, *, expected_version: int) -> bool:
        result = self._session.execute(
            update(ProjectModel)
            .where(
                ProjectModel.id == project.project_id,
                ProjectModel.version == expected_version,
            )
            .values(
                name=project.name,
                description=project.description,
                status=project.status.value,
                updated_at=project.updated_at or datetime.now(UTC),
                version=expected_version + 1,
            )
        )
        return result.rowcount > 0

    def list_visible(
        self,
        *,
        user_id: UUID | None,
        status: ProjectStatus,
        after_code: str | None,
        limit: int,
    ) -> list[ProjectListing]:
        _check_limit(limit)
        if user_id is None:
            # 平台管理员视角：全部项目，不带成员角色。
            statement = select(ProjectModel).where(ProjectModel.status == status.value)
            if after_code is not None:
                statement = statement.where(ProjectModel.code > after_code)
            statement = statement.order_by(ProjectModel.code).limit(limit)
            return [
                ProjectListing(project=_project(row), role=None)
                for row in self._session.scalars(statement)
            ]

        statement = (
            select(ProjectModel, ProjectMemberModel.role)
            .join(
                ProjectMemberModel,
                (ProjectMemberModel.project_id == ProjectModel.id)
                & (ProjectMemberModel.user_id == user_id),
            )
            .where(ProjectModel.status == status.value)
        )
        if after_code is not None:
            statement = statement.where(ProjectModel.code > after_code)
        statement = statement.order_by(ProjectModel.code).limit(limit)
        return [
            ProjectListing(project=_project(row), role=_role(raw_role))
            for row, raw_role in self._session.execute(statement)
        ]

    def lock_for_member_change(self, project_id: UUID) -> None:
        """锁项目行；SQLite 忽略 ``FOR UPDATE``，其写事务本身串行。"""
        self._session.execute(
            select(ProjectModel.id).where(ProjectModel.id == project_id).with_for_update()
        )


class SqlProjectMemberRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(
        self,
        scope: ProjectScope,
        *,
        after_user_id: UUID | None,
        limit: int,
    ) -> list[ProjectMember]:
        _check_limit(limit)
        statement = (
            select(ProjectMemberModel, UserModel.display_name)
            .join(UserModel, UserModel.id == ProjectMemberModel.user_id)
            .where(ProjectMemberModel.project_id == scope.project_id)
        )
        if after_user_id is not None:
            statement = statement.where(ProjectMemberModel.user_id > after_user_id)
        statement = statement.order_by(ProjectMemberModel.user_id).limit(limit)
        return [
            _member(row, display_name) for row, display_name in self._session.execute(statement)
        ]

    def get(self, scope: ProjectScope, user_id: UUID) -> ProjectMember | None:
        row = self._session.execute(
            select(ProjectMemberModel, UserModel.display_name)
            .join(UserModel, UserModel.id == ProjectMemberModel.user_id)
            .where(
                ProjectMemberModel.project_id == scope.project_id,
                ProjectMemberModel.user_id == user_id,
            )
        ).first()
        if row is None:
            return None
        member_row, display_name = row
        return _member(member_row, display_name)

    def add(
        self,
        scope: ProjectScope,
        *,
        user_id: UUID,
        role: ProjectRole,
        now: datetime,
    ) -> ProjectMember:
        self._session.add(
            ProjectMemberModel(
                id=uuid4(),
                project_id=scope.project_id,
                user_id=user_id,
                role=role.value,
                created_at=now,
                updated_at=now,
            )
        )
        # 唯一约束 (project_id, user_id) 是并发下的最后一道兜底；
        # 命中时由全局 IntegrityError 处理器返回 409。
        self._session.flush()
        member = self.get(scope, user_id)
        if member is None:
            # 刚写入就读不到，说明数据层不一致，必须显式失败而不是返回半成品。
            raise RuntimeError("项目成员写入成功但读取失败")
        return member

    def update_role(
        self,
        scope: ProjectScope,
        user_id: UUID,
        *,
        role: ProjectRole,
        now: datetime,
    ) -> bool:
        result = self._session.execute(
            update(ProjectMemberModel)
            .where(
                ProjectMemberModel.project_id == scope.project_id,
                ProjectMemberModel.user_id == user_id,
            )
            .values(role=role.value, updated_at=now)
        )
        return result.rowcount > 0

    def remove(self, scope: ProjectScope, user_id: UUID) -> bool:
        result = self._session.execute(
            delete(ProjectMemberModel).where(
                ProjectMemberModel.project_id == scope.project_id,
                ProjectMemberModel.user_id == user_id,
            )
        )
        return result.rowcount > 0

    def count_owners(self, scope: ProjectScope) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ProjectMemberModel)
                .where(
                    ProjectMemberModel.project_id == scope.project_id,
                    ProjectMemberModel.role == ProjectRole.OWNER.value,
                )
            )
            or 0
        )


class SqlUserDirectory:
    """用户目录：本阶段只做存在性判定（用户创建/邀请不在范围内）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, user_id: UUID) -> bool:
        return self._session.scalar(select(UserModel.id).where(UserModel.id == user_id)) is not None


def _member(row: ProjectMemberModel, display_name: str) -> ProjectMember:
    return ProjectMember(
        project_id=row.project_id,
        user_id=row.user_id,
        display_name=display_name,
        role=_role(row.role),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


__all__ = [
    "SqlProjectMemberRepository",
    "SqlProjectRepository",
    "SqlUserDirectory",
]
