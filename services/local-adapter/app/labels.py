"""Episode 级标注与 split 读取。

支持两种真实导出形态，按优先级：
1. ``data/episodes.parquet``（episode_id/split/action[/success/quality_score]）；
2. ``data/episodes.jsonl``（当前导出补全任务实际写出的形态，字段同上）；
3. 回退：``media.jsonl`` 自带 action 且 ``meta/ddp_divisions.json`` 能确定 split。
都走不通就报 BLOCKED，不用猜测标签。
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

from app.errors import BlockedError, DatasetError
from app.export_dataset import ExportLayout
from app.media import MediaManifest
from app.storage import ObjectStorage

_VALID_SPLITS = {"train", "validation", "test"}
_REQUIRED_FIELDS = ("episode_id", "split", "action")
_PARQUET_MAX_BYTES = 512 * 1024 * 1024
_JSONL_MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class EpisodeLabel:
    episode_id: str
    split: str
    action: str
    success: bool | None = None
    quality_score: float | None = None
    source: str = "parquet"


@dataclass(frozen=True, slots=True)
class LabelSet:
    labels: dict[str, EpisodeLabel]
    source: str
    skipped_rows: int


def _split(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in _VALID_SPLITS else None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_row(row: dict[str, Any], *, source: str) -> EpisodeLabel | None:
    episode_id = str(row.get("episode_id") or "").strip()
    split = _split(row.get("split"))
    action = str(row.get("action") or "").strip().lower()
    if not episode_id or split is None or not action:
        return None
    return EpisodeLabel(
        episode_id=episode_id,
        split=split,
        action=action,
        success=_bool(row.get("success")),
        quality_score=_float(row.get("quality_score")),
        source=source,
    )


def _collect(
    rows: list[dict[str, Any]], *, source: str
) -> tuple[dict[str, EpisodeLabel], int]:
    labels: dict[str, EpisodeLabel] = {}
    skipped = 0
    for row in rows:
        label = _normalize_row(row, source=source)
        if label is None:
            skipped += 1
            continue
        if label.episode_id in labels:
            raise DatasetError(
                "labels_duplicate_episode",
                f"{source} 中 episode_id 重复：{label.episode_id}",
            )
        labels[label.episode_id] = label
    return labels, skipped


def _read_parquet(
    storage: ObjectStorage, layout: ExportLayout
) -> tuple[dict[str, EpisodeLabel], int]:
    key = f"{layout.prefix}data/episodes.parquet"
    if storage.object_size(key) is None:
        return {}, 0
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise DatasetError(
            "pyarrow_missing", "读取 episodes.parquet 需要 pyarrow"
        ) from exc
    raw = storage.get_bytes(key, max_bytes=_PARQUET_MAX_BYTES)
    try:
        table = pq.read_table(io.BytesIO(raw))
    except Exception as exc:  # pyarrow 异常类型较多，统一收敛
        raise DatasetError(
            "labels_parquet_invalid", "episodes.parquet 无法解析"
        ) from exc
    missing = set(_REQUIRED_FIELDS) - set(table.column_names)
    if missing:
        raise DatasetError(
            "labels_parquet_invalid",
            "episodes.parquet 缺少列：" + "、".join(sorted(missing)),
        )
    return _collect(table.to_pylist(), source="parquet")


def _read_jsonl(
    storage: ObjectStorage, layout: ExportLayout
) -> tuple[dict[str, EpisodeLabel], int]:
    key = f"{layout.prefix}data/episodes.jsonl"
    if storage.object_size(key) is None:
        return {}, 0
    raw = storage.get_bytes(key, max_bytes=_JSONL_MAX_BYTES)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DatasetError("labels_jsonl_invalid", "episodes.jsonl 不是 UTF-8") from exc
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(
                "labels_jsonl_invalid", f"episodes.jsonl 第 {index} 行不是合法 JSON"
            ) from exc
        if not isinstance(row, dict):
            raise DatasetError(
                "labels_jsonl_invalid", f"episodes.jsonl 第 {index} 行不是对象"
            )
        rows.append(row)
    return _collect(rows, source="jsonl")


def _division_splits(layout: ExportLayout) -> dict[str, str]:
    divisions = layout.divisions.get("divisions")
    if not isinstance(divisions, dict):
        return {}
    result: dict[str, str] = {}
    for name, values in divisions.items():
        split = _split(name)
        if split is None or not isinstance(values, list):
            continue
        for value in values:
            result[str(value)] = split
    return result


def load_episode_labels(
    storage: ObjectStorage,
    layout: ExportLayout,
    media: MediaManifest,
    *,
    required: bool = True,
) -> LabelSet:
    for reader, source in ((_read_parquet, "parquet"), (_read_jsonl, "jsonl")):
        labels, skipped = reader(storage, layout)
        if labels:
            return LabelSet(labels=labels, source=source, skipped_rows=skipped)
    split_map = _division_splits(layout)
    fallback: dict[str, EpisodeLabel] = {}
    for entry in media.entries:
        split = split_map.get(entry.episode_id)
        if not entry.action or split is None:
            continue
        fallback.setdefault(
            entry.episode_id,
            EpisodeLabel(
                episode_id=entry.episode_id,
                split=split,
                action=entry.action.strip().lower(),
                source="media+divisions",
            ),
        )
    if fallback:
        return LabelSet(labels=fallback, source="media+divisions", skipped_rows=0)
    if not required:
        return LabelSet(labels={}, source="none", skipped_rows=0)
    raise BlockedError(
        "blocked_labels_missing",
        "导出产物缺少 data/episodes.parquet / data/episodes.jsonl，"
        "media.jsonl 也未提供 action；无法取得监督标签",
    )


__all__ = ["EpisodeLabel", "LabelSet", "load_episode_labels"]
