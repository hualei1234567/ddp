"""LeRobot / Canonical 元数据导出产物生成（确定性、无帧级映射）。

本模块生成 ``ddp.lerobot/1.0.0`` 与 ``ddp.canonical/1.0.0`` 的元数据、训练输入索引与
DDP sidecar：
- meta/info.json               权威元数据（LE_ROBOT 合并官方 v2.1 必需键与 ddp.loader_probe）
- meta/episodes.jsonl          LE_ROBOT 为官方 v2.1 schema；CANONICAL 为 DDP Episode 溯源表
- meta/ddp_episodes.jsonl      LE_ROBOT 时的 DDP Episode 溯源表（消除与官方同名文件冲突）
- meta/episodes_stats.jsonl    LE_ROBOT 逐 Episode 全数组统计（v2.1 必需）
- meta/stats.json              LE_ROBOT 聚合全数组统计（不含非法嵌套 dict）
- meta/tasks.jsonl             LE_ROBOT 任务索引
- meta/media.jsonl             每个 Episode 每一路视频的媒体位置（一个媒体一行）
- ddp/episodes.jsonl           每行一个 Episode 的 DDP 训练表（标签 + 媒体键；不混入 data/）
- meta/ddp_divisions.json      划分成员与 per_split 统计
- ddp/sidecar.json             DDP 溯源与策略快照
- data/chunk-000/episode_*.parquet  LE_ROBOT 逐 Episode 元数据 Parquet（仅含 .parquet）

诚实边界：``frame_level_mapping`` 固定 ``False``、``loadable_scope`` 固定
``METADATA_ONLY``。``LE_ROBOT`` 已在隔离环境通过官方 ``lerobot==0.3.3``
``LeRobotDataset`` 默认（``episodes=None``）与显式（``episodes=[0,1,2]``）双路构造
验证（证据见 ``docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md``），
因此 ``LE_ROBOT`` 产物标记 ``verification_status="VERIFIED_BY_LOADER"`` 与
``frame_level_read="NOT_CLAIMED_METADATA_ONLY"``；``CANONICAL`` 保持 ``UNVERIFIED``。
所有输出按规范 JSON/NDJSON 与确定性 Parquet 生成，相同输入必然得到相同文件清单与 Hash。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ddp_data.lerobot.export_writer import (
    EPISODES_PER_CHUNK,
    LEROBOT_CODEBASE_VERSION,
    NOMINAL_METADATA_FPS,
    TASK_PLACEHOLDER,
    write_lerobot_metadata,
)

from app.domain.datasets.canonical import canonical_json, plain, sha256_text, validate_sha256
from app.domain.datasets.enums import ExportFormat, ExportVerificationStatus
from app.domain.datasets.export_jobs import ExportJob
from app.domain.datasets.export_validation import coerce_format
from app.domain.datasets.models import DatasetItem, DatasetVersion

JSON_CONTENT_TYPE = "application/json"
NDJSON_CONTENT_TYPE = "application/x-ndjson"

MEDIA_INDEX_PATH = "meta/media.jsonl"
#: DDP 自有训练表迁至 ``ddp/episodes.jsonl``，净化 ``data/`` 目录使其仅含
#: LeRobot parquet 数据文件（保证 ``LeRobotDataset(..., episodes=None)`` 可直接加载）。
EPISODE_DATA_PATH = "ddp/episodes.jsonl"
#: LE_ROBOT 导出时 DDP 自有 Episode 溯源表路径（避免与官方 ``meta/episodes.jsonl`` 冲突）。
DDP_EPISODES_META_PATH = "meta/ddp_episodes.jsonl"

#: 官方 lerobot==0.3.3 Loader 隔离实测元数据（证据见 evidence_ref）。
LEROBOT_LOADER_PROBE: dict[str, str] = {
    "lerobot_version": "0.3.3",
    "codebase_version": LEROBOT_CODEBASE_VERSION,
    "loadable_scope": "METADATA_ONLY",
    "frame_level_read": "NOT_CLAIMED_METADATA_ONLY",
    "evidence_ref": "docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md",
}


def _artifact_verification_status(export_format: ExportFormat | str) -> str:
    """按格式返回产物的验证结论：LE_ROBOT 已通过 lerobot==0.3.3 结构级复测。"""
    if coerce_format(export_format) is ExportFormat.LE_ROBOT:
        return ExportVerificationStatus.VERIFIED_BY_LOADER.value
    return ExportVerificationStatus.UNVERIFIED.value


# 标签键 -> 训练列 的确定性映射。
#
# ``label_key`` 由各项目的标注 Schema 定义，不是固定枚举；这里只登记仓库里
# 真实出现过的约定键（见 ``scripts/seed/modules/annotation.py`` 的 Schema 与
# ``tests/api/dataset_support.py`` 的测试 Schema）。命中就填对应训练列，没命中
# 就留空——完整的已批准标签始终原样写进 ``labels`` 列，不丢信息、不编造。
OUTCOME_LABEL_KEYS: tuple[str, ...] = ("episode_outcome",)
ACTION_LABEL_KEYS: tuple[str, ...] = ("action_segment", "action")
QUALITY_SCORE_LABEL_KEYS: tuple[str, ...] = ("quality_score",)

_SUCCESS_TEXT = frozenset({"success", "succeeded", "true"})
_FAILURE_TEXT = frozenset({"failure", "failed", "false"})


@dataclass(frozen=True, slots=True)
class ExportArtifactSpec:
    path: str
    data: bytes
    content_type: str
    episode_refs: tuple[UUID, ...] = ()
    sidecar: bool = False
    time_range: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("产物路径非法")
        if "\\" in self.path:
            raise ValueError("产物路径不得包含反斜杠")
        object.__setattr__(self, "episode_refs", tuple(dict.fromkeys(self.episode_refs)))
        object.__setattr__(
            self,
            "time_range",
            dict(self.time_range) if self.time_range is not None else None,
        )

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        # 二进制产物（parquet）与文本产物统一按字节哈希：utf-8 文本的字节
        # 哈希与 sha256_text(text) 完全等价，既有产物 hash 值不变。
        return "sha256:" + hashlib.sha256(self.data).hexdigest()

    def to_entry(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "episode_refs": [str(item) for item in self.episode_refs],
            "time_range": self.time_range,
            "sidecar": self.sidecar,
        }


def _json_bytes(payload: Mapping[str, object] | list[object]) -> bytes:
    return canonical_json(payload).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EpisodeMediaRef:
    """Episode 引用的某一路媒体在对象存储中的位置。

    ``object_key`` 指向对象存储里的真实文件（媒体本体不进数据库、也不内联进
    导出产物）；``sha256``/``size_bytes`` 用于下载后校验内容未被替换。
    """

    episode_id: UUID
    role: str
    object_key: str
    sha256: str
    size_bytes: int
    duration_ns: int | None = None
    asset_id: UUID | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("媒体 role 不得为空")
        if not isinstance(self.object_key, str) or not self.object_key.strip():
            raise ValueError("媒体 object_key 不得为空")
        if self.object_key.startswith("/") or ".." in self.object_key.split("/"):
            raise ValueError("媒体 object_key 非法")
        validate_sha256(self.sha256, "media.sha256")
        if not isinstance(self.size_bytes, int) or self.size_bytes <= 0:
            raise ValueError("媒体 size_bytes 必须是正整数")
        if self.duration_ns is not None and self.duration_ns < 0:
            raise ValueError("媒体 duration_ns 不得为负")
        if self.content_type is not None and not self.content_type.strip():
            raise ValueError("媒体 content_type 不得为空字符串")


@dataclass(frozen=True, slots=True)
class EpisodeExportSource:
    """导出所需的单个 Episode 事实：时长、成功标记、已批准标签与媒体位置。

    ``labels`` 只来自 ``status=APPROVED`` 的标注记录，保持记录里的原始形态；
    没有批准标签时为空元组，调用方**不得**据此编造训练标签。

    标签值原样参与 RFC 8785 规范化：若某个标签值含超过 2^53 的整数（例如把
    绝对纳秒时间戳直接写成数字），规范化会拒绝，导出任务以明确错误失败——
    宁可失败，也不静默把类型改成字符串从而让产物 Hash 与源数据脱钩。
    """

    episode_id: UUID
    duration_ns: int | None = None
    success: bool | None = None
    labels: tuple[Mapping[str, object], ...] = ()
    media: tuple[EpisodeMediaRef, ...] = ()
    annotation_submission_id: UUID | None = None
    annotation_version: int | None = None

    def __post_init__(self) -> None:
        if self.duration_ns is not None and self.duration_ns < 0:
            raise ValueError("Episode duration_ns 不得为负")
        normalized: list[dict[str, object]] = []
        for label in self.labels:
            if not isinstance(label, Mapping):
                raise ValueError("标注标签必须是对象")
            normalized.append(dict(plain(label)))
        object.__setattr__(self, "labels", tuple(normalized))
        object.__setattr__(self, "media", tuple(self.media))


def _label_values(
    labels: Sequence[Mapping[str, object]],
    keys: tuple[str, ...],
) -> list[object]:
    """按键优先级取标签值；第一个命中的键即有值的键。"""
    for key in keys:
        values = [label.get("value") for label in labels if label.get("label_key") == key]
        if values:
            return values
    return []


def _single_label_value(
    labels: Sequence[Mapping[str, object]],
    keys: tuple[str, ...],
) -> tuple[bool, object]:
    """返回 ``(标签是否存在, 唯一值)``；多值/多段时唯一值为 ``None``。

    多段动作等情况下"取第一条"是编造，因此宁可留空，让调用方去读 ``labels``。
    """
    values = _label_values(labels, keys)
    if not values:
        return False, None
    distinct = {canonical_json({"value": plain(value)}) for value in values}
    if len(distinct) != 1:
        return True, None
    return True, values[0]


def _action_value(labels: Sequence[Mapping[str, object]]) -> str | None:
    _found, value = _single_label_value(labels, ACTION_LABEL_KEYS)
    return value if isinstance(value, str) and value.strip() else None


def _success_value(
    labels: Sequence[Mapping[str, object]],
    fallback: bool | None,
) -> bool | None:
    """优先用已批准标签的结论；没有该标签时退回 Episode 自身的事实列。"""
    found, value = _single_label_value(labels, OUTCOME_LABEL_KEYS)
    if not found:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _SUCCESS_TEXT:
            return True
        if normalized in _FAILURE_TEXT:
            return False
    return None


def _quality_score_value(labels: Sequence[Mapping[str, object]]) -> float | None:
    _found, value = _single_label_value(labels, QUALITY_SCORE_LABEL_KEYS)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _media_index(
    ordered: Sequence[DatasetItem],
    sources: Mapping[UUID, EpisodeExportSource],
) -> tuple[list[dict[str, object]], dict[UUID, tuple[str, ...]]]:
    """生成 ``meta/media.jsonl`` 行，并返回每个 Episode 的媒体键。

    只保留数据集条目冻结的 ``raw_asset_ids``：条目是导出范围的权威，
    Episode 上后加的关联不得混进一个已冻结版本。
    """
    rows: list[dict[str, object]] = []
    keys: dict[UUID, list[str]] = {}
    for item in ordered:
        source = sources.get(item.episode_id)
        if source is None:
            continue
        permitted = frozenset(item.raw_asset_ids)
        for ref in sorted(source.media, key=lambda value: (value.role, value.object_key)):
            if ref.episode_id != item.episode_id:
                raise ValueError("媒体引用的 Episode 与数据集条目不一致")
            if ref.asset_id is not None and ref.asset_id not in permitted:
                continue
            rows.append(
                {
                    "episode_id": str(ref.episode_id),
                    "role": ref.role,
                    "object_key": ref.object_key,
                    "content_type": ref.content_type,
                    "sha256": ref.sha256,
                    "size_bytes": ref.size_bytes,
                    "duration_ns": ref.duration_ns,
                    "asset_id": str(ref.asset_id) if ref.asset_id is not None else None,
                }
            )
            keys.setdefault(item.episode_id, []).append(ref.object_key)
    return rows, {episode_id: tuple(dict.fromkeys(values)) for episode_id, values in keys.items()}


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return "".join(canonical_json(plain(row)) + "\n" for row in rows).encode("utf-8")


def _merge_lerobot_info_keys(
    info: dict[str, object],
    episodes: Sequence[Mapping[str, object]],
) -> None:
    """把 LeRobot v2.1 必需键合并进 backend 权威 info（原地）。

    keys 与 ``data-platform/ddp_data/lerobot/export_writer.py`` 的口径一致：
    ``codebase_version`` 固定 v2.1；``features`` 只声明 episode 级列（含官方
    必需的 ``timestamp``）——逐帧模态缺失是**声明的事实**；``fps`` 使用
    ``NOMINAL_METADATA_FPS``（=1，满足官方 Loader ``fps > 0`` 数学不变量），
    并在 ``ddp`` 子对象中显式标注 ``fps_source="NOMINAL_METADATA_ONLY"``。
    """
    total_frames = len(episodes)
    episode_level_features = {
        "episode_index": {"dtype": "int64", "shape": [1]},
        "task_index": {"dtype": "int64", "shape": [1]},
        "length": {"dtype": "int64", "shape": [1]},
        "timestamp": {"dtype": "float32", "shape": [1]},
        "split": {"dtype": "string", "shape": [1]},
    }
    info.update(
        {
            "codebase_version": LEROBOT_CODEBASE_VERSION,
            "robot_type": "ddp-metadata",
            "total_episodes": len(episodes),
            "total_frames": total_frames,
            "total_tasks": 1,
            "total_videos": 0,
            "total_endorsements": 0,
            "chunks_size": EPISODES_PER_CHUNK,
            "fps": NOMINAL_METADATA_FPS,
            "splits": {
                split: f"file://{split}_chunk"
                for split in sorted({str(row.get("split", "train")) for row in episodes})
            },
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": (
                "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
            ),
            "features": episode_level_features,
            "loadable_scope": "METADATA_ONLY",
            "ddp": {
                "loadable_scope": "METADATA_ONLY",
                "verification_status": info["verification_status"],
                "fps_source": "NOMINAL_METADATA_ONLY",
                "nominal_fps": NOMINAL_METADATA_FPS,
                "episodes_metadata_path": DDP_EPISODES_META_PATH,
                "episode_data_path": EPISODE_DATA_PATH,
                "loader_probe": dict(LEROBOT_LOADER_PROBE),
            },
        }
    )


def _writer_episode_rows(
    ordered: Sequence[DatasetItem],
    training_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """把 DDP Episode 事实映射成 writer 需要的 episode 级行。

    - ``length``：固定 1，与 METADATA_ONLY 模式下每个 episode parquet 的 1 条
      元数据记录行严格一致（不伪造逐帧帧数，同时满足官方 Loader 每 episode
      ``length >= 1`` 且与 parquet 行数一致的数学不变量）；
    - ``timestamp``：固定 0.0（单条元数据记录的起始时间戳）；
    - ``task``：占位任务（方案 §3.2-A：元数据导出无真实任务语义）；
    - ``split``：来自 DatasetItem 划分。
    """
    rows: list[dict[str, object]] = []
    for item, _training in zip(ordered, training_rows, strict=True):
        rows.append(
            {
                "episode_id": str(item.episode_id),
                "episode_version": item.episode_version,
                "task": TASK_PLACEHOLDER,
                "length": 1,
                "timestamp": 0.0,
                "split": item.split.value if hasattr(item.split, "value") else str(item.split),
            }
        )
    return rows


def build_export_artifacts(
    job: ExportJob,
    version: DatasetVersion,
    items: Iterable[DatasetItem],
    *,
    created_at: datetime,
    sources: Iterable[EpisodeExportSource] = (),
) -> tuple[ExportArtifactSpec, ...]:
    """生成确定性的元数据产物；调用方负责写入对象存储并回读校验。

    ``sources`` 提供 Episode 事实与已批准标签；缺省为空，此时媒体索引为空、
    训练表里的标签列如实留空——不会用占位值冒充真实标注。
    """
    ordered = tuple(sorted(items, key=lambda item: (str(item.episode_id), item.item_sha256 or "")))
    if not ordered:
        raise ValueError("导出产物不得为空")
    source_map = {source.episode_id: source for source in sources}
    episode_refs = tuple(item.episode_id for item in ordered)
    media_rows, media_keys = _media_index(ordered, source_map)
    split_members: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    for item in ordered:
        split = item.split.value if hasattr(item.split, "value") else str(item.split)
        split_members.setdefault(split.lower(), []).append(str(item.episode_id))
    for values in split_members.values():
        values.sort()

    verification_status = _artifact_verification_status(job.format)
    info = {
        "format": job.format.value if hasattr(job.format, "value") else str(job.format),
        "format_version": job.format_version,
        "dataset_version_id": str(version.dataset_version_id),
        "dataset_version": version.dataset_version,
        "manifest_sha256": version.manifest_sha256,
        "item_count": len(ordered),
        "split_counts": {
            "train": version.train_count,
            "validation": version.validation_count,
            "test": version.test_count,
        },
        "total_duration_ns": version.total_duration_ns,
        "canonicalization": "RFC8785_JCS",
        "frame_level_mapping": False,
        "verification_status": verification_status,
        "media_index_path": MEDIA_INDEX_PATH,
        "episode_data_path": EPISODE_DATA_PATH,
        "created_at": created_at.isoformat(),
    }
    episode_rows = []
    for item in ordered:
        split = item.split.value if hasattr(item.split, "value") else str(item.split)
        episode_rows.append(
            {
                "episode_id": str(item.episode_id),
                "episode_version": item.episode_version,
                "episode_lineage_id": str(item.episode_lineage_id),
                "split": split,
                "sample_role": (
                    item.sample_role.value
                    if hasattr(item.sample_role, "value")
                    else str(item.sample_role)
                ),
                "weight": item.weight,
                "manifest_sha256": item.manifest_sha256,
                "annotation_submission_id": str(item.annotation_submission_id),
                "annotation_version": item.annotation_version,
                "schema_id": str(item.schema_id),
                "schema_version": item.schema_version,
                "item_sha256": item.item_sha256,
                "time_range": plain(item.time_range),
            }
        )
    training_rows = []
    for item in ordered:
        source = source_map.get(item.episode_id)
        labels = source.labels if source is not None else ()
        training_rows.append(
            {
                "episode_id": str(item.episode_id),
                "episode_version": item.episode_version,
                "split": item.split.value if hasattr(item.split, "value") else str(item.split),
                "action": _action_value(labels),
                "success": _success_value(
                    labels,
                    source.success if source is not None else None,
                ),
                "quality_score": _quality_score_value(labels),
                "duration_ns": source.duration_ns if source is not None else None,
                "media_keys": list(media_keys.get(item.episode_id, ())),
                "annotation_submission_id": (
                    str(source.annotation_submission_id)
                    if source is not None and source.annotation_submission_id is not None
                    else None
                ),
                "annotation_version": (source.annotation_version if source is not None else None),
                "labels": [dict(label) for label in labels],
            }
        )
    is_lerobot = coerce_format(job.format) is ExportFormat.LE_ROBOT
    writer_episodes: list[dict[str, object]] = []
    if is_lerobot:
        writer_episodes = _writer_episode_rows(ordered, training_rows)
        _merge_lerobot_info_keys(info, writer_episodes)
    per_split_stats: dict[str, dict[str, int]] = {}
    for split_name, members in sorted(split_members.items()):
        if members:
            per_split_stats[split_name] = {
                "episodes": len(members),
                "frames": len(members),
            }
    divisions = {
        "dataset_version_id": str(version.dataset_version_id),
        "divisions": split_members,
        "counts": {
            "train": len(split_members.get("train", [])),
            "validation": len(split_members.get("validation", [])),
            "test": len(split_members.get("test", [])),
        },
        "per_split": per_split_stats,
    }
    sidecar = {
        "schema_version": "ddp.export.sidecar/1.0.0",
        # 格式与版本必须出现在 sidecar 里：下游据此判断"这份产物是什么"，
        # 不能只从任务表推断（sidecar 会随产物一起流转）。
        "format": job.format.value if hasattr(job.format, "value") else str(job.format),
        "format_version": job.format_version,
        "export_job_id": str(job.export_job_id),
        "dataset_version_id": str(version.dataset_version_id),
        "dataset_version": version.dataset_version,
        "project_id": str(version.project_id),
        "manifest_sha256": version.manifest_sha256,
        "quality_report_sha256": version.quality_report_sha256,
        "leakage_report_sha256": version.leakage_report_sha256,
        "episode_version_digest": version.episode_version_digest,
        "annotation_submission_refs_digest": version.annotation_submission_refs_digest,
        "policy_refs": {
            "dedup": version.dedup_policy_ref.to_payload(),
            "split": version.split_policy_ref.to_payload(),
            "leakage": version.leakage_policy_ref.to_payload(),
            "quality": version.quality_policy_ref.to_payload(),
        },
        "exporter_id": job.exporter_id,
        "exporter_version": job.exporter_version,
        "mapping_version": job.mapping_version,
        "config_sha256": job.config_sha256,
        "frame_level_mapping": False,
        "verification_status": verification_status,
        "created_at": created_at.isoformat(),
    }
    if is_lerobot:
        sidecar["fps_source"] = "NOMINAL_METADATA_ONLY"
        sidecar["nominal_fps"] = NOMINAL_METADATA_FPS
        sidecar["per_split"] = per_split_stats
        sidecar["loader_probe"] = dict(LEROBOT_LOADER_PROBE)
    episodes_meta_path = DDP_EPISODES_META_PATH if is_lerobot else "meta/episodes.jsonl"
    artifacts = [
        ExportArtifactSpec(
            path="meta/info.json",
            data=_json_bytes(info),
            content_type=JSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
        ExportArtifactSpec(
            path=episodes_meta_path,
            data=_jsonl_bytes(episode_rows),
            content_type=NDJSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
        ExportArtifactSpec(
            path="meta/ddp_divisions.json",
            data=_json_bytes(divisions),
            content_type=JSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
        ExportArtifactSpec(
            path="ddp/sidecar.json",
            data=_json_bytes(sidecar),
            content_type=JSON_CONTENT_TYPE,
            episode_refs=episode_refs,
            sidecar=True,
        ),
        ExportArtifactSpec(
            path=MEDIA_INDEX_PATH,
            data=_jsonl_bytes(media_rows),
            content_type=NDJSON_CONTENT_TYPE,
            episode_refs=tuple(sorted(media_keys)),
        ),
        ExportArtifactSpec(
            path=EPISODE_DATA_PATH,
            data=_jsonl_bytes(training_rows),
            content_type=NDJSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
    ]

    # V31-042 切片 3：LE_ROBOT 产物升级为 v2.1 结构合规 —— 在既有 DDP 自有
    # 产物**之上**追加 LeRobot 必需文件（tasks/stats/episode parquet）。
    # 既有 NDJSON 训练表与 sidecar 是 DDP 内部消费者依赖，原样保留不删。
    # writer 只在 LE_ROBOT 时接线：CANONICAL 是平台自有格式，语义不同。
    # meta/info.json 的 LeRobot 必需键在下方 info 构建处合并（backend 权威）。
    if writer_episodes:
        written = write_lerobot_metadata(
            episodes=writer_episodes,
            dataset_version_id=str(version.dataset_version_id),
            created_at_iso=created_at.isoformat(),
        )
        artifacts.extend(
            ExportArtifactSpec(
                path=item.path,
                data=item.data,
                content_type=item.content_type,
                episode_refs=episode_refs,
            )
            for item in written
        )
    return tuple(sorted(artifacts, key=lambda artifact: artifact.path))


def aggregate_sha256(artifacts: Iterable[ExportArtifactSpec]) -> str:
    entries = [
        {
            "path": artifact.path,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "content_type": artifact.content_type,
        }
        for artifact in sorted(artifacts, key=lambda item: item.path)
    ]
    return sha256_text(canonical_json({"artifacts": entries}))


def build_artifact_manifest(
    job: ExportJob,
    artifacts: tuple[ExportArtifactSpec, ...],
    *,
    aggregate: str,
) -> dict[str, object]:
    entries = [artifact.to_entry() for artifact in artifacts]
    total_bytes = sum(artifact.size_bytes for artifact in artifacts)
    return {
        "export_job_id": str(job.export_job_id),
        "format": job.format.value if hasattr(job.format, "value") else str(job.format),
        "format_version": job.format_version,
        "exporter_version": job.exporter_version,
        "mapping_version": job.mapping_version,
        "config_sha256": job.config_sha256,
        "artifact_count": len(artifacts),
        "total_bytes": total_bytes,
        "artifacts": entries,
        "aggregate_sha256": aggregate,
        "verification_status": _artifact_verification_status(job.format),
    }


def manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return _json_bytes(plain(manifest))


def decode_json(data: bytes) -> object:
    return json.loads(data.decode("utf-8"))


__all__ = [
    "DDP_EPISODES_META_PATH",
    "EPISODE_DATA_PATH",
    "EpisodeExportSource",
    "EpisodeMediaRef",
    "ExportArtifactSpec",
    "JSON_CONTENT_TYPE",
    "MEDIA_INDEX_PATH",
    "NDJSON_CONTENT_TYPE",
    "aggregate_sha256",
    "build_artifact_manifest",
    "build_export_artifacts",
    "decode_json",
    "manifest_bytes",
]
