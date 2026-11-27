"""视频帧索引 ORM（V6.1）。列与迁移 0087 一一对应。

**红线**：原始视频永不可变（RawAsset 不可变）。本表只存帧索引与校验信息，
像素本体在对象存储（``frame_object_ref``），数据库绝不大对象。
``pts_seconds`` / ``timestamp_raw`` / ``time_base`` 是解码器**原样**带回的
原始时间；``timestamp_normalized_seconds`` 是衍生列（未做时为 NULL，
``normalize_method='NONE'``），两者永不混写。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class VideoFrameModel(Base):
    """一帧已解码视频的索引行（像素在对象存储）。"""

    __tablename__ = "video_frames"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    #: 原始视频资产（RawAsset / MediaAsset 语义上的上层引用——帧是衍生品）
    asset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    #: 流标识（多相机场景一资产多流：camera_front / camera_wrist …）
    stream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 解码器原样时间（**原始**，永不改写）
    pts_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 9), nullable=True)
    timestamp_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_base: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 归一化时间（**衍生**）：TIMESTAMP_NORMALIZE 阶段填写，未做时 NULL
    timestamp_normalized_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 9), nullable=True
    )
    normalize_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pixel_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_keyframe: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    #: OK / DECODE_ERROR / SKIPPED —— 失败帧也留痕，不静默跳过
    decode_status: Mapped[str] = mapped_column(String(16), nullable=False)
    frame_object_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    frame_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "decode_status IN ('OK', 'DECODE_ERROR', 'SKIPPED')",
            name="ck_video_frames_decode_status",
        ),
        CheckConstraint("frame_index >= 0", name="ck_video_frames_frame_index_non_negative"),
        Index("ix_video_frames_asset_stream_frame", "asset_id", "stream_id", "frame_index"),
        Index("ix_video_frames_project_created_at", "project_id", "created_at"),
    )
