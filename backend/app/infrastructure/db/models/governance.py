"""资源配额与用量快照 ORM 模型（N9 规模化运营）。

两张表职责严格分开：

- ``resource_quotas``：**配置**，每项目每资源类型一行，可人工调整；
- ``usage_snapshots``：**最近一次离线测量**，由 Worker 整体替换，
  没有历史保留（历史曲线属于时序库，不在这张行存表里）。

``usage_snapshots`` 用 ``(project_id, resource_kind)`` 复合主键：语义就是
"每个项目每种资源只有最新一份"。加一个自增 id 只会让"取最新"变成
``DISTINCT ON`` / 窗口函数，且留下无人清理的历史行。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.governance.enums import ResourceKind
from app.infrastructure.db.base import Base

_RESOURCE_KIND_VALUES = ", ".join(repr(item.value) for item in ResourceKind)
_RESOURCE_KIND_CHECK = f"resource_kind IN ({_RESOURCE_KIND_VALUES})"
_SOURCE_MAX_LENGTH = 64


class ResourceQuotaModel(Base):
    __tablename__ = "resource_quotas"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    limit_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
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
    created_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    updated_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "resource_kind",
            name="uq_resource_quotas_project_kind",
        ),
        CheckConstraint(_RESOURCE_KIND_CHECK, name="resource_kind_allowed"),
        CheckConstraint("limit_value >= 0", name="limit_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
    )


class UsageSnapshotModel(Base):
    __tablename__ = "usage_snapshots"

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    resource_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    used_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    measurement_source: Mapped[str] = mapped_column(
        String(_SOURCE_MAX_LENGTH),
        nullable=False,
    )
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_RESOURCE_KIND_CHECK, name="resource_kind_allowed"),
        CheckConstraint("used_value >= 0", name="used_non_negative"),
    )


__all__ = ["ResourceQuotaModel", "UsageSnapshotModel"]
