"""阶段 2 上传会话、分片、RawAsset 与使用关系 ORM 模型。

所有业务表强制 ``project_id``；父引用尽量使用 ``(project_id, id)`` 复合外键。
内容去重使用部分唯一索引，只对可用状态生效。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.ingestion.enums import (
    RAW_ASSET_ACTIVE_STATUSES,
    RawAssetFormat,
    RawAssetLinkStatus,
    RawAssetLinkType,
    RawAssetSourceType,
    RawAssetStatus,
    StorageClass,
    UploadSessionStatus,
    UploadVerificationStatus,
)
from app.infrastructure.db.base import Base

_UPLOAD_SESSION_STATUS_VALUES = ", ".join(repr(item.value) for item in UploadSessionStatus)
_VERIFICATION_STATUS_VALUES = ", ".join(repr(item.value) for item in UploadVerificationStatus)
_RAW_ASSET_STATUS_VALUES = ", ".join(repr(item.value) for item in RawAssetStatus)
_RAW_ASSET_SOURCE_VALUES = ", ".join(repr(item.value) for item in RawAssetSourceType)
_RAW_ASSET_FORMAT_VALUES = ", ".join(repr(item.value) for item in RawAssetFormat)
_STORAGE_CLASS_VALUES = ", ".join(repr(item.value) for item in StorageClass)
_LINK_TYPE_VALUES = ", ".join(repr(item.value) for item in RawAssetLinkType)
_LINK_STATUS_VALUES = ", ".join(repr(item.value) for item in RawAssetLinkStatus)
_ACTIVE_STATUS_VALUES = ", ".join(repr(item) for item in sorted(RAW_ASSET_ACTIVE_STATUSES))
_SHA256_CHECK = "length(sha256) = 71 AND substr(sha256, 1, 7) = 'sha256:'"


def _enum_check(column: str, values: str, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({values})", name=name)


class UploadSessionModel(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    collection_task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    format_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    part_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allowed_content_types: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    multipart_upload_id: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aborted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    uploaded_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    uploaded_part_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    deduplicated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    deduplicated_asset_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    asset_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    validation_job_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
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
        UniqueConstraint("project_id", "id", name="uq_upload_sessions_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "collection_task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_upload_sessions_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_upload_sessions_project_batch",
            ondelete="RESTRICT",
        ),
        _enum_check("status", _UPLOAD_SESSION_STATUS_VALUES, "status_allowed"),
        _enum_check(
            "verification_status",
            _VERIFICATION_STATUS_VALUES,
            "verification_status_allowed",
        ),
        CheckConstraint("declared_size_bytes > 0", name="declared_size_positive"),
        CheckConstraint("part_size_bytes > 0", name="part_size_positive"),
        CheckConstraint("max_size_bytes > 0", name="max_size_positive"),
        CheckConstraint(
            "max_size_bytes >= declared_size_bytes",
            name="declared_size_within_max",
        ),
        CheckConstraint(
            "expected_sha256 IS NULL OR ("
            "length(expected_sha256) = 71 AND substr(expected_sha256, 1, 7) = 'sha256:'"
            ")",
            name="expected_sha256_format",
        ),
        CheckConstraint("uploaded_bytes >= 0", name="uploaded_bytes_non_negative"),
        CheckConstraint(
            "uploaded_part_count >= 0",
            name="uploaded_part_count_non_negative",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_upload_sessions_project_status_expires_at",
            "project_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_upload_sessions_project_task_status",
            "project_id",
            "collection_task_id",
            "status",
        ),
        Index("ix_upload_sessions_project_asset", "project_id", "asset_id"),
    )


class UploadPartModel(Base):
    __tablename__ = "upload_parts"

    project_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    upload_session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    part_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_upload_parts_project_session",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "part_number BETWEEN 1 AND 10000",
            name="part_number_range",
        ),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "length(sha256) = 71 AND substr(sha256, 1, 7) = 'sha256:'",
            name="sha256_format",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )


class UploadPartChecksumBindingModel(Base):
    """分片预签名 URL 声明的校验和绑定。

    预签名请求可以早于分片 ack；绑定值必须跨请求持久化，ack 时据此拒绝
    与签名 URL 不一致的分片内容。重复签名同一分片时以最新绑定为准。
    """

    __tablename__ = "upload_part_checksum_bindings"

    project_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    upload_session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    part_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_upload_part_checksum_bindings_project_session",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "part_number BETWEEN 1 AND 10000",
            name="part_number_range",
        ),
        CheckConstraint(
            "length(sha256) = 71 AND substr(sha256, 1, 7) = 'sha256:'",
            name="sha256_format",
        ),
    )


class RawAssetModel(Base):
    __tablename__ = "raw_assets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    collection_task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    upload_session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 以下五列在**领域模型与契约里早已存在，但 ORM 一直没有列**——
    # 契约 schemas.stage2.yaml 把 raw_metadata 列为 required，领域
    # RawAsset.raw_metadata 也有，API 出参也声明了，唯独落不了库，
    # 实际永远是空。这是既有契约违约，在此补齐（N2 ADP-004 的实质缺口）。
    device_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    sensor_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    sensor_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    device_time_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_metadata: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    storage_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_policy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    quarantine_codes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    scan_report_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_of_asset_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    uploaded_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
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
        UniqueConstraint("project_id", "id", name="uq_raw_assets_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_raw_assets_project_code"),
        ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_raw_assets_project_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "collection_task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_raw_assets_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_raw_assets_project_batch",
            ondelete="RESTRICT",
        ),
        _enum_check("status", _RAW_ASSET_STATUS_VALUES, "status_allowed"),
        _enum_check("source_type", _RAW_ASSET_SOURCE_VALUES, "source_type_allowed"),
        _enum_check("format", _RAW_ASSET_FORMAT_VALUES, "format_allowed"),
        _enum_check("storage_class", _STORAGE_CLASS_VALUES, "storage_class_allowed"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(_SHA256_CHECK, name="sha256_format"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_raw_assets_project_status_created_at", "project_id", "status", "created_at"),
        Index("ix_raw_assets_project_task", "project_id", "collection_task_id"),
        Index("ix_raw_assets_project_session", "project_id", "upload_session_id"),
        Index(
            "uq_raw_assets_project_content_active",
            "project_id",
            "sha256",
            "size_bytes",
            unique=True,
            postgresql_where=text(f"status IN ({_ACTIVE_STATUS_VALUES})"),
            sqlite_where=text(f"status IN ({_ACTIVE_STATUS_VALUES})"),
        ),
    )


class RawAssetLinkModel(Base):
    __tablename__ = "raw_asset_links"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    collection_task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    upload_session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    link_type: Mapped[str] = mapped_column(String(32), nullable=False)
    link_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_raw_asset_links_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "asset_id"],
            ["raw_assets.project_id", "raw_assets.id"],
            name="fk_raw_asset_links_project_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "collection_task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_raw_asset_links_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_raw_asset_links_project_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_raw_asset_links_project_session",
            ondelete="RESTRICT",
        ),
        _enum_check("link_type", _LINK_TYPE_VALUES, "link_type_allowed"),
        _enum_check("link_status", _LINK_STATUS_VALUES, "link_status_allowed"),
        Index(
            "ix_raw_asset_links_project_task_status",
            "project_id",
            "collection_task_id",
            "link_status",
        ),
        Index("ix_raw_asset_links_project_asset", "project_id", "asset_id"),
        Index(
            "uq_raw_asset_links_session",
            "project_id",
            "asset_id",
            "upload_session_id",
            "link_type",
            unique=True,
            postgresql_where=text("upload_session_id IS NOT NULL"),
            sqlite_where=text("upload_session_id IS NOT NULL"),
        ),
    )
