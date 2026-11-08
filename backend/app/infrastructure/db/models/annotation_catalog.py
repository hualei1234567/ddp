"""阶段 3 标签 Schema、样本集与 Episode 引用 ORM 模型。

所有业务表强制 ``project_id``；父引用使用 ``(project_id, id)`` 复合外键。
状态列使用 ``varchar + CHECK``，迁移与 ``create_all`` 同时安装触发器兜底。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
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

from app.domain.annotation.enums import (
    AnnotationSampleSetStatus,
    AnnotationSchemaStatus,
    DataSource,
    DedupKey,
    SampleRole,
    SampleStrategy,
    SampleTimeDomain,
    SchemaCompatibility,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class AnnotationAuditMixin:
    """标注域公共字段：项目隔离、乐观锁与审计。"""

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
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
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)


class AnnotationSchema(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_schemas"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schema_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    annotation_levels: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    labels: Mapped[list[dict[str, object]]] = mapped_column(_JSON_TYPE, nullable=False)
    compatibility: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_schemas_project_id_id"),
        UniqueConstraint(
            "project_id",
            "schema_lineage_id",
            "schema_version",
            name="uq_annotation_schemas_project_lineage_version",
        ),
        UniqueConstraint(
            "project_id",
            "code",
            "schema_version",
            name="uq_annotation_schemas_project_code_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_schemas_project_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationSchemaStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            f"compatibility IN ({_values(SchemaCompatibility)})",
            name="compatibility_allowed",
        ),
        CheckConstraint(_SHA_CHECK.format(column="schema_sha256"), name="schema_sha256_format"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_schemas_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_annotation_schemas_project_lineage",
            "project_id",
            "schema_lineage_id",
        ),
    )


class AnnotationSampleSet(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_sample_sets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    filters: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    strata: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    dedup_key: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    deduplicated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)
    exclusion_summary: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_sample_sets_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_annotation_sample_sets_project_code"),
        CheckConstraint(
            f"status IN ({_values(AnnotationSampleSetStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            f"strategy IN ({_values(SampleStrategy)})",
            name="strategy_allowed",
        ),
        CheckConstraint(
            f"dedup_key IN ({_values(DedupKey)})",
            name="dedup_key_allowed",
        ),
        CheckConstraint(
            f"data_source IN ({_values(DataSource)})",
            name="data_source_allowed",
        ),
        CheckConstraint("candidate_count >= 0", name="candidate_count_non_negative"),
        CheckConstraint("selected_count >= 0", name="selected_count_non_negative"),
        CheckConstraint("excluded_count >= 0", name="excluded_count_non_negative"),
        CheckConstraint("deduplicated_count >= 0", name="deduplicated_count_non_negative"),
        CheckConstraint(
            "status NOT IN ('DRAFT', 'PREVIEW') OR snapshot_sha256 IS NULL",
            name="snapshot_hash_only_after_preview",
        ),
        CheckConstraint(
            "snapshot_sha256 IS NULL OR (" + _SHA_CHECK.format(column="snapshot_sha256") + ")",
            name="snapshot_sha256_format",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_sample_sets_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
    )


class AnnotationSampleEpisode(Base):
    __tablename__ = "annotation_sample_episodes"

    ref_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    sample_set_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_version: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time_ns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_time_ns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_domain: Mapped[str] = mapped_column(String(16), nullable=False)
    stratum: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sample_role: Mapped[str] = mapped_column(String(16), nullable=False)
    selected_reason: Mapped[str] = mapped_column(String(500), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "sample_set_id",
            "episode_id",
            name="uq_annotation_sample_episodes_set_episode",
        ),
        ForeignKeyConstraint(
            ["project_id", "sample_set_id"],
            ["annotation_sample_sets.project_id", "annotation_sample_sets.id"],
            name="fk_annotation_sample_episodes_project_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_annotation_sample_episodes_project_episode",
            ondelete="RESTRICT",
        ),
        CheckConstraint("episode_version >= 1", name="episode_version_positive"),
        CheckConstraint(
            f"sample_role IN ({_values(SampleRole)})",
            name="sample_role_allowed",
        ),
        CheckConstraint(
            f"time_domain IN ({_values(SampleTimeDomain)})",
            name="time_domain_allowed",
        ),
        CheckConstraint(
            "(start_time_ns IS NULL AND end_time_ns IS NULL) OR ("
            "start_time_ns IS NOT NULL AND end_time_ns IS NOT NULL "
            "AND start_time_ns < end_time_ns)",
            name="time_range_valid",
        ),
        CheckConstraint("length(selected_reason) >= 1", name="selected_reason_non_empty"),
        Index(
            "ix_annotation_sample_episodes_project_set",
            "project_id",
            "sample_set_id",
        ),
        Index(
            "ix_annotation_sample_episodes_project_episode",
            "project_id",
            "episode_id",
        ),
    )
