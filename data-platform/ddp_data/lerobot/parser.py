"""把 Human-1M 预览元数据映射为 DDP 的 Canonical Episode / MediaAsset。

映射原则（`docs/ddp/07` §4）：

- **保留原始**：``raw`` 字段原样留存，不因映射而丢弃来源信息；
- **不凭空补齐**：元数据没有的（机器人位姿、关节角）就留空，
  不能因为 Canonical 模型有该字段就编一个默认值；
- **一行一相机**：Human-1M 的 metadata.jsonl 是"视频文件级"平铺结构，
  同一 ``example_id`` 有多行（main / right / stereo / wrist）；
  按 ``example_id`` 聚合成一个 Episode，每行成为一个 MediaAsset。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Mapping

# Human-1M 的 view 取值 → 机位标识。未知取值保留原文归一成 slug，
# 因为将来可能有新机位；用白名单会让新数据静默丢失。
_VIEW_CODES = {
    "Egocentric main": "main",
    "Egocentric right": "right",
    "Stereo side by side": "stereo",
    "Left wrist": "wrist_left",
    "Right wrist": "wrist_right",
}

_TASK_MAX = 200
_INSTRUCTION_MAX = 2000


class LeRobotParseError(Exception):
    """元数据不符合预期结构；调用方应记为导入失败并保留原因。"""


@dataclass(frozen=True, slots=True)
class CanonicalMediaAsset:
    """一个相机的一段视频。"""

    path: str
    camera: str
    view_raw: str
    modality: str
    size_bytes_hint: int | None = None
    sha256: str | None = None
    excerpt_start_seconds: float | None = None
    excerpt_seconds: float | None = None
    source_fps: float | None = None


@dataclass(frozen=True, slots=True)
class CanonicalEpisode:
    """一个演示片段（同一 example_id 的全部机位）。"""

    episode_key: str
    task_name: str
    instruction: str
    environment: str | None
    setting: str | None
    episode_seconds: float | None
    source_fps: float | None
    has_wrist: bool
    media_assets: tuple[CanonicalMediaAsset, ...]
    raw: Mapping[str, object] = field(default_factory=dict)


def _as_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise LeRobotParseError(f"{field_name} 不能是布尔值")
    if isinstance(value, (int, float)):
        number = float(value)
        if number < 0:
            raise LeRobotParseError(f"{field_name} 不能为负：{number}")
        return number
    raise LeRobotParseError(
        f"{field_name} 必须是数字或 null，得到 {type(value).__name__}"
    )


def _as_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LeRobotParseError(f"{field_name} 必须是非空字符串")
    text = value.strip()
    if len(text) > max_length:
        raise LeRobotParseError(f"{field_name} 超过 {max_length} 字符")
    return text


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LeRobotParseError(f"{field_name} 必须是字符串或 null")
    return value.strip() or None


def _camera_of(view_raw: str) -> str:
    if view_raw in _VIEW_CODES:
        return _VIEW_CODES[view_raw]
    return view_raw.strip().lower().replace(" ", "_")


def parse_preview_metadata(
    rows: list[Mapping[str, object]],
) -> tuple[CanonicalEpisode, ...]:
    """把 metadata.jsonl 的行聚合成 Episode 序列（按首次出现顺序）。"""
    if not rows:
        raise LeRobotParseError("元数据为空")

    grouped: OrderedDict[str, list[Mapping[str, object]]] = OrderedDict()
    for index, row in enumerate(rows):
        key = row.get("example_id")
        if not isinstance(key, str) or not key.strip():
            raise LeRobotParseError(f"第 {index + 1} 行缺少 example_id")
        if not isinstance(row.get("file_name"), str):
            raise LeRobotParseError(f"第 {index + 1} 行缺少 file_name")
        grouped.setdefault(key.strip(), []).append(row)

    episodes: list[CanonicalEpisode] = []
    for key, items in grouped.items():
        first = items[0]
        assets: list[CanonicalMediaAsset] = []
        seen_paths: set[str] = set()
        for row in items:
            path = str(row["file_name"]).strip()
            if path in seen_paths:
                # 同一 demo 同一机位重复出现会导致审看时出现重复项。
                raise LeRobotParseError(f"{key} 的同一视频重复出现：{path}")
            seen_paths.add(path)
            view_raw = row.get("view") if isinstance(row.get("view"), str) else ""
            assets.append(
                CanonicalMediaAsset(
                    path=path,
                    camera=_camera_of(view_raw),
                    view_raw=view_raw,
                    modality=str(row.get("modality") or "unknown"),
                    excerpt_start_seconds=_as_float(
                        row.get("excerpt_start_seconds"), "excerpt_start_seconds"
                    ),
                    excerpt_seconds=_as_float(
                        row.get("excerpt_seconds"), "excerpt_seconds"
                    ),
                    source_fps=_as_float(row.get("source_fps"), "source_fps"),
                )
            )

        episodes.append(
            CanonicalEpisode(
                episode_key=key,
                task_name=_as_text(
                    first.get("task_name"), "task_name", max_length=_TASK_MAX
                ),
                instruction=_as_text(
                    first.get("instruction"), "instruction", max_length=_INSTRUCTION_MAX
                ),
                environment=_optional_text(first.get("environment"), "environment"),
                setting=_optional_text(first.get("setting"), "setting"),
                episode_seconds=_as_float(
                    first.get("episode_seconds"), "episode_seconds"
                ),
                source_fps=_as_float(first.get("source_fps"), "source_fps"),
                has_wrist=bool(first.get("has_wrist", False)),
                media_assets=tuple(assets),
                raw=dict(first),
            )
        )
    return tuple(episodes)
