"""VIDEO_DECODE 阶段处理器（V6.1 视频链）。

## 这个阶段做什么

把「一个视频资产」解码成**帧索引**：逐帧的原始时间（pts）、时间基、宽高、
编码、关键帧标记，落到 ``video_frames`` 表（迁移 0087），并把帧索引整体
作为一份产物登记（``processing_artifacts``）。

## 红线

- **原始视频永不可变**：本阶段只读视频、只写帧索引与产物登记，不改原文件；
- **保留原始时间**：``pts_seconds`` / ``timestamp_raw`` / ``time_base`` 是解码器
  原样带回的值，**不做归一化**——归一化是 ``TIMESTAMP_NORMALIZE`` 阶段的职责，
  两个阶段混做会让"原始时间"丢失（V6.1 蓝图 07 号明确要求）；
- **解码失败的帧也留痕**（``decode_status='DECODE_ERROR'``）：静默跳过会让
  "25,650 帧里少了 3 帧"这种事实不可见；
- **像素不进数据库**：帧图像是对象存储里的衍生对象，库内只存引用与 Hash。

## 为什么单独成模块

解码能力来自 ``ddp_data.media.video_decode``（data-platform 层，纯只读探针）。
本模块是**编排器适配层**：把领域产物（StageArtifact）与数据库写入
（``video_frames`` 行）接起来。两侧职责分离：探针不碰数据库，编排器不碰
ffprobe 细节。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.application.processing.stages import (
    StageArtifact,
    StageContext,
    StageOutcome,
)
from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.steps import ProcessingStepStatus
from app.infrastructure.db.models.video_frame import VideoFrameModel

logger = logging.getLogger("ddp.processing.video_decode")

#: 帧索引产物的 object key 前缀（对象存储命名空间）。
FRAME_INDEX_PREFIX = "processing/video-frames"

#: 解析器版本：随解码逻辑变化递增，写入每一行，便于事后判定"这批帧是哪版解析的"。
PARSER_VERSION = "ddp.video_decode/1.0.0"

#: 帧索引产物的 schema 版本（内容结构版本，不是数据库迁移版本）。
FRAME_INDEX_SCHEMA_VERSION = "ddp.video.frame-index/1.0.0"


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """一帧的解码事实（与 data-platform 的 FrameInfo 同形，便于映射）。"""

    index: int
    pts_time: float | None
    pts_time_raw: str | None
    time_base: str | None
    width: int | None
    height: int | None
    codec: str | None
    pixel_format: str | None
    is_keyframe: bool
    decode_status: str


class VideoInputResolver(Protocol):
    """视频输入解析端口：从运行登记的输入对象里找出视频资产。

    **为什么做成端口而不是在处理器里直接查库**：处理器本该只关心"解码与
    落帧索引"，"哪些输入是视频"涉及数据源导入域的模型关系（MediaAsset
    有 data_source / import / project 三层外键），放在处理器里会让它变成
    半个仓储。生产实现走 SQLAlchemy 查询（``SqlMediaAssetVideoResolver``），
    测试实现直接给列表——两者都遵守同一协议。
    """

    def resolve(self, run: object) -> tuple[tuple[UUID, str, str], ...]: ...


class VideoDecoder(Protocol):
    """解码器端口（生产用 ffprobe 探针，测试可注入假实现）。"""

    def decode(
        self,
        *,
        asset_id: UUID,
        storage_reference: str,
        stream_id: str,
    ) -> tuple[DecodedFrame, ...]: ...


class VideoDecodeHandler:
    """``VIDEO_DECODE`` 阶段处理器。"""

    def __init__(
        self,
        session: Session,
        decoder: VideoDecoder,
        *,
        resolver: VideoInputResolver | None = None,
        max_frames: int = 100_000,
    ) -> None:
        self._session = session
        self._decoder = decoder
        self._resolver = resolver
        self._max_frames = max_frames

    def __call__(self, context: StageContext) -> StageOutcome:
        run = context.run
        inputs = self._video_inputs(run)
        if not inputs:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="运行未登记视频输入，本阶段跳过（无视频可解码）",
            )

        artifacts: list[StageArtifact] = []
        total_frames = 0
        for asset_id, storage_reference, stream_id in inputs:
            frames = self._decoder.decode(
                asset_id=asset_id,
                storage_reference=storage_reference,
                stream_id=stream_id,
            )
            if len(frames) > self._max_frames:
                raise ValueError(
                    f"解码帧数 {len(frames)} 超过上限 {self._max_frames}："
                    "拒绝把异常大的帧索引写入库"
                )
            self._persist(context, asset_id, stream_id, frames)
            total_frames += len(frames)
            artifacts.append(
                StageArtifact(
                    kind=ProcessingArtifactKind.METADATA,
                    object_key=f"{FRAME_INDEX_PREFIX}/{asset_id}/{stream_id}.json",
                    schema_version=FRAME_INDEX_SCHEMA_VERSION,
                    content_type="application/json",
                    size_bytes=0,
                    source_ref=storage_reference,
                )
            )

        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=f"已解码 {len(inputs)} 个视频流、{total_frames} 帧索引",
            output_refs=tuple(a.object_key for a in artifacts),
        )

    def _video_inputs(self, run: object) -> tuple[tuple[UUID, str, str], ...]:
        """从运行登记的输入对象里挑出视频资产。

        默认走真实查询：``MediaAssetModel.content_type`` 以 ``video/`` 开头
        即视频（``object_key`` 是对象存储键、``camera`` 是相机名，多相机即多流）。
        注入 resolver 时以 resolver 为准（生产组合根按需替换，测试直接注入）。
        """
        if self._resolver is not None:
            return self._resolver.resolve(run)
        from app.infrastructure.db.models.datasources import MediaAssetModel

        asset_ids = tuple(getattr(run, "input_asset_ids", None) or ())
        if not asset_ids:
            return ()
        rows = self._session.query(MediaAssetModel).filter(MediaAssetModel.id.in_(asset_ids)).all()
        videos: list[tuple[UUID, str, str]] = []
        for row in rows:
            content_type = (row.content_type or "").lower()
            if not content_type.startswith("video/"):
                continue
            videos.append((row.id, row.object_key, row.camera or "default"))
        return tuple(videos)

    def _persist(
        self,
        context: StageContext,
        asset_id: UUID,
        stream_id: str,
        frames: tuple[DecodedFrame, ...],
    ) -> None:
        """把帧索引写入 ``video_frames``（同事务，随阶段产物一起提交）。"""
        now = context.now or datetime.now(UTC)
        for frame in frames:
            self._session.add(
                VideoFrameModel(
                    id=uuid4(),
                    project_id=context.scope.project_id,
                    asset_id=asset_id,
                    stream_id=stream_id,
                    frame_index=frame.index,
                    pts_seconds=frame.pts_time,
                    timestamp_raw=frame.pts_time_raw,
                    time_base=frame.time_base,
                    # 归一化列**留空**：TIMESTAMP_NORMALIZE 阶段负责填写，
                    # 此刻写值会让"原始时间"与"归一化时间"无法区分。
                    timestamp_normalized_seconds=None,
                    normalize_method=None,
                    width=frame.width,
                    height=frame.height,
                    pixel_format=frame.pixel_format,
                    codec=frame.codec,
                    is_keyframe=frame.is_keyframe,
                    decode_status=frame.decode_status,
                    frame_object_ref=None,
                    frame_hash=None,
                    parser_version=PARSER_VERSION,
                    created_at=now,
                )
            )
        self._session.flush()


__all__ = [
    "DecodedFrame",
    "VideoInputResolver",
    "FRAME_INDEX_PREFIX",
    "FRAME_INDEX_SCHEMA_VERSION",
    "PARSER_VERSION",
    "VideoDecodeHandler",
    "VideoDecoder",
]
