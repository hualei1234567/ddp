"""stage2 upload sessions, parts, raw assets and usage links

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08

建立上传会话、分片、RawAsset 与使用关系：
- 所有表强制 ``project_id``，父引用使用 ``(project_id, id)`` 复合外键；
- 会话/资产状态使用 ``varchar + CHECK``，初始状态与合法迁移由领域动作保证；
- 内容去重使用部分唯一索引，只对 VALID/QUARANTINED/ARCHIVED 生效；
- 对象 Key、Hash 长度/前缀在 DB 层兜底，完整格式由应用层校验。

阶段 2 尚无历史采集数据，本迁移不做回填。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.ingestion_guards import (
    drop_ingestion_state_guards,
    install_ingestion_state_guards,
)

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_UPLOAD_STATUS_VALUES = (
    "'CREATED', 'UPLOADING', 'COMPLETING', 'COMPLETED', 'ABORTED', 'EXPIRED', 'FAILED'"
)
_VERIFICATION_STATUS_VALUES = "'PENDING', 'VERIFIED', 'REJECTED'"
_RAW_ASSET_STATUS_VALUES = (
    "'VALIDATING', 'VALID', 'QUARANTINED', 'REJECTED', 'ARCHIVED', 'TOMBSTONED'"
)
_RAW_ASSET_SOURCE_VALUES = "'UPLOAD', 'DEVICE', 'IMPORT'"
_RAW_ASSET_FORMAT_VALUES = (
    "'MCAP', 'ROSBAG2', 'HDF5', 'MP4', 'PARQUET', 'CSV', 'JSON', 'ZIP', 'UNKNOWN'"
)
_STORAGE_CLASS_VALUES = "'STANDARD', 'INFREQUENT', 'ARCHIVE'"
_LINK_TYPE_VALUES = "'UPLOAD_SESSION', 'COLLECTION_TASK', 'COLLECTION_BATCH'"
_LINK_STATUS_VALUES = "'PENDING_VERIFICATION', 'ACTIVE', 'REVOKED'"
_ACTIVE_STATUS_VALUES = "'VALID', 'QUARANTINED', 'ARCHIVED'"
_SHA256_CHECK = "length(sha256) = 71 AND substr(sha256, 1, 7) = 'sha256:'"


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    ]


def _audit_constraints(table: str) -> list[sa.schema.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{table}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{table}_updated_by_users",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("collection_task_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("verification_status", sa.String(length=16), nullable=False),
        sa.Column("verification_error_code", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_content_type", sa.String(length=200), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(length=71), nullable=True),
        sa.Column("format_hint", sa.String(length=32), nullable=True),
        sa.Column("part_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "allowed_content_types", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("multipart_upload_id", sa.String(length=256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aborted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_summary", sa.String(length=2000), nullable=True),
        sa.Column("uploaded_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("uploaded_part_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deduplicated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deduplicated_asset_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("validation_job_id", sa.Uuid(), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_upload_sessions"),
        sa.UniqueConstraint("project_id", "id", name="uq_upload_sessions_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_upload_sessions_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "collection_task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_upload_sessions_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_upload_sessions_project_batch",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN ({_UPLOAD_STATUS_VALUES})",
            name=op.f("ck_upload_sessions_status_allowed"),
        ),
        sa.CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUS_VALUES})",
            name=op.f("ck_upload_sessions_verification_status_allowed"),
        ),
        sa.CheckConstraint(
            "declared_size_bytes > 0",
            name=op.f("ck_upload_sessions_declared_size_positive"),
        ),
        sa.CheckConstraint(
            "part_size_bytes > 0",
            name=op.f("ck_upload_sessions_part_size_positive"),
        ),
        sa.CheckConstraint(
            "max_size_bytes > 0",
            name=op.f("ck_upload_sessions_max_size_positive"),
        ),
        sa.CheckConstraint(
            "max_size_bytes >= declared_size_bytes",
            name=op.f("ck_upload_sessions_declared_size_within_max"),
        ),
        sa.CheckConstraint(
            "expected_sha256 IS NULL OR ("
            "length(expected_sha256) = 71 AND substr(expected_sha256, 1, 7) = 'sha256:'"
            ")",
            name=op.f("ck_upload_sessions_expected_sha256_format"),
        ),
        sa.CheckConstraint(
            "uploaded_bytes >= 0",
            name=op.f("ck_upload_sessions_uploaded_bytes_non_negative"),
        ),
        sa.CheckConstraint(
            "uploaded_part_count >= 0",
            name=op.f("ck_upload_sessions_uploaded_part_count_non_negative"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_upload_sessions_version_positive")),
        *_audit_constraints("upload_sessions"),
    )
    op.create_index(
        "ix_upload_sessions_project_status_expires_at",
        "upload_sessions",
        ["project_id", "status", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_upload_sessions_project_task_status",
        "upload_sessions",
        ["project_id", "collection_task_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_upload_sessions_project_asset",
        "upload_sessions",
        ["project_id", "asset_id"],
        unique=False,
    )

    op.create_table(
        "upload_parts",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("etag", sa.String(length=256), nullable=False),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint(
            "project_id",
            "upload_session_id",
            "part_number",
            name="pk_upload_parts",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_upload_parts_project_session",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "part_number BETWEEN 1 AND 10000",
            name=op.f("ck_upload_parts_part_number_range"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_upload_parts_size_positive")),
        sa.CheckConstraint(_SHA256_CHECK, name=op.f("ck_upload_parts_sha256_format")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_upload_parts_version_positive")),
    )

    op.create_table(
        "raw_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("collection_task_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("upload_session_id", sa.Uuid(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_class", sa.String(length=16), nullable=False),
        sa.Column("retention_policy_id", sa.Uuid(), nullable=True),
        sa.Column("quarantine_reason", sa.String(length=1000), nullable=True),
        sa.Column("quarantine_codes", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("scan_report_key", sa.String(length=1024), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duplicate_of_asset_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_raw_assets"),
        sa.UniqueConstraint("project_id", "id", name="uq_raw_assets_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_raw_assets_project_code"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_raw_assets_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_raw_assets_project_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "collection_task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_raw_assets_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_raw_assets_project_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name="fk_raw_assets_uploaded_by_users",
        ),
        sa.CheckConstraint(
            f"status IN ({_RAW_ASSET_STATUS_VALUES})",
            name=op.f("ck_raw_assets_status_allowed"),
        ),
        sa.CheckConstraint(
            f"source_type IN ({_RAW_ASSET_SOURCE_VALUES})",
            name=op.f("ck_raw_assets_source_type_allowed"),
        ),
        sa.CheckConstraint(
            f"format IN ({_RAW_ASSET_FORMAT_VALUES})",
            name=op.f("ck_raw_assets_format_allowed"),
        ),
        sa.CheckConstraint(
            f"storage_class IN ({_STORAGE_CLASS_VALUES})",
            name=op.f("ck_raw_assets_storage_class_allowed"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_raw_assets_size_positive")),
        sa.CheckConstraint(_SHA256_CHECK, name=op.f("ck_raw_assets_sha256_format")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_raw_assets_version_positive")),
        *_audit_constraints("raw_assets"),
    )
    op.create_index(
        "ix_raw_assets_project_status_created_at",
        "raw_assets",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_raw_assets_project_task",
        "raw_assets",
        ["project_id", "collection_task_id"],
        unique=False,
    )
    op.create_index(
        "ix_raw_assets_project_session",
        "raw_assets",
        ["project_id", "upload_session_id"],
        unique=False,
    )
    op.create_index(
        "uq_raw_assets_project_content_active",
        "raw_assets",
        ["project_id", "sha256", "size_bytes"],
        unique=True,
        sqlite_where=sa.text(f"status IN ({_ACTIVE_STATUS_VALUES})"),
        postgresql_where=sa.text(f"status IN ({_ACTIVE_STATUS_VALUES})"),
    )

    op.create_table(
        "raw_asset_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("collection_task_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("upload_session_id", sa.Uuid(), nullable=True),
        sa.Column("link_type", sa.String(length=32), nullable=False),
        sa.Column("link_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_raw_asset_links"),
        sa.UniqueConstraint("project_id", "id", name="uq_raw_asset_links_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_raw_asset_links_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "asset_id"],
            ["raw_assets.project_id", "raw_assets.id"],
            name="fk_raw_asset_links_project_asset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "collection_task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_raw_asset_links_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_raw_asset_links_project_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_raw_asset_links_project_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_raw_asset_links_created_by_users",
        ),
        sa.CheckConstraint(
            f"link_type IN ({_LINK_TYPE_VALUES})",
            name=op.f("ck_raw_asset_links_link_type_allowed"),
        ),
        sa.CheckConstraint(
            f"link_status IN ({_LINK_STATUS_VALUES})",
            name=op.f("ck_raw_asset_links_link_status_allowed"),
        ),
    )
    op.create_index(
        "ix_raw_asset_links_project_task_status",
        "raw_asset_links",
        ["project_id", "collection_task_id", "link_status"],
        unique=False,
    )
    op.create_index(
        "ix_raw_asset_links_project_asset",
        "raw_asset_links",
        ["project_id", "asset_id"],
        unique=False,
    )
    op.create_index(
        "uq_raw_asset_links_session",
        "raw_asset_links",
        ["project_id", "asset_id", "upload_session_id", "link_type"],
        unique=True,
        sqlite_where=sa.text("upload_session_id IS NOT NULL"),
        postgresql_where=sa.text("upload_session_id IS NOT NULL"),
    )
    install_ingestion_state_guards(op.get_bind())


def downgrade() -> None:
    drop_ingestion_state_guards(op.get_bind())
    op.drop_index("uq_raw_asset_links_session", table_name="raw_asset_links")
    op.drop_index("ix_raw_asset_links_project_asset", table_name="raw_asset_links")
    op.drop_index("ix_raw_asset_links_project_task_status", table_name="raw_asset_links")
    op.drop_table("raw_asset_links")
    op.drop_index("uq_raw_assets_project_content_active", table_name="raw_assets")
    op.drop_index("ix_raw_assets_project_session", table_name="raw_assets")
    op.drop_index("ix_raw_assets_project_task", table_name="raw_assets")
    op.drop_index("ix_raw_assets_project_status_created_at", table_name="raw_assets")
    op.drop_table("raw_assets")
    op.drop_table("upload_parts")
    op.drop_index("ix_upload_sessions_project_asset", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_project_task_status", table_name="upload_sessions")
    op.drop_index(
        "ix_upload_sessions_project_status_expires_at",
        table_name="upload_sessions",
    )
    op.drop_table("upload_sessions")
