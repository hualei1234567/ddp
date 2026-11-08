"""Dataset、DatasetVersion 与 DatasetItem ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.datasets.enums import (
    DatasetPurpose,
    DatasetStatus,
    DatasetUsagePolicy,
    DatasetVersionStatus,
    LeakageStatus,
    SampleRole,
    Split,
    ValidityStatus,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


def _sha_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(_SHA.format(column=column), name=name)


class DatasetAuditMixin:
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
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
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)


class DatasetModel(Base, DatasetAuditMixin):
    __tablename__ = "datasets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    dataset_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    usage_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    retention_policy: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    default_dedup_policy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    default_split_policy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    default_leakage_policy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    frozen_version_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_datasets_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_datasets_project_code"),
        CheckConstraint(f"purpose IN ({_values(DatasetPurpose)})", name="purpose_allowed"),
        CheckConstraint(f"status IN ({_values(DatasetStatus)})", name="status_allowed"),
        CheckConstraint(
            f"usage_policy IN ({_values(DatasetUsagePolicy)})",
            name="usage_policy_allowed",
        ),
        CheckConstraint("frozen_version_count >= 0", name="frozen_count_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status <> 'ARCHIVED' OR (archived_at IS NOT NULL AND archive_reason IS NOT NULL)",
            name="archive_requires_metadata",
        ),
        Index("ix_datasets_project_status_created_at", "project_id", "status", "created_at"),
        Index("ix_datasets_project_lineage", "project_id", "dataset_lineage_id"),
    )


class DatasetVersionModel(Base, DatasetAuditMixin):
    __tablename__ = "dataset_versions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    dataset_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validity_status: Mapped[str] = mapped_column(String(16), nullable=False)
    selection_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_filters: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    selection_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedup_policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    dedup_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dedup_policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    split_policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    split_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    split_policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    leakage_policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    leakage_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    leakage_policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    quality_policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    quality_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    train_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    test_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_duration_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    episode_version_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    annotation_schema_versions: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    annotation_submission_refs_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    manifest_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    quality_report_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    quality_report_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    leakage_report_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    leakage_report_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    leakage_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    build_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    freeze_approval_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    frozen_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    superseded_by_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    blocking_issues: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    warnings: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    exclusion_summary: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_versions_project_id_id"),
        UniqueConstraint(
            "project_id",
            "dataset_id",
            "dataset_version",
            name="uq_dataset_versions_project_dataset_version",
        ),
        UniqueConstraint(
            "project_id",
            "dataset_lineage_id",
            "dataset_version",
            name="uq_dataset_versions_project_lineage_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_id"],
            ["datasets.project_id", "datasets.id"],
            name="fk_dataset_versions_project_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_dataset_versions_project_supersedes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "superseded_by_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_dataset_versions_project_superseded_by",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "frozen_by"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_dataset_versions_project_frozen_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(DatasetVersionStatus)})", name="status_allowed"),
        CheckConstraint(
            f"validity_status IN ({_values(ValidityStatus)})",
            name="validity_status_allowed",
        ),
        CheckConstraint(
            f"leakage_status IS NULL OR leakage_status IN ({_values(LeakageStatus)})",
            name="leakage_status_allowed",
        ),
        CheckConstraint("dataset_version >= 1", name="dataset_version_positive"),
        CheckConstraint("build_attempt >= 0", name="build_attempt_non_negative"),
        *(
            CheckConstraint(f"{column} >= 0", name=f"{column}_non_negative")
            for column in (
                "candidate_count",
                "selected_count",
                "excluded_count",
                "duplicated_count",
                "item_count",
                "train_count",
                "validation_count",
                "test_count",
            )
        ),
        CheckConstraint(
            "item_count = train_count + validation_count + test_count",
            name="item_count_consistent",
        ),
        _sha_check("dedup_policy_sha256", "dedup_policy_sha256_format"),
        _sha_check("split_policy_sha256", "split_policy_sha256_format"),
        _sha_check("leakage_policy_sha256", "leakage_policy_sha256_format"),
        _sha_check("quality_policy_sha256", "quality_policy_sha256_format"),
        CheckConstraint(
            "manifest_sha256 IS NULL OR ("
            "length(manifest_sha256) = 71 AND substr(manifest_sha256, 1, 7) = 'sha256:')",
            name="manifest_sha256_format",
        ),
        CheckConstraint(
            "quality_report_sha256 IS NULL OR ("
            "length(quality_report_sha256) = 71 AND substr(quality_report_sha256, 1, 7) = 'sha256:')",
            name="quality_report_sha256_format",
        ),
        CheckConstraint(
            "leakage_report_sha256 IS NULL OR ("
            "length(leakage_report_sha256) = 71 AND substr(leakage_report_sha256, 1, 7) = 'sha256:')",
            name="leakage_report_sha256_format",
        ),
        CheckConstraint(
            "status NOT IN ('READY', 'FROZEN') OR ("
            "manifest_sha256 IS NOT NULL AND quality_report_sha256 IS NOT NULL "
            "AND train_count >= 1 AND validation_count >= 1)",
            name="ready_requires_reports_and_splits",
        ),
        CheckConstraint(
            "status <> 'FROZEN' OR ("
            "leakage_report_sha256 IS NOT NULL AND leakage_status IS NOT NULL "
            "AND freeze_approval_id IS NOT NULL AND frozen_by IS NOT NULL "
            "AND frozen_at IS NOT NULL)",
            name="frozen_requires_approval_and_leakage",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_dataset_versions_project_dataset_status",
            "project_id",
            "dataset_id",
            "status",
        ),
        Index(
            "ix_dataset_versions_project_lineage",
            "project_id",
            "dataset_lineage_id",
            "dataset_version",
        ),
        Index(
            "ix_dataset_versions_project_validity",
            "project_id",
            "validity_status",
            "status",
        ),
    )


class DatasetItemModel(Base):
    __tablename__ = "dataset_items"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_version: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    raw_asset_ids: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    annotation_submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    annotation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    annotation_record_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    schema_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    stratum: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sample_role: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=text("1.0"))
    dedup_group_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    similarity_group_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    scene_group_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    selected_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    item_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    time_range: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_items_project_id_id"),
        UniqueConstraint(
            "project_id",
            "dataset_version_id",
            "episode_id",
            name="uq_dataset_items_project_version_episode",
        ),
        UniqueConstraint(
            "project_id",
            "dataset_version_id",
            "item_sha256",
            name="uq_dataset_items_project_version_sha",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_dataset_items_project_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_dataset_items_project_episode",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"split IN ({_values(Split)})", name="split_allowed"),
        CheckConstraint(f"sample_role IN ({_values(SampleRole)})", name="sample_role_allowed"),
        CheckConstraint("episode_version >= 1", name="episode_version_positive"),
        CheckConstraint("annotation_version >= 1", name="annotation_version_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("weight > 0 AND weight <= 1000", name="weight_range"),
        CheckConstraint("length(selected_reason) >= 1", name="selected_reason_non_empty"),
        _sha_check("manifest_sha256", "manifest_sha256_format"),
        _sha_check("annotation_record_digest", "annotation_record_digest_format"),
        _sha_check("schema_sha256", "schema_sha256_format"),
        _sha_check("item_sha256", "item_sha256_format"),
        Index(
            "ix_dataset_items_project_version_split",
            "project_id",
            "dataset_version_id",
            "split",
        ),
        Index(
            "ix_dataset_items_project_episode",
            "project_id",
            "episode_id",
        ),
    )
