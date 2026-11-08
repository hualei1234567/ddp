"""数据集泄漏报告与质量报告 ORM 模型（只追加）。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
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

from app.domain.datasets.enums import LeakageStatus, QualityReportStatus
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


def _sha_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(_SHA.format(column=column), name=name)


class DatasetLeakageReportModel(Base):
    __tablename__ = "dataset_leakage_reports"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    leakage_status: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    checked_rule_count: Mapped[int] = mapped_column(Integer, nullable=False)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hard_violation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    soft_violation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    violations: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    waivers: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    unknown_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    report_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_dataset_leakage_reports_project_id_id",
        ),
        UniqueConstraint(
            "project_id",
            "dataset_version_id",
            "report_sha256",
            name="uq_dataset_leakage_reports_project_version_sha",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_dataset_leakage_reports_project_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(QualityReportStatus)})", name="status_allowed"),
        CheckConstraint(
            f"leakage_status IN ({_values(LeakageStatus)})",
            name="leakage_status_allowed",
        ),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("checked_rule_count >= 1", name="checked_rule_count_positive"),
        CheckConstraint("violation_count >= 0", name="violation_count_non_negative"),
        CheckConstraint(
            "hard_violation_count >= 0 AND soft_violation_count >= 0",
            name="violation_counts_non_negative",
        ),
        CheckConstraint(
            "violation_count = hard_violation_count + soft_violation_count",
            name="violation_count_consistent",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        _sha_check("policy_sha256", "policy_sha256_format"),
        _sha_check("report_sha256", "report_sha256_format"),
        Index(
            "ix_dataset_leakage_reports_project_version_checked",
            "project_id",
            "dataset_version_id",
            "checked_at",
        ),
    )


class DatasetQualityReportModel(Base):
    __tablename__ = "dataset_quality_reports"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    quality_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    split_counts: Mapped[dict[str, int]] = mapped_column(_JSON_TYPE, nullable=False)
    distributions: Mapped[dict[str, dict[str, int]]] = mapped_column(_JSON_TYPE, nullable=False)
    total_duration_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coverage: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    dedup_summary: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    leakage_summary: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    blocking_issues: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    source_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(100), nullable=False)
    report_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    report_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_dataset_quality_reports_project_id_id",
        ),
        UniqueConstraint(
            "project_id",
            "dataset_version_id",
            "report_sha256",
            name="uq_dataset_quality_reports_project_version_sha",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_dataset_quality_reports_project_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(QualityReportStatus)})", name="status_allowed"),
        CheckConstraint("quality_policy_version >= 1", name="policy_version_positive"),
        CheckConstraint(
            "(candidate_count >= 0) AND (selected_count >= 0) AND "
            "(excluded_count >= 0) AND (duplicated_count >= 0)",
            name="counts_non_negative",
        ),
        CheckConstraint("total_duration_ns >= 0", name="duration_non_negative"),
        CheckConstraint("total_bytes >= 0", name="bytes_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        _sha_check("quality_policy_sha256", "policy_sha256_format"),
        _sha_check("report_sha256", "report_sha256_format"),
        Index(
            "ix_dataset_quality_reports_project_version_created",
            "project_id",
            "dataset_version_id",
            "created_at",
        ),
    )
