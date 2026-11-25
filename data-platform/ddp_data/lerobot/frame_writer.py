"""LeRobot v2.1 **逐帧**导出 writer（V51-011）。

**与 ``export_writer.py`` 的关系（重要）**

``export_writer.py`` 是 **METADATA_ONLY** writer：每个 Episode 只有 1 条元数据行，
``features`` 只有 5 项（episode_index/task_index/length/timestamp/split），
**显式不含** ``observation.*`` 与 ``action``——因为 DDP 当时确实没有逐帧存储，
写入逐帧特征就是虚构数据。

本模块是**并行的另一条路**：当 DDP 真的有逐帧物化产物（STREAM_EXTRACTION 产出的
Observation / Action）时，把它们写成官方 Loader 可逐帧读取的 Parquet。

**两条路必须共存，不能互相替换**：
- 没有逐帧数据 → 只能 METADATA_ONLY（``loadable_scope=METADATA_ONLY``）
- 有逐帧数据 → 可产出 ``LOADER_FRAME`` 级产物
把 METADATA_ONLY 改名成 FRAME 就是谎报（见 MEM-REQ-001）。

**逐帧数据从哪来**

不直接读 HDF5（那会让 writer 依赖具体格式），而是接收**已物化的帧记录**：
``frames: list[{observation: {...}, action: {...}, timestamp: float}]``。
职责分离：格式解析归 STREAM_EXTRACTION，序列化归本模块。

**官方 LeRobot v2.1 逐帧 Parquet 的关键约束**
- 必须含 ``observation.*`` 与 ``action`` 列（这是"逐帧"的定义）
- 必须有 ``timestamp``（float32，秒）
- ``episode_index`` / ``frame_index`` / ``index`` 三元组必须自洽
- ``info.json.features`` 必须与 Parquet 列完全对应
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: 逐帧导出的 Schema 版本。改变输出结构必须升版本。
FRAME_SCHEMA_VERSION = "ddp.lerobot.frame-export/1.0.0"

#: 逐帧产物信息文档（供 sidecar 与验收读取）。
FRAME_SCOPE = "LOADER_FRAME"


@dataclass(frozen=True, slots=True)
class FrameFeature:
    """一个逐帧 feature 的声明（对应 LeRobot ``features`` 条目）。"""

    name: str
    dtype: str
    shape: tuple[int, ...]

    def as_info(self) -> dict[str, object]:
        return {"dtype": self.dtype, "shape": list(self.shape), "names": None}


@dataclass(frozen=True, slots=True)
class FrameExportResult:
    """逐帧导出的产物集合。"""

    parquet_bytes: bytes
    info_json: bytes
    feature_names: tuple[str, ...]
    frame_count: int
    episode_index: int
    #: 数据源摘要，供 lineage 与验收核对
    source_digest: str


def build_frame_features(
    *,
    observation_dim: int,
    action_dim: int,
) -> tuple[FrameFeature, ...]:
    """构建逐帧 feature 声明。

    官方 LeRobot 逐帧数据集至少有 ``observation.state`` 与 ``action``；
    图像类 feature 需要真实视频，本模块不做（``ph`` 样本无图像）。
    """
    if observation_dim <= 0 or action_dim <= 0:
        raise ValueError("observation_dim 与 action_dim 必须为正")
    return (
        FrameFeature("observation.state", "float32", (observation_dim,)),
        FrameFeature("action", "float32", (action_dim,)),
        FrameFeature("timestamp", "float32", (1,)),
    )


def write_frame_episode(
    *,
    frames: Sequence[Mapping[str, Any]],
    episode_index: int,
    task_index: int = 0,
    fps: float,
    observation_dim: int,
    action_dim: int,
    split: str = "train",
) -> FrameExportResult:
    """把逐帧记录写成 LeRobot v2.1 逐帧 Parquet + info.json。

    ``frames`` 每项：``{"observation": [...], "action": [...], "timestamp": float}``。

    **严格校验**：维度不符、时间戳非单调、帧数为空都直接失败——
    产出一个「看起来像逐帧但维度错位」的数据集，比失败更糟（训练时才会暴露）。
    """
    if not frames:
        raise ValueError("逐帧导出至少需要 1 帧（空数据集不能标 FRAME_LEVEL）")
    if fps <= 0:
        raise ValueError("fps 必须为正（官方 Loader 的 check_timestamps_sync 依赖它）")
    if episode_index < 0 or task_index < 0:
        raise ValueError("episode_index / task_index 必须非负")

    observations: list[list[float]] = []
    actions: list[list[float]] = []
    timestamps: list[float] = []
    for position, frame in enumerate(frames):
        obs = _vector(
            frame.get("observation"), observation_dim, "observation", position
        )
        act = _vector(frame.get("action"), action_dim, "action", position)
        ts = frame.get("timestamp")
        if not isinstance(ts, int | float):
            raise ValueError(
                f"第 {position} 帧 timestamp 必须是数字，得到 {type(ts).__name__}"
            )
        observations.append(obs)
        actions.append(act)
        timestamps.append(float(ts))

    # 时间戳必须严格单调递增：官方 Loader 会校验，且非单调意味着数据错位。
    for position in range(1, len(timestamps)):
        if timestamps[position] <= timestamps[position - 1]:
            raise ValueError(
                f"时间戳必须严格递增：第 {position} 帧 {timestamps[position]} "
                f"<= 第 {position - 1} 帧 {timestamps[position - 1]}"
            )

    features = build_frame_features(
        observation_dim=observation_dim, action_dim=action_dim
    )
    parquet = _write_parquet(
        episode_index=episode_index,
        task_index=task_index,
        split=split,
        observations=observations,
        actions=actions,
        timestamps=timestamps,
        observation_dim=observation_dim,
        action_dim=action_dim,
    )
    info = _build_info_json(
        features=features,
        fps=fps,
        frame_count=len(timestamps),
        episode_index=episode_index,
    )
    return FrameExportResult(
        parquet_bytes=parquet,
        info_json=info,
        feature_names=tuple(feature.name for feature in features),
        frame_count=len(timestamps),
        episode_index=episode_index,
        source_digest=_digest(observations, actions, timestamps),
    )


def _vector(value: object, expected_dim: int, label: str, position: int) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"第 {position} 帧 {label} 必须是数值序列")
    if len(value) != expected_dim:
        raise ValueError(
            f"第 {position} 帧 {label} 维度不符：期望 {expected_dim}，得到 {len(value)}"
        )
    result: list[float] = []
    for item in value:
        if not isinstance(item, int | float) or isinstance(item, bool):
            raise ValueError(f"第 {position} 帧 {label} 含非数值元素：{item!r}")
        result.append(float(item))
    return result


def _write_parquet(
    *,
    episode_index: int,
    task_index: int,
    split: str,
    observations: list[list[float]],
    actions: list[list[float]],
    timestamps: list[float],
    observation_dim: int,
    action_dim: int,
) -> bytes:
    """用 pyarrow 写逐帧 Parquet（固定写参数以保证字节确定）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    frame_count = len(timestamps)
    arrays: dict[str, Any] = {
        "episode_index": pa.array([episode_index] * frame_count, type=pa.int64()),
        "frame_index": pa.array(list(range(frame_count)), type=pa.int64()),
        "index": pa.array(list(range(frame_count)), type=pa.int64()),
        "task_index": pa.array([task_index] * frame_count, type=pa.int64()),
        "timestamp": pa.array(timestamps, type=pa.float32()),
        "split": pa.array([split] * frame_count, type=pa.string()),
    }
    for dim in range(observation_dim):
        arrays[f"observation.state[{dim}]"] = pa.array(
            [row[dim] for row in observations], type=pa.float32()
        )
    for dim in range(action_dim):
        arrays[f"action[{dim}]"] = pa.array(
            [row[dim] for row in actions], type=pa.float32()
        )

    table = pa.table(arrays)
    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression="snappy",
        version="2.6",
        write_statistics=True,
        use_dictionary=False,
        data_page_size=1 << 20,
    )
    return buffer.getvalue()


def _build_info_json(
    *,
    features: Sequence[FrameFeature],
    fps: float,
    frame_count: int,
    episode_index: int,
) -> bytes:
    """构建逐帧数据集的 info.json。

    **与 METADATA_ONLY 的关键区别**：
    - ``total_frames`` 是真实帧数（不是 Episode 数）
    - ``fps`` 是真实采样率（不是标称 1）
    - ``features`` 含 ``observation.*`` 与 ``action``
    - ``frame_level_mapping`` = True（这是可宣称逐帧的依据）
    """
    payload = {
        "codebase_version": "v2.1",
        "robot_type": "ddp_import",
        "total_episodes": 1,
        "total_frames": frame_count,
        "total_tasks": 1,
        "fps": fps,
        # 官方 Loader 会读 chunks_size 计算 chunk 目录编号；缺失直接 KeyError。
        "chunks_size": 1000,
        # 官方要求**模板形式**（含 {episode_chunk:03d}/{episode_index:06d}），
        # 写死具体路径会让 Loader 无法推导其它 Episode 的位置。
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        # 官方 v2.1 要求 video_path 是**模板串**（即使本数据集无视频）：
        # 传 None 会让 Loader 在建 split 时失败。这是实测踩到的。
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "created_at": "1970-01-01T00:00:00+00:00",
        "splits": {"train": f"0:{episode_index + 1}"},
        "features": {feature.name: feature.as_info() for feature in features},
        "ddp": {
            "schema_version": FRAME_SCHEMA_VERSION,
            "loadable_scope": FRAME_SCOPE,
            "frame_level_mapping": True,
            "video_provided": False,
            "fps_source": "MEASURED_FROM_FRAMES",
            "episode_index": episode_index,
        },
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_stats(
    *,
    observations: Sequence[Sequence[float]],
    actions: Sequence[Sequence[float]],
    timestamps: Sequence[float],
) -> dict[str, dict[str, float | int]]:
    """生成官方 v2.1 的 ``stats.json`` 内容。

    **为什么必须生成**：官方 Loader 读取时会尝试加载 ``meta/stats.json``；
    缺失会导致构造失败。这不是可选装饰，是元数据契约的一部分。

    每个 feature 给 min/max/mean/std/count——与官方计算口径一致。
    """
    stats: dict[str, dict[str, float | int]] = {}

    def _one(column: Sequence[float]) -> dict[str, float | int]:
        values = [float(v) for v in column]
        if not values:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "count": 0}
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return {
            "min": min(values),
            "max": max(values),
            "mean": mean,
            "std": variance**0.5,
            "count": len(values),
        }

    stats["timestamp"] = _one(timestamps)
    for dim in range(len(observations[0])):
        stats[f"observation.state[{dim}]"] = _one([row[dim] for row in observations])
    for dim in range(len(actions[0])):
        stats[f"action[{dim}]"] = _one([row[dim] for row in actions])
    return stats


def build_stats_json(
    stats: Mapping[str, Mapping[str, float | int]],
    *,
    frame_count: int,
    episode_count: int = 1,
) -> bytes:
    payload = {
        "stats": {name: dict(values) for name, values in stats.items()},
        "num_frames": frame_count,
        "num_episodes": episode_count,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_episodes_stats_jsonl(
    stats: Mapping[str, Mapping[str, float | int]],
    *,
    episode_index: int,
) -> bytes:
    """官方 ``meta/episodes_stats.jsonl``：每个 Episode 一行，值是**数组**形式。"""
    row = {
        "episode_index": episode_index,
        "stats": {
            name: {
                "min": [values["min"]],
                "max": [values["max"]],
                "mean": [values["mean"]],
                "std": [values["std"]],
                "count": [values["count"]],
            }
            for name, values in stats.items()
        },
    }
    return (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _digest(
    observations: list[list[float]], actions: list[list[float]], timestamps: list[float]
) -> str:
    hasher = hashlib.sha256()
    for obs, act, ts in zip(observations, actions, timestamps, strict=True):
        hasher.update(json.dumps([obs, act, ts], sort_keys=True).encode("utf-8"))
        hasher.update(b"\n")
    return "sha256:" + hasher.hexdigest()


__all__ = [
    "FRAME_SCHEMA_VERSION",
    "FRAME_SCOPE",
    "FrameExportResult",
    "FrameFeature",
    "build_episodes_stats_jsonl",
    "build_frame_features",
    "build_stats",
    "build_stats_json",
    "write_frame_episode",
]
