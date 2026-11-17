"""把导出产物中的视频转成帧级特征样本。

训练 / 评测 / 推理共用同一条特征化路径，保证三者输入分布一致。任何一路视频
下载失败、sha256 不符或抽不出帧都会让整个任务失败并上报错误码，不静默跳过。
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import frames
from app.config import Settings
from app.errors import AdapterError, BlockedError, DatasetError
from app.export_dataset import ExportLayout
from app.labels import EpisodeLabel
from app.media import MediaManifest
from app.storage import ObjectStorage

logger = logging.getLogger("ddp.local_adapter.features")

CancelCheck = Callable[[], None]


@dataclass(slots=True)
class FeatureBatch:
    features: list[bytes] = field(default_factory=list)
    labels: list[str | None] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    cameras: list[str | None] = field(default_factory=list)
    frame_indices: list[int] = field(default_factory=list)
    timestamps: list[float | None] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.features)


def _safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def collect_frame_samples(
    *,
    storage: ObjectStorage,
    settings: Settings,
    layout: ExportLayout,
    media: MediaManifest,
    labels: dict[str, EpisodeLabel],
    episode_ids: set[str] | None,
    split: str | None,
    require_labels: bool,
    check_cancel: CancelCheck,
) -> FeatureBatch:
    if not media.present:
        raise BlockedError(
            "blocked_media_manifest_missing",
            "导出产物缺少 meta/media.jsonl，无法定位视频；不生成任何模型/预测结果",
        )
    if not media.entries:
        raise BlockedError(
            "blocked_media_manifest_empty",
            "meta/media.jsonl 存在但没有可用的 episode+object_key 行",
        )

    selected = []
    skipped_no_label = 0
    for entry in media.entries:
        if episode_ids is not None and entry.episode_id not in episode_ids:
            continue
        label = labels.get(entry.episode_id)
        if require_labels and label is None:
            skipped_no_label += 1
            continue
        if split is not None and (label is None or label.split != split):
            continue
        selected.append((entry, label))
    if not selected:
        code = "blocked_no_media_for_split" if split else "blocked_no_media_episodes"
        summary = (
            f"没有同时具备视频与标注、且 split={split} 的 episode"
            if split
            else "没有同时具备视频与标注的 episode"
        )
        raise BlockedError(code, summary)

    batch = FeatureBatch()
    sampling: dict[str, int] = {}
    videos = 0
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="ddp-local-adapter-") as workdir:
        for index, (entry, label) in enumerate(selected):
            check_cancel()
            target = Path(workdir) / f"{index:05d}-{_safe_name(entry.object_key)}.video"
            downloaded = storage.download_to_file(
                entry.object_key,
                target,
                max_bytes=settings.max_video_bytes,
            )
            total_bytes += downloaded.size
            if entry.sha256 is not None and downloaded.sha256 != entry.sha256:
                raise DatasetError(
                    "media_sha256_mismatch",
                    f"视频 sha256 与 media.jsonl 不一致：{entry.object_key}",
                )
            extracted = frames.extract_frames(
                downloaded.path,
                ffmpeg_path=settings.ffmpeg_path,
                ffprobe_path=settings.ffprobe_path,
                count=settings.frames_per_video,
                size=settings.image_size,
                timeout=settings.ffmpeg_timeout_seconds,
            )
            videos += 1
            sampling[extracted.sampling] = sampling.get(extracted.sampling, 0) + 1
            for frame_index, payload in enumerate(extracted.frames):
                if len(batch.features) >= settings.max_frames:
                    raise BlockedError(
                        "frame_budget_exceeded",
                        f"抽取帧数超过上限 {settings.max_frames}，拒绝在截断数据上训练",
                    )
                batch.features.append(payload)
                batch.labels.append(label.action if label is not None else None)
                batch.episode_ids.append(entry.episode_id)
                batch.cameras.append(entry.camera)
                batch.frame_indices.append(frame_index)
                batch.timestamps.append(
                    extracted.duration_seconds
                    * (frame_index + 0.5)
                    / len(extracted.frames)
                    if extracted.duration_seconds is not None and extracted.frames
                    else None
                )
    batch.stats = {
        "video_count": videos,
        "episode_count": len({item[0].episode_id for item in selected}),
        "frame_count": len(batch.features),
        "video_bytes": total_bytes,
        "sampling": sampling,
        "skipped_no_label": skipped_no_label,
        "media_malformed_rows": media.malformed_rows,
    }
    logger.info("特征化完成 %s", batch.stats)
    return batch


def ensure_same_feature_size(batch: FeatureBatch, size: int) -> None:
    expected = size * size
    for payload in batch.features:
        if len(payload) != expected:
            raise AdapterError(
                "feature_size_mismatch",
                f"帧字节数 {len(payload)} 与模型期望的 {expected} 不一致",
            )


__all__ = ["FeatureBatch", "collect_frame_samples", "ensure_same_feature_size"]
