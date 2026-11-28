"""Episode 关联视频帧索引的只读 SQLAlchemy 仓储（V6.1）。

复用迁移 0087 的 ``video_frames`` 表与 ``episode_raw_assets`` 关联表：
- 所有查询强制按 ``scope.project_id`` 隔离；
- Episode 无关联原始资产或无帧索引时返回空列表与全 0 汇总，绝不造假；
- 汇总统计单次 SQL 聚合完成，不拉全表到内存也不做 N+1 查询。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeRawAssetModel
from app.infrastructure.db.models.video_frame import VideoFrameModel


@dataclass(frozen=True, slots=True)
class VideoFrameItem:
    """对外只读展示的一条视频帧索引记录。"""

    frame_index: int
    stream_id: str
    pts_seconds: Decimal | None
    timestamp_raw: str | None
    timestamp_normalized_seconds: Decimal | None
    normalize_method: str | None
    time_base: str | None
    is_keyframe: bool
    decode_status: str
    error_message: str | None


@dataclass(frozen=True, slots=True)
class VideoFrameSummary:
    """Episode 视频帧解码与时间归一化汇总。"""

    total_frames: int
    decoded_count: int
    failed_count: int
    normalized_count: int


@dataclass(frozen=True, slots=True)
class VideoFramePage:
    """Episode 视频帧索引分页结果。"""

    episode_id: UUID
    raw_asset_ids: tuple[UUID, ...]
    summary: VideoFrameSummary
    items: tuple[VideoFrameItem, ...]
    limit: int
    offset: int


class SqlVideoFrameRepository:
    """按 Episode 查询 ``video_frames`` 索引的只读仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        stream_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> VideoFramePage:
        asset_ids = tuple(
            self._session.scalars(
                select(EpisodeRawAssetModel.asset_id)
                .where(
                    EpisodeRawAssetModel.project_id == scope.project_id,
                    EpisodeRawAssetModel.episode_id == episode_id,
                )
                .distinct()
                .order_by(EpisodeRawAssetModel.asset_id)
            ).all()
        )
        if not asset_ids:
            return VideoFramePage(
                episode_id=episode_id,
                raw_asset_ids=(),
                summary=VideoFrameSummary(
                    total_frames=0,
                    decoded_count=0,
                    failed_count=0,
                    normalized_count=0,
                ),
                items=(),
                limit=limit,
                offset=offset,
            )

        conditions = [
            VideoFrameModel.project_id == scope.project_id,
            VideoFrameModel.asset_id.in_(asset_ids),
        ]
        if stream_id is not None:
            conditions.append(VideoFrameModel.stream_id == stream_id)

        summary_row = self._session.execute(
            select(
                func.count(VideoFrameModel.id),
                func.coalesce(
                    func.sum(case((VideoFrameModel.decode_status == "OK", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((VideoFrameModel.decode_status == "DECODE_ERROR", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (VideoFrameModel.timestamp_normalized_seconds.is_not(None), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(*conditions)
        ).one()

        summary = VideoFrameSummary(
            total_frames=int(summary_row[0] or 0),
            decoded_count=int(summary_row[1] or 0),
            failed_count=int(summary_row[2] or 0),
            normalized_count=int(summary_row[3] or 0),
        )

        if summary.total_frames == 0:
            return VideoFramePage(
                episode_id=episode_id,
                raw_asset_ids=asset_ids,
                summary=summary,
                items=(),
                limit=limit,
                offset=offset,
            )

        rows = self._session.scalars(
            select(VideoFrameModel)
            .where(*conditions)
            .order_by(
                VideoFrameModel.stream_id.asc(),
                VideoFrameModel.frame_index.asc(),
                VideoFrameModel.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()

        items = tuple(
            VideoFrameItem(
                frame_index=row.frame_index,
                stream_id=row.stream_id,
                pts_seconds=row.pts_seconds,
                timestamp_raw=row.timestamp_raw,
                timestamp_normalized_seconds=row.timestamp_normalized_seconds,
                normalize_method=row.normalize_method,
                time_base=row.time_base,
                is_keyframe=bool(row.is_keyframe),
                decode_status=row.decode_status,
                # 迁移 0087 未落 error_message 列，严格返回 None，不编造错误文案。
                error_message=None,
            )
            for row in rows
        )
        return VideoFramePage(
            episode_id=episode_id,
            raw_asset_ids=asset_ids,
            summary=summary,
            items=items,
            limit=limit,
            offset=offset,
        )
