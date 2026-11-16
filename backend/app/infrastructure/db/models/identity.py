"""身份与项目隔离相关 ORM 模型。

阶段 0 建立项目、用户、项目成员三张基础表；阶段 8 为项目管理补齐
``projects.description`` / ``status`` / ``version``（迁移 0046），
其余列与 0002 保持一致。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.identity.models import ProjectRole
from app.domain.identity.projects import (
    PROJECT_DESCRIPTION_MAX_LENGTH,
    ProjectStatus,
)
from app.infrastructure.db.base import Base

_ROLE_VALUES = ", ".join(repr(role.value) for role in ProjectRole)
ROLE_CHECK = f"role IN ({_ROLE_VALUES})"
_STATUS_VALUES = ", ".join(repr(status.value) for status in ProjectStatus)
PROJECT_STATUS_CHECK = f"status IN ({_STATUS_VALUES})"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(
        String(PROJECT_DESCRIPTION_MAX_LENGTH),
        nullable=True,
    )
    # 状态用 CHECK 约束兜底：应用层已拒绝非法值，数据库不能成为绕过点。
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    # 乐观锁版本：PATCH 需要 If-Match，与服务端其它可改资源同一口径。
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_projects_code"),
        CheckConstraint(PROJECT_STATUS_CHECK, name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        # 列表按 (status, code) 键集分页；没有这个索引时归档过滤会退化为全表扫描。
        Index("ix_projects_status_code", "status", "code"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_id"),
        CheckConstraint(ROLE_CHECK, name="role_allowed"),
        Index("ix_project_members_user_id", "user_id"),
    )
