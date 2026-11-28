"""视频解码与输入解析的生产适配器（V6.1 视频链 T1-04 接线）。

## 职责

把两侧能力接起来，每侧只做自己擅长的事：

- 解码事实来自 ``ddp_data.media.video_decode.probe_frames``（纯只读 ffprobe
  逐帧 pts 探针，data-platform 层）；
- 对象字节来自 ``InputObjectReader``（有界流式下载，基础设施层）。

本模块是**唯一的拼接点**：探针不碰对象存储，读取器不碰 ffprobe，
应用层处理器只依赖 ``VideoDecoder`` / ``VideoInputResolver`` 两个端口。

## 红线

- **原始视频永不可变**：只下载到 Worker 临时目录，解码后即删，
  绝不回写原对象；
- **有界**：下载受 ``max_object_bytes`` 约束，帧数受 ``max_frames`` 约束；
- **不静默降级**：ffprobe 缺失、超时、输出超限一律抛
  ``StageFailure``（由处理器翻译为阶段失败），不返回空帧列表冒充成功；
- **临时文件必清理**：成功、失败、异常路径都删，避免 Worker 盘被占满。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from app.application.processing.stages import StageFailure
from app.application.processing.video_decode_stage import DecodedFrame

if TYPE_CHECKING:
    from app.application.processing.ports import InputObjectReader

logger = logging.getLogger("ddp.processing.video_decode_adapter")

#: ffprobe 逐帧读取的默认帧数上限；与处理器 ``max_frames`` 保持同一口径。
DEFAULT_MAX_FRAMES = 100_000


class FfprobeVideoDecoder:
    """用 ffprobe 逐帧读取 pts 的 ``VideoDecoder`` 实现。

    宽高/编码/像素格式/关键帧在逐帧探针里拿不到（探针只取 pts 与 media_type，
    刻意保持最小字段集），因此这些列如实留 ``None`` 而不是猜一个值——
    补全它们需要另一次流级探测，属于后续增量，不在本适配器里"顺手"混做。
    """

    def __init__(
        self,
        reader: InputObjectReader,
        *,
        max_object_bytes: int,
        max_frames: int = DEFAULT_MAX_FRAMES,
    ) -> None:
        if max_object_bytes <= 0:
            raise ValueError("max_object_bytes 必须为正整数")
        if max_frames <= 0:
            raise ValueError("max_frames 必须为正整数")
        self._reader = reader
        self._max_object_bytes = max_object_bytes
        self._max_frames = max_frames

    def decode(
        self,
        *,
        asset_id: UUID,
        storage_reference: str,
        stream_id: str,
    ) -> tuple[DecodedFrame, ...]:
        # 延迟导入：ddp_data 只在该适配器真正被使用时加载，
        # 避免纯领域/应用测试因缺少 data-platform 依赖而失败。
        from ddp_data.media.video_decode import VideoProbeError, probe_frames

        with tempfile.TemporaryDirectory(prefix="ddp-video-") as workdir:
            local = Path(workdir) / "source.bin"
            try:
                self._reader.download_to(
                    storage_reference,
                    local,
                    max_bytes=self._max_object_bytes,
                )
            except Exception as exc:  # noqa: BLE001 - 读取端口异常统一翻译为阶段失败
                raise StageFailure(
                    "video_object_unavailable",
                    f"视频对象读取失败（{asset_id}/{stream_id}）：{type(exc).__name__}",
                ) from exc

            if local.stat().st_size == 0:
                raise StageFailure(
                    "video_object_empty",
                    f"视频对象为空（{asset_id}/{stream_id}）",
                )

            try:
                frames = probe_frames(local, max_frames=self._max_frames)
            except VideoProbeError as exc:
                # 只保留错误码与脱敏后的短消息；绝对路径已在探针内脱敏。
                raise StageFailure(
                    "video_probe_failed",
                    f"逐帧探测失败（{asset_id}/{stream_id}）：{exc}",
                ) from exc

        if len(frames) > self._max_frames:
            raise StageFailure(
                "video_frame_limit_exceeded",
                f"帧数 {len(frames)} 超过上限 {self._max_frames}",
            )

        # 全部成功解码的帧记为 OK；探针本身不会返回"部分失败"，
        # 若未来支持逐帧失败，这里按 frame.decode_status 透传即可。
        return tuple(
            DecodedFrame(
                index=frame.index,
                pts_time=frame.pts_time,
                pts_time_raw=frame.pts_time_raw,
                # 容器时间基在逐帧 entries 里不取，留 None 而不是猜；
                # TIMESTAMP_NORMALIZE 在 pts_seconds 有值时走 SECONDS_IDENTITY，
                # 不依赖 time_base。
                time_base=None,
                width=None,
                height=None,
                codec=None,
                pixel_format=None,
                is_keyframe=False,
                decode_status="OK",
            )
            for frame in frames
        )


class RawAssetVideoResolver:
    """从运行的 ``input_refs``（对象 key）反查视频资产的 ``VideoInputResolver``。

    ``ProcessingRun`` 只保存 ``input_refs``（对象 key），不含资产 ID。本实现
    按 ``(project_id, object_key)`` 在 ``raw_assets`` 里查找，命中且
    ``content_type`` 以 ``video/`` 开头才认为是视频输入。

    **为什么不用 ``data_source_media_assets``**：处理运行的上游是 RawAsset
    （``raw_asset_id`` 经用例解析后落成 ``object_key``），MediaAsset 只是
    导入链的中间表；按 RawAsset 查才是唯一权威来源。
    """

    def __init__(self, session: object) -> None:
        self._session = session

    def resolve(self, run: object) -> tuple[tuple[UUID, str, str], ...]:
        from app.infrastructure.db.models.ingestion import RawAssetModel
        from sqlalchemy import select

        refs = tuple(getattr(run, "input_refs", None) or ())
        project_id = getattr(run, "project_id", None)
        if not refs or project_id is None:
            return ()

        rows = self._session.scalars(
            select(RawAssetModel)
            .where(RawAssetModel.project_id == project_id)
            .where(RawAssetModel.object_key.in_(refs))
        ).all()

        videos: list[tuple[UUID, str, str]] = []
        for row in rows:
            content_type = (row.content_type or "").lower()
            if not content_type.startswith("video/"):
                continue
            # 单相机资产：方向取内容类型尾段（video/mp4 → mp4）无法表达相机名，
            # 因此统一用 ``camera`` 列缺省值。多相机导入走 MediaAsset 链路，
            # 由后续增量扩展（此处不虚构相机名）。
            videos.append((row.id, row.object_key, "camera"))
        return tuple(videos)


__all__ = [
    "DEFAULT_MAX_FRAMES",
    "FfprobeVideoDecoder",
    "RawAssetVideoResolver",
]
