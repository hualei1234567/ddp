"""Episode、Episode-RawAsset 与血缘 ORM 模型。"""

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
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.episodes.enums import (
    EpisodeAnnotationStatus,
    EpisodeQualityStatus,
    EpisodeStatus,
    LineageNodeType,
    LineageRelation,
)
from app.infrastructure.db.base import Base

_STATUS_VALUES = ", ".join(repr(item.value) for item in EpisodeStatus)
_QUALITY_VALUES = ", ".join(repr(item.value) for item in EpisodeQualityStatus)
_ANNOTATION_VALUES = ", ".join(repr(item.value) for item in EpisodeAnnotationStatus)
_NODE_TYPE_VALUES = ", ".join(repr(item.value) for item in LineageNodeType)
_RELATION_VALUES = ", ".join(repr(item.value) for item in LineageRelation)
_MANIFEST_SHA_CHECK = "length(manifest_sha256) = 71 AND substr(manifest_sha256, 1, 7) = 'sha256:'"
_SHA256_CHECK = "length(sha256_snapshot) = 71 AND substr(sha256_snapshot, 1, 7) = 'sha256:'"
_READY_STATUSES = "'READY', 'QUALITY_CHECKING', 'ACCEPTED', 'COMPLETED', 'ARCHIVED'"
_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class EpisodeModel(Base):
    __tablename__ = "episodes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    episode_lineage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    episode_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    robot_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # N9-008：产出该 Episode 的采集会话；可空（历史行无归属），同项目由复合外键强制。
    capture_session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    device_ids: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    sensor_ids: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=EpisodeQualityStatus.NOT_CHECKED.value,
        server_default=text("'NOT_CHECKED'"),
    )
    annotation_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=EpisodeAnnotationStatus.NOT_STARTED.value,
        server_default=text("'NOT_STARTED'"),
    )
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    scene_labels: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    language_instruction: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # G6 语义近似去重的文本指纹（迁移 0084）：``language_instruction`` 的
    # ``sha256:`` 规范化指纹。历史行由迁移一次性回填；该列只做**精确重复**
    # 快速判定与 B-tree 索引，近似判定仍由 pg_trgm/领域纯函数完成。
    # 可空是兼容口径：未填指令的 Episode 本来就不进去重语料。
    text_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    start_time_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_time_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_domain: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="UTC",
        server_default=text("'UTC'"),
    )
    time_base: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    canonical_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    segmentation_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    frame_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    calibration_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    manifest_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    # 最近一次自动就绪判定的拒绝原因（[{"code","message","detail"}]）。
    # 空数组 + PENDING = 尚未尝试自动就绪；非空 = 尝试过且被明确拒绝。
    readiness_blockers: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    preview_refs: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
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

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_episodes_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_episodes_project_code"),
        UniqueConstraint(
            "project_id",
            "episode_lineage_id",
            "episode_version",
            name="uq_episodes_project_lineage_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_episodes_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_episodes_project_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "capture_session_id"],
            ["capture_sessions.project_id", "capture_sessions.id"],
            name="fk_episodes_project_capture_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_episodes_project_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint(f"quality_status IN ({_QUALITY_VALUES})", name="quality_status_allowed"),
        CheckConstraint(
            f"annotation_status IN ({_ANNOTATION_VALUES})",
            name="annotation_status_allowed",
        ),
        CheckConstraint("time_domain = 'UTC'", name="time_domain_utc"),
        CheckConstraint("start_time_ns < end_time_ns", name="time_range_valid"),
        CheckConstraint("duration_ns = end_time_ns - start_time_ns", name="duration_consistent"),
        CheckConstraint("episode_version >= 1", name="episode_version_positive"),
        CheckConstraint("frame_count >= 0", name="frame_count_non_negative"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempt <= max_attempts", name="attempt_within_max"),
        CheckConstraint(
            "manifest_sha256 IS NULL OR (" + _MANIFEST_SHA_CHECK + ")",
            name="manifest_sha256_format",
        ),
        CheckConstraint(
            f"status NOT IN ({_READY_STATUSES}) OR ("
            "manifest_key IS NOT NULL AND manifest_sha256 IS NOT NULL)",
            name="ready_requires_manifest",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_episodes_project_status_created_at", "project_id", "status", "created_at"),
        Index("ix_episodes_project_task", "project_id", "task_id"),
        Index("ix_episodes_project_batch", "project_id", "batch_id"),
        Index("ix_episodes_project_capture_session", "project_id", "capture_session_id"),
        Index("ix_episodes_project_lineage", "project_id", "episode_lineage_id"),
        Index(
            "ix_episodes_project_quality_status",
            "project_id",
            "quality_status",
            "status",
        ),
        Index(
            "uq_episodes_supersedes",
            "project_id",
            "supersedes_id",
            unique=True,
            postgresql_where=text("supersedes_id IS NOT NULL"),
            sqlite_where=text("supersedes_id IS NOT NULL"),
        ),
    )


class EpisodeRawAssetModel(Base):
    __tablename__ = "episode_raw_assets"

    project_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    episode_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    start_time_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    end_time_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256_snapshot: Mapped[str] = mapped_column(String(71), nullable=False)
    size_bytes_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "episode_id"],
            ["episodes.project_id", "episodes.id"],
            name="fk_episode_raw_assets_project_episode",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "asset_id"],
            ["raw_assets.project_id", "raw_assets.id"],
            name="fk_episode_raw_assets_project_asset",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(role) >= 1", name="role_non_empty"),
        CheckConstraint(_SHA256_CHECK, name="sha256_snapshot_format"),
        CheckConstraint("size_bytes_snapshot > 0", name="size_snapshot_positive"),
        CheckConstraint(
            "(start_time_ns IS NULL AND end_time_ns IS NULL) OR ("
            "start_time_ns IS NOT NULL AND end_time_ns IS NOT NULL "
            "AND start_time_ns < end_time_ns)",
            name="time_range_valid",
        ),
        Index("ix_episode_raw_assets_project_asset", "project_id", "asset_id"),
        Index("ix_episode_raw_assets_project_episode", "project_id", "episode_id"),
    )


class LineageNodeRefModel(Base):
    __tablename__ = "lineage_node_refs"

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    node_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(f"node_type IN ({_NODE_TYPE_VALUES})", name="node_type_allowed"),
        Index(
            "ix_lineage_node_refs_project_active",
            "project_id",
            "is_active",
            "node_type",
        ),
        Index("ix_lineage_node_refs_project_node", "project_id", "node_id"),
    )


class DataLineageEdgeModel(Base):
    __tablename__ = "data_lineage_edges"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_node_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    to_node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    to_node_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    edge_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "from_node_type",
            "from_node_id",
            "to_node_type",
            "to_node_id",
            "relation",
            name="uq_data_lineage_edges_endpoints_relation",
        ),
        ForeignKeyConstraint(
            ["project_id", "from_node_type", "from_node_id"],
            [
                "lineage_node_refs.project_id",
                "lineage_node_refs.node_type",
                "lineage_node_refs.node_id",
            ],
            name="fk_data_lineage_edges_project_from_node",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "to_node_type", "to_node_id"],
            [
                "lineage_node_refs.project_id",
                "lineage_node_refs.node_type",
                "lineage_node_refs.node_id",
            ],
            name="fk_data_lineage_edges_project_to_node",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"from_node_type IN ({_NODE_TYPE_VALUES})", name="from_node_type_allowed"),
        CheckConstraint(f"to_node_type IN ({_NODE_TYPE_VALUES})", name="to_node_type_allowed"),
        CheckConstraint(f"relation IN ({_RELATION_VALUES})", name="relation_allowed"),
        CheckConstraint(
            "NOT (from_node_type = to_node_type AND from_node_id = to_node_id)",
            name="no_self_edge",
        ),
        Index(
            "ix_data_lineage_edges_project_from",
            "project_id",
            "from_node_type",
            "from_node_id",
        ),
        Index(
            "ix_data_lineage_edges_project_to",
            "project_id",
            "to_node_type",
            "to_node_id",
        ),
        Index(
            "ix_data_lineage_edges_project_relation_created_at",
            "project_id",
            "relation",
            "created_at",
        ),
    )
