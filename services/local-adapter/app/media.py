"""``meta/media.jsonl`` 解析。

该文件由导出链路并行补充，字段命名尚未写进稳定契约。解析器接受常见别名，
但只在能明确定位到「视频对象 + episode」时采信；缺失时返回 present=False，
由上层如实报 BLOCKED，绝不编造训练数据。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.errors import BlockedError, DatasetError
from app.export_dataset import ExportLayout
from app.storage import ObjectStorage

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EPISODE_KEYS = ("episode_id", "episode", "episode_ref", "episode_uuid")
_OBJECT_KEYS = ("object_key", "key", "s3_key", "path", "video_path", "uri")
_SHA_KEYS = ("sha256", "sha_256", "hash")
_CAMERA_KEYS = ("camera", "view", "sensor", "camera_id", "stream")
_ACTION_KEYS = ("action", "label", "action_label")
_NESTED_LISTS = ("videos", "media", "camera_views", "streams", "files")


@dataclass(frozen=True, slots=True)
class MediaEntry:
    episode_id: str
    object_key: str
    sha256: str | None = None
    camera: str | None = None
    size_bytes: int | None = None
    action: str | None = None


@dataclass(frozen=True, slots=True)
class MediaManifest:
    present: bool
    entries: tuple[MediaEntry, ...]
    malformed_rows: int

    @property
    def episodes(self) -> set[str]:
        return {entry.episode_id for entry in self.entries}


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _normalize_sha(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.startswith("sha256=") or text.startswith("sha256:"):
        text = text[7:]
    if not text:
        return None
    if not _SHA256.fullmatch(text):
        raise DatasetError(
            "media_sha256_invalid", f"media.jsonl 中 sha256 格式非法：{value!r}"
        )
    return text


def _normalize_object_key(value: Any, *, bucket: str) -> str:
    text = str(value).strip()
    if text.startswith("s3://"):
        without_scheme = text[5:]
        remote_bucket, _, key = without_scheme.partition("/")
        if remote_bucket != bucket:
            raise DatasetError(
                "media_bucket_mismatch",
                f"media.jsonl 引用了其他 bucket：{remote_bucket}（本服务 bucket={bucket}）",
            )
        text = key
    text = text.lstrip("/")
    if not text or ".." in text.split("/"):
        raise DatasetError(
            "media_object_key_invalid", f"media.jsonl 中 object_key 非法：{value!r}"
        )
    return text


def _row_entries(
    row: dict[str, Any],
    *,
    bucket: str,
    parent_episode: str | None = None,
    parent_action: str | None = None,
) -> list[MediaEntry]:
    episode = _first(row, _EPISODE_KEYS) or parent_episode
    action = _first(row, _ACTION_KEYS) or parent_action
    nested: list[dict[str, Any]] = []
    for key in _NESTED_LISTS:
        value = row.get(key)
        if isinstance(value, list):
            nested.extend(item for item in value if isinstance(item, dict))
    entries: list[MediaEntry] = []
    if nested:
        for item in nested:
            entries.extend(
                _row_entries(
                    item,
                    bucket=bucket,
                    parent_episode=str(episode) if episode else parent_episode,
                    parent_action=str(action) if action else parent_action,
                )
            )
        if entries:
            return entries
    object_key = _first(row, _OBJECT_KEYS)
    if object_key is None or not episode:
        return []
    size = row.get("size_bytes", row.get("size"))
    return [
        MediaEntry(
            episode_id=str(episode),
            object_key=_normalize_object_key(object_key, bucket=bucket),
            sha256=_normalize_sha(_first(row, _SHA_KEYS)),
            camera=(
                str(_first(row, _CAMERA_KEYS))
                if _first(row, _CAMERA_KEYS) is not None
                else None
            ),
            size_bytes=int(size)
            if isinstance(size, int) and not isinstance(size, bool)
            else None,
            action=str(action) if action is not None else None,
        )
    ]


def ensure_media_available(media: MediaManifest) -> None:
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


def load_media_manifest(
    storage: ObjectStorage,
    layout: ExportLayout,
    *,
    max_bytes: int,
) -> MediaManifest:
    key = f"{layout.prefix}meta/media.jsonl"
    if storage.object_size(key) is None:
        return MediaManifest(present=False, entries=(), malformed_rows=0)
    raw = storage.get_bytes(key, max_bytes=max_bytes)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DatasetError("media_manifest_invalid", "media.jsonl 不是 UTF-8") from exc
    entries: list[MediaEntry] = []
    seen: set[tuple[str, str, str | None]] = set()
    malformed = 0
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue
        try:
            row_items = _row_entries(row, bucket=storage.bucket)
        except DatasetError:
            raise
        except (TypeError, ValueError) as exc:
            raise DatasetError(
                "media_manifest_invalid", f"media.jsonl 第 {index} 行字段非法：{exc}"
            ) from exc
        if not row_items:
            malformed += 1
            continue
        for entry in row_items:
            signature = (entry.episode_id, entry.object_key, entry.camera)
            if signature not in seen:
                seen.add(signature)
                entries.append(entry)
    return MediaManifest(present=True, entries=tuple(entries), malformed_rows=malformed)


__all__ = [
    "MediaEntry",
    "MediaManifest",
    "ensure_media_available",
    "load_media_manifest",
]
