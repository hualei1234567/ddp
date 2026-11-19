"""采集素材 ORM：采集方案、SOP、场景、动作库（COL-002）。

四张表全部沿用采集域既有范式（见 ``models/collection.py``）：
- 强制 ``project_id``，且带 ``(project_id, id)`` 复合唯一键，
  使 ``collection_tasks`` 能用 ``(project_id, plan_id)`` 形式的**复合外键**
  引用它们——跨项目引用由数据库直接拒绝，这是本次修复的核心；
- 状态列 ``varchar + CHECK``，取值来自 :class:`CollectionAssetStatus`；
- JSON 列在 PostgreSQL 上用 JSONB、SQLite 上用 JSON（与标注域一致）；
- 审计/乐观锁字段与 ``ProjectScopedAuditMixin`` 完全一致，故直接复用该 Mixin。

迁移 0040 与本模块必须逐列一致：``test_schema_builds_clean`` 之外的
``tests/migrations/test_collection_plan_entities_migration.py`` 会比对两者。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.collection.enums import CollectionAssetStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import ProjectScopedAuditMixin

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_STATUS_VALUES = ", ".join(repr(item.value) for item in CollectionAssetStatus)
# 与领域层 DESCRIPTION_MAX_LENGTH 对齐；两处都写死会漂移，故只在此处定义列长，
# 领域层用同一数值校验，并由迁移测试断言列长。
_DESCRIPTION_LENGTH = 2000


def _status_check() -> CheckConstraint:
    return CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_allowed")


class CollectionPlanModel(Base, ProjectScopedAuditMixin):
    """采集方案；同一 ``code`` 在项目内可有多行，以 ``plan_version`` 区分。"""

    __tablename__ = "collection_plans"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(_DESCRIPTION_LENGTH), nullable=True)
    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    # 采集目标/质量阈值是计划字段，不是执行结果，也不构成自动验收依据。
    # NULL 表示"未设定目标"，与 0 区分；阈值默认空对象表示"未指定"。
    target_episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_thresholds: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_collection_plans_project_id_id"),
        UniqueConstraint(
            "project_id",
            "code",
            "plan_version",
            name="uq_collection_plans_project_code_version",
        ),
        _status_check(),
        CheckConstraint("plan_version >= 1", name="plan_version_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_collection_plans_project_status", "project_id", "status"),
        Index("ix_collection_plans_project_code", "project_id", "code"),
    )


class CollectionSopModel(Base, ProjectScopedAuditMixin):
    """SOP；``sop_version`` 是业务版本文本（``v1`` / ``2026-11``）。"""

    __tablename__ = "collection_sops"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sop_version: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(_DESCRIPTION_LENGTH), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_collection_sops_project_id_id"),
        UniqueConstraint(
            "project_id",
            "code",
            "sop_version",
            name="uq_collection_sops_project_code_version",
        ),
        _status_check(),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_collection_sops_project_status", "project_id", "status"),
        Index("ix_collection_sops_project_code", "project_id", "code"),
    )


class ScenarioModel(Base, ProjectScopedAuditMixin):
    """采集场景；不版本化，``code`` 在项目内唯一。"""

    __tablename__ = "scenarios"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scene_labels: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(_DESCRIPTION_LENGTH), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_scenarios_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_scenarios_project_code"),
        _status_check(),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_scenarios_project_status", "project_id", "status"),
    )


class ActionSpecModel(Base, ProjectScopedAuditMixin):
    """动作库；``spec_version`` 是业务版本文本。"""

    __tablename__ = "action_specs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spec_version: Mapped[str] = mapped_column(String(64), nullable=False)
    actions: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(_DESCRIPTION_LENGTH), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_action_specs_project_id_id"),
        UniqueConstraint(
            "project_id",
            "code",
            "spec_version",
            name="uq_action_specs_project_code_version",
        ),
        _status_check(),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_action_specs_project_status", "project_id", "status"),
        Index("ix_action_specs_project_code", "project_id", "code"),
    )


__all__ = [
    "ActionSpecModel",
    "CollectionPlanModel",
    "CollectionSopModel",
    "ScenarioModel",
]
