"""场地 ORM 模型（N9 多基地）。

与迁移 0041 逐列一致。审计/乐观锁字段直接复用采集域的
``ProjectScopedAuditMixin``——它已是仓库里"项目隔离 + 版本 + 四人字段"的
既有实现，四张素材表与三张任务表都在用。另写一份 ``ProjectScopedMixin``
会让两套审计字段形态在同一个库里并存。

为什么带 ``(project_id, id)`` 复合唯一键：``robots`` / ``devices`` /
``collection_tasks`` 要用 ``(project_id, location_id)`` 形式的**复合外键**
引用本表。没有这个唯一键，PostgreSQL 拒绝建外键（引用列必须唯一），
而单列外键挡不住跨项目引用。这与 ``collection_assets`` 四张表的做法完全一致。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.locations.enums import LocationKind
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import ProjectScopedAuditMixin

_ADDRESS_LENGTH = 500
# 与领域层 NOTES_MAX_LENGTH 对齐；两处都写死会漂移，故由迁移测试断言列长。
_NOTES_LENGTH = 2000
_KIND_VALUES = ", ".join(repr(item.value) for item in LocationKind)


class LocationModel(Base, ProjectScopedAuditMixin):
    """项目内的一个采集场地。"""

    __tablename__ = "locations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    address: Mapped[str | None] = mapped_column(String(_ADDRESS_LENGTH), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(_NOTES_LENGTH), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_locations_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_locations_project_code"),
        CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_locations_project_kind", "project_id", "kind"),
        Index("ix_locations_project_code", "project_id", "code"),
    )


__all__ = ["LocationModel"]
