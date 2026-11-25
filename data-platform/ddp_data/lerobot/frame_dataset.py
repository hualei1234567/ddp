"""LeRobot v2.1 多 Episode 逐帧数据集组装与 NDJSON 桥接器（V51-011 扩展）。

将单 Episode ``write_frame_episode`` 扩展为完整多 Episode 数据集导出能力：
- 跨 Episode 全局 ``index`` 连续递增（``0..total_frames-1``），单集内 ``frame_index``
  从 ``0..length-1`` 独立计数，满足官方 ``LeRobotDataset`` 多集加载不变量；
- 自动生成 ``meta/info.json``、``meta/stats.json``（全集聚合）、``meta/episodes_stats.jsonl``
  （逐集独立行）、``meta/episodes.jsonl``、``meta/tasks.jsonl``；
- 提供 ``parse_stream_extraction_ndjson`` 直接把 Pipeline 落盘的 ``STREAM_EXTRACTION``
  NDJSON 物化产物无损还原为逐帧导出输入，消除胶水脚本重复拼装。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ddp_data.lerobot.frame_writer import (
    _build_info_json,
    build_episodes_stats_jsonl,
    build_frame_features,
    build_stats,
    build_stats_json,
    write_frame_episode,
)


@dataclass(frozen=True, slots=True)
class FrameDatasetBundle:
    """多 Episode 逐帧 LeRobot v2.1 完整数据集文件包（path -> bytes）。"""

    files: Mapping[str, bytes]
    total_episodes: int
    total_frames: int
    feature_names: tuple[str, ...]
    episode_lengths: tuple[int, ...]
    source_digest: str


def _encode_episode_rows(
    episodes: Sequence[Mapping[str, Any]],
    *,
    fps: float,
    observation_dim: int,
    action_dim: int,
    chunks_size: int,
) -> tuple[
    dict[str, bytes],
    dict[str, int],
    list[str],
    list[bytes],
    list[list[float]],
    list[list[float]],
    list[float],
    list[int],
    str,
]:
    files: dict[str, bytes] = {}
    task_to_index: dict[str, int] = {}
    episodes_jsonl_lines: list[str] = []
    episodes_stats_chunks: list[bytes] = []
    all_obs: list[list[float]] = []
    all_act: list[list[float]] = []
    all_ts: list[float] = []
    episode_lengths: list[int] = []
    digest_hasher = hashlib.sha256()
    global_offset = 0

    for ep_idx, ep_spec in enumerate(episodes):
        frames = ep_spec.get("frames")
        if not isinstance(frames, Sequence):
            raise ValueError(f"Episode {ep_idx} 缺少合法 frames 序列")
        task_name = str(ep_spec.get("task") or "ddp_task").strip() or "ddp_task"
        split_name = str(ep_spec.get("split") or "train").strip() or "train"
        task_idx = task_to_index.setdefault(task_name, len(task_to_index))

        ep_result = write_frame_episode(
            frames=frames,
            episode_index=ep_idx,
            task_index=task_idx,
            fps=fps,
            observation_dim=observation_dim,
            action_dim=action_dim,
            split=split_name,
            global_index_offset=global_offset,
        )
        chunk_idx = ep_idx // chunks_size
        files[f"data/chunk-{chunk_idx:03d}/episode_{ep_idx:06d}.parquet"] = (
            ep_result.parquet_bytes
        )

        ep_obs = [[float(x) for x in f["observation"]] for f in frames]
        ep_act = [[float(x) for x in f["action"]] for f in frames]
        ep_ts = [float(f["timestamp"]) for f in frames]
        all_obs.extend(ep_obs)
        all_act.extend(ep_act)
        all_ts.extend(ep_ts)
        episode_lengths.append(ep_result.frame_count)
        global_offset += ep_result.frame_count
        digest_hasher.update(ep_result.source_digest.encode("utf-8") + b"\n")

        ep_stats = build_stats(observations=ep_obs, actions=ep_act, timestamps=ep_ts)
        episodes_stats_chunks.append(
            build_episodes_stats_jsonl(ep_stats, episode_index=ep_idx)
        )
        episodes_jsonl_lines.append(
            json.dumps(
                {
                    "episode_index": ep_idx,
                    "tasks": [task_name],
                    "length": ep_result.frame_count,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    return (
        files,
        task_to_index,
        episodes_jsonl_lines,
        episodes_stats_chunks,
        all_obs,
        all_act,
        all_ts,
        episode_lengths,
        "sha256:" + digest_hasher.hexdigest(),
    )


def write_frame_dataset(
    *,
    episodes: Sequence[Mapping[str, Any]],
    fps: float,
    observation_dim: int,
    action_dim: int,
    chunks_size: int = 1000,
) -> FrameDatasetBundle:
    """把多个 Episode 的逐帧记录构建成确定性的 LeRobot v2.1 逐帧数据集目录字典。"""
    if not episodes:
        raise ValueError("多 Episode 逐帧导出至少需要 1 个 Episode")
    if chunks_size <= 0:
        raise ValueError("chunks_size 必须为正整数")

    (
        files,
        task_to_index,
        episodes_jsonl_lines,
        episodes_stats_chunks,
        all_obs,
        all_act,
        all_ts,
        episode_lengths,
        source_digest,
    ) = _encode_episode_rows(
        episodes,
        fps=fps,
        observation_dim=observation_dim,
        action_dim=action_dim,
        chunks_size=chunks_size,
    )

    total_frames = sum(episode_lengths)
    features = build_frame_features(
        observation_dim=observation_dim, action_dim=action_dim
    )
    files["meta/info.json"] = _build_info_json(
        features=features,
        fps=fps,
        frame_count=total_frames,
        episode_index=len(episodes) - 1,
        total_episodes=len(episodes),
        total_tasks=len(task_to_index),
        chunks_size=chunks_size,
        splits={"train": f"0:{len(episodes)}"},
    )
    agg_stats = build_stats(observations=all_obs, actions=all_act, timestamps=all_ts)
    files["meta/stats.json"] = build_stats_json(
        agg_stats, frame_count=total_frames, episode_count=len(episodes)
    )
    files["meta/episodes_stats.jsonl"] = b"".join(episodes_stats_chunks)
    files["meta/episodes.jsonl"] = ("\n".join(episodes_jsonl_lines) + "\n").encode(
        "utf-8"
    )
    tasks_lines = [
        json.dumps(
            {"task_index": idx, "task": name}, ensure_ascii=False, sort_keys=True
        )
        for name, idx in sorted(task_to_index.items(), key=lambda item: item[1])
    ]
    files["meta/tasks.jsonl"] = ("\n".join(tasks_lines) + "\n").encode("utf-8")

    return FrameDatasetBundle(
        files=files,
        total_episodes=len(episodes),
        total_frames=total_frames,
        feature_names=tuple(feature.name for feature in features),
        episode_lengths=tuple(episode_lengths),
        source_digest=source_digest,
    )


def parse_stream_extraction_ndjson(data: bytes, *, fps: float) -> list[dict[str, Any]]:
    """将 ``STREAM_EXTRACTION`` 产出的 NDJSON 字节还原为 ``write_frame_episode`` 所需的帧列表。"""
    if fps <= 0:
        raise ValueError("fps 必须为正数")
    lines = [ln.strip() for ln in data.decode("utf-8").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("STREAM_EXTRACTION NDJSON 为空")
    meta = json.loads(lines[0]).get("_meta") or {}
    if meta.get("action_source") != "PRESENT":
        raise ValueError(
            f"当前产物不含动作源（action_source={meta.get('action_source')!r}），"
            "不能导出为含 action 特征的逐帧训练集"
        )
    obs_by_idx: dict[int, list[float]] = {}
    ts_by_idx: dict[int, int | None] = {}
    act_by_idx: dict[int, list[float]] = {}
    for raw in lines[1:]:
        row = json.loads(raw)
        kind = row.get("type")
        idx = int(row["frame_index"])
        if kind == "observation":
            obs_by_idx[idx] = [float(v) for v in row["values"]]
            raw_ts = row.get("timestamp_ns")
            ts_by_idx[idx] = int(raw_ts) if raw_ts is not None else None
        elif kind == "action":
            act_by_idx[idx] = [float(v) for v in row["values"]]

    if not obs_by_idx or set(obs_by_idx) != set(act_by_idx):
        raise ValueError(
            "STREAM_EXTRACTION 产物的 observation 与 action 帧索引不对齐或为空"
        )

    ordered = sorted(obs_by_idx)
    all_have_ns = all(ts_by_idx.get(i) is not None for i in ordered)
    base_ns = int(ts_by_idx[ordered[0]]) if all_have_ns else 0  # type: ignore[arg-type]

    return [
        {
            "observation": obs_by_idx[idx],
            "action": act_by_idx[idx],
            "timestamp": ((int(ts_by_idx[idx]) - base_ns) / 1e9)
            if all_have_ns
            else (pos / fps),  # type: ignore[arg-type]
        }
        for pos, idx in enumerate(ordered)
    ]


__all__ = [
    "FrameDatasetBundle",
    "parse_stream_extraction_ndjson",
    "write_frame_dataset",
]
