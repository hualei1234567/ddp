"""数据集去重、划分、泄漏与质量策略 ORM 模型。

策略按版本追加，不提供更新/删除；内容 Hash 由服务端计算。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
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
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.datasets.enums import (
    EmptySplitPolicy,
    NearDuplicateEnforcement,
    RetainRule,
    RoundingRule,
    SplitStrategy,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA = "length(policy_sha256) = 71 AND substr(policy_sha256, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class DatasetPolicyBase:
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)


class DatasetDedupPolicyModel(Base, DatasetPolicyBase):
    __tablename__ = "dataset_dedup_policies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    exact_keys: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    near_duplicate_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )
    near_duplicate_enforcement: Mapped[str] = mapped_column(String(16), nullable=False)
    similarity_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    similarity_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    similarity_min_cluster_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grouping_keys: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    retain_rule: Mapped[str] = mapped_column(String(24), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_dedup_policies_project_id_id"),
        UniqueConstraint(
            "project_id",
            "dataset_id",
            "version",
            name="uq_dataset_dedup_policies_project_dataset_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_id"],
            ["datasets.project_id", "datasets.id"],
            name="fk_dataset_dedup_policies_project_dataset",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(_SHA, name="policy_sha256_format"),
        CheckConstraint(
            "near_duplicate_enforcement IN "
            f"({_values(NearDuplicateEnforcement)})",
            name="near_duplicate_enforcement_allowed",
        ),
        CheckConstraint(f"retain_rule IN ({_values(RetainRule)})", name="retain_rule_allowed"),
        CheckConstraint(
            "similarity_threshold IS NULL OR "
            "(similarity_threshold >= 0 AND similarity_threshold <= 1)",
            name="similarity_threshold_range",
        ),
        CheckConstraint(
            "similarity_min_cluster_size IS NULL OR similarity_min_cluster_size >= 2",
            name="similarity_cluster_size",
        ),
        Index(
            "ix_dataset_dedup_policies_project_dataset",
            "project_id",
            "dataset_id",
            "version",
        ),
    )


class DatasetSplitPolicyModel(Base, DatasetPolicyBase):
    __tablename__ = "dataset_split_policies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    strategy: Mapped[str] = mapped_column(String(16), nullable=False)
    train_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    validation_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    test_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    train_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    stratify_by: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    group_by: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    empty_split_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    min_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_duration_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    rounding_rule: Mapped[str] = mapped_column(String(24), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_split_policies_project_id_id"),
        UniqueConstraint(
            "project_id",
            "dataset_id",
            "version",
            name="uq_dataset_split_policies_project_dataset_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_id"],
            ["datasets.project_id", "datasets.id"],
            name="fk_dataset_split_policies_project_dataset",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(_SHA, name="policy_sha256_format"),
        CheckConstraint(f"strategy IN ({_values(SplitStrategy)})", name="strategy_allowed"),
        CheckConstraint(
            f"empty_split_policy IN ({_values(EmptySplitPolicy)})",
            name="empty_split_policy_allowed",
        ),
        CheckConstraint(f"rounding_rule IN ({_values(RoundingRule)})", name="rounding_rule_allowed"),
        CheckConstraint("seed >= 0", name="seed_non_negative"),
        CheckConstraint("min_item_count >= 1", name="min_item_count_positive"),
        CheckConstraint("min_duration_ns >= 0", name="min_duration_non_negative"),
        CheckConstraint(
            "train_ratio >= 0 AND train_ratio <= 1 AND "
            "validation_ratio >= 0 AND validation_ratio <= 1 AND "
            "test_ratio >= 0 AND test_ratio <= 1",
            name="ratios_range",
        ),
        Index(
            "ix_dataset_split_policies_project_dataset",
            "project_id",
            "dataset_id",
            "version",
        ),
    )


class DatasetLeakagePolicyModel(Base, DatasetPolicyBase):
    __tablename__ = "dataset_leakage_policies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    hard_rules: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    soft_rules: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    waiver_allowed_rules: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    similarity_grouping_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )
    scene_grouping_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    batch_grouping_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )
    temporal_overlap_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_leakage_policies_project_id_id"),
        UniqueConstraint(
            "project_id",
            "dataset_id",
            "version",
            name="uq_dataset_leakage_policies_project_dataset_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_id"],
            ["datasets.project_id", "datasets.id"],
            name="fk_dataset_leakage_policies_project_dataset",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(_SHA, name="policy_sha256_format"),
        Index(
            "ix_dataset_leakage_policies_project_dataset",
            "project_id",
            "dataset_id",
            "version",
        ),
    )


class DatasetQualityPolicyModel(Base, DatasetPolicyBase):
    __tablename__ = "dataset_quality_policies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    min_selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allow_warn_quality: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )
    require_leakage_pass: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dataset_quality_policies_project_id_id"),
        UniqueConstraint(
            "project_id",
            "dataset_id",
            "version",
            name="uq_dataset_quality_policies_project_dataset_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_id"],
            ["datasets.project_id", "datasets.id"],
            name="fk_dataset_quality_policies_project_dataset",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(_SHA, name="policy_sha256_format"),
        CheckConstraint("min_selected_count >= 1", name="min_selected_count_positive"),
        Index(
            "ix_dataset_quality_policies_project_dataset",
            "project_id",
            "dataset_id",
            "version",
        ),
    )
