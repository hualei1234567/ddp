"""数据源域 ORM 模型：外部数据集登记与导入记录。

表归属说明：本表属于项目级业务数据，按既有约定放在独立模块而不是
``models/collection.py``（R-18 的教训：横切表才共享模块，业务表各自成模块）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
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

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.devices import ProjectScopedMixin


class DataSourceModel(Base, ProjectScopedMixin):
    __tablename__ = "data_sources"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    repo_id: Mapped[str] = mapped_column(String(200), nullable=False)
    revision: Mapped[str] = mapped_column(String(100), nullable=False)
    license: Mapped[str] = mapped_column(String(100), nullable=False)
    access_condition: Mapped[str] = mapped_column(String(32), nullable=False)
    is_gated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    config_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    split: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dataset_card_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_data_sources_project_code"),
        # 同一项目内同一 repo + revision + config + split 只能登记一次，
        # 否则同一份数据会被重复导入，破坏去重与血缘。
        UniqueConstraint(
            "project_id",
            "repo_id",
            "revision",
            "config_name",
            "split",
            name="uq_data_sources_project_repo_revision",
        ),
        UniqueConstraint("project_id", "id", name="uq_data_sources_project_id_id"),
        CheckConstraint(
            "access_condition IN ('PUBLIC','GATED','PRIVATE','UNKNOWN')",
            name="access_condition_allowed",
        ),
        CheckConstraint("kind IN ('HUGGINGFACE')", name="kind_allowed"),
        # GATED 与 is_gated 必须一致，防止"标公开实际要申请"。
        CheckConstraint(
            "(access_condition = 'GATED' AND is_gated = true) "
            "OR (access_condition <> 'GATED' AND access_condition <> 'PUBLIC') "
            "OR (access_condition = 'PUBLIC' AND is_gated = false)",
            name="gated_consistency",
        ),
        # revision 必须是 40 位 commit sha：可复现性底线。
        CheckConstraint("length(revision) = 40", name="revision_is_commit_sha"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_data_sources_project_kind", "project_id", "kind"),
    )


class DataSourceImportModel(Base, ProjectScopedMixin):
    __tablename__ = "data_source_imports"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    data_source_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(100), nullable=False)
    sample_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_location: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    imported_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_data_source_imports_project_id_id"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="import_status_allowed",
        ),
        CheckConstraint("scope IN ('SAMPLE','SPLIT','FULL')", name="import_scope_allowed"),
        CheckConstraint(
            "scope <> 'SAMPLE' OR sample_limit IS NOT NULL",
            name="sample_scope_requires_limit",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_data_source_imports_source_status",
            "project_id",
            "data_source_id",
            "status",
        ),
    )
