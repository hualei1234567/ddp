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
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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


class MediaAssetModel(Base, ProjectScopedMixin):
    """导入产出的媒体资产：对象存储引用 + Hash + 审看所需时序提示。"""

    __tablename__ = "data_source_media_assets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    data_source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    import_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_source_imports.id", ondelete="CASCADE"), nullable=False
    )
    episode_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    camera: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    excerpt_start_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    excerpt_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_fps: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # 同一导入内同一路径只能出现一次：重复会导致审看列表出现重复项。
        UniqueConstraint("project_id", "import_id", "path", name="uq_media_assets_import_path"),
        UniqueConstraint("project_id", "id", name="uq_media_assets_project_id_id"),
        CheckConstraint("size_bytes > 0", name="media_size_positive"),
        CheckConstraint("length(sha256) = 71", name="media_sha256_length"),
        CheckConstraint(
            "excerpt_start_seconds IS NULL OR excerpt_start_seconds >= 0",
            name="media_excerpt_start_non_negative",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_media_assets_project_episode",
            "project_id",
            "data_source_id",
            "episode_key",
        ),
    )


class DataReviewModel(Base, ProjectScopedMixin):
    """导入结果的人工审核记录。

    职责分离在数据库层也留了痕迹：``reviewer_id`` 与 ``submitted_by`` 分开存，
    便于审计查询"是否存在自己审自己"的历史记录。
    """

    __tablename__ = "data_source_reviews"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    import_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_source_imports.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    reviewer_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 一个导入只有一个审核记录：重复创建会让"审核通过"的语义含糊。
        UniqueConstraint("project_id", "import_id", name="uq_reviews_project_import"),
        UniqueConstraint("project_id", "id", name="uq_reviews_project_id_id"),
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')", name="review_status_allowed"
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('ACCEPT','REJECT','NEEDS_FIX')",
            name="review_decision_allowed",
        ),
        # 职责分离：审核人不得与提交人相同。应用层也拦，这里是兜底。
        CheckConstraint(
            "reviewer_id IS NULL OR reviewer_id <> submitted_by",
            name="review_segregation_of_duties",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )
