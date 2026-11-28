"""GATE-A 视频 Loader 端到端验证（V61-008）。

**验证什么**：DDP 导出的 LeRobot v2.1 数据集**含视频 feature** 时，
官方 ``lerobot==0.3.3`` 的 ``LeRobotDataset`` 能加载并**真实解码视频帧**。

**与 GATE-B 的关系**（蓝图红线 9）：GATE-A 是「官方 Loader 读得动」，
GATE-B 是「目标训练脚本消费得了」——两个独立状态，分别验收。

**数据来源**：lerobot/pusht 官方缓存（真实机器人推物数据，Apache-2.0 系）——
取其视频前 100 帧（10 秒）与配套 state/action 构造 v2.1 数据集。
合成规模：100 帧（pusht 全量 25,650 帧不需要，GATE-A 只验证解码链路）。

**诚实边界**：pusht 是公开数据集（B 级样本），只证明软件集成，
不替代硬件 Pilot（A 级）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data-platform"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from ddp_data.lerobot.frame_writer import (  # noqa: E402
    FrameFeature,
    build_stats,
    build_stats_json,
    write_frame_episode,
)

PUSHT_BLOB = Path.home() / (
    ".cache/huggingface/hub/datasets--lerobot--pusht/blobs/"
    "f58d11857651dad5983c021b56a50a68a6ce19068834c1fb5cac099219fb3a78"
)
PUSHT_PARQUET = Path.home() / (
    ".cache/huggingface/hub/datasets--lerobot--pusht/blobs/"
    "9abc0431f12d10c33b5b37b1bf1e6e557c8ae1c8c29fb86658969e48fcbbbf01"
)
#: 取前 100 帧（10fps × 10s）——GATE-A 只验证解码链路，不需要全量。
FRAMES = 100
FPS = 10.0
VIDEO_KEY = "observation.image"


def _cut_video(dest: Path) -> dict[str, object]:
    """ffmpeg 截取前 100 帧为独立 mp4（流复制会保留全部帧，须重编码）。"""
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(PUSHT_BLOB),
        "-frames:v",
        str(FRAMES),
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 截取失败：{proc.stderr[-400:]}")
    return {"path": str(dest), "size_bytes": dest.stat().st_size}


def _state_action() -> tuple[list[list[float]], list[list[float]], list[float]]:
    """从 pusht parquet 读前 100 帧的 state/action/timestamp。"""
    import pyarrow.parquet as pq

    table = pq.read_table(
        PUSHT_PARQUET, columns=["observation.state", "action", "timestamp"]
    )
    states = [
        list(row) for row in table.column("observation.state").to_pylist()[:FRAMES]
    ]
    actions = [list(row) for row in table.column("action").to_pylist()[:FRAMES]]
    stamps = [float(v) for v in table.column("timestamp").to_pylist()[:FRAMES]]
    return states, actions, stamps


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ddp-gate-a-video-"))
    print(f"数据集根：{root}")

    # ---- 1. 截取视频 ----
    video_info = _cut_video(root / "source.mp4")
    print(f"视频截取：{video_info}")

    # ---- 2. state/action（真实 pusht 数据）----
    states, actions, stamps = _state_action()
    print(
        f"state/action：{len(states)} 帧，obs {len(states[0])} 维，act {len(actions[0])} 维"
    )

    # ---- 3. 写 v2.1 数据集（video feature 声明）----
    video_feature = FrameFeature(
        VIDEO_KEY, "video", (96, 96, 3), names=("height", "width", "channels")
    )
    frames = [
        {"observation": states[i], "action": actions[i], "timestamp": stamps[i]}
        for i in range(len(states))
    ]
    result = write_frame_episode(
        frames=frames,
        episode_index=0,
        fps=FPS,
        observation_dim=len(states[0]),
        action_dim=len(actions[0]),
        video_features=(video_feature,),
    )
    (root / "data" / "chunk-000").mkdir(parents=True)
    (root / "meta").mkdir(parents=True)
    (root / "data" / "chunk-000" / "episode_000000.parquet").write_bytes(
        result.parquet_bytes
    )
    (root / "meta" / "info.json").write_bytes(result.info_json)
    stats = build_stats(observations=states, actions=actions, timestamps=stamps)
    # 视频特征 stats：官方要求 (3,1,1)（实测 pusht 官方 stats.json：
    # min=[[[0.0]],[[0.0]],[[0.2039]]]）。占位零值即可——GATE-A 验证解码链路，
    # 不验证统计精度；但形状必须严格合规，否则 aggregate_stats 拒绝加载。
    stats[VIDEO_KEY] = {
        "min": [[[0.0]], [[0.0]], [[0.0]]],
        "max": [[[1.0]], [[1.0]], [[1.0]]],
        "mean": [[[0.5]], [[0.5]], [[0.5]]],
        "std": [[[0.1]], [[0.1]], [[0.1]]],
        "count": [len(states)],
    }
    (root / "meta" / "stats.json").write_bytes(
        build_stats_json(stats, frame_count=len(frames))
    )
    (root / "meta" / "episodes.jsonl").write_text(
        json.dumps({"episode_index": 0, "tasks": ["pusht"], "length": len(frames)})
        + "\n"
    )
    (root / "meta" / "tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "pusht"}) + "\n"
    )
    # episodes_stats.jsonl：沿用 frame_writer 的 _wrap 规则（标量包一层、list 原样）——
    # 直接 dumps 会让逐维标量变 0 维，官方 aggregate_stats 拒绝（实测踩到）。
    from ddp_data.lerobot.frame_writer import build_episodes_stats_jsonl

    (root / "meta" / "episodes_stats.jsonl").write_bytes(
        build_episodes_stats_jsonl(stats, episode_index=0)
    )

    # ---- 4. 视频放官方路径 ----
    video_dir = root / "videos" / "chunk-000" / VIDEO_KEY
    video_dir.mkdir(parents=True)
    shutil.copy(root / "source.mp4", video_dir / "episode_000000.mp4")

    # ---- 5. 官方 Loader 加载 + 解码 ----
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    ds = LeRobotDataset(repo_id="local", root=str(root))
    assert len(ds) == len(frames), f"帧数不符：{len(ds)} != {len(frames)}"
    sample = ds[0]
    img = sample[VIDEO_KEY]
    # 官方 hf_transform_to_torch：视频帧解码为 (C, H, W) float 张量
    assert img.ndim == 3, f"视频帧应为 (C,H,W)，实际 {tuple(img.shape)}"
    assert tuple(img.shape) == (3, 96, 96), f"形状不符：{tuple(img.shape)}"
    assert 0.0 <= float(img.min()) and float(img.max()) <= 1.0, "像素值域应在 [0,1]"
    middle = ds[len(ds) // 2]
    assert tuple(middle[VIDEO_KEY].shape) == (3, 96, 96)
    last = ds[len(ds) - 1]
    assert tuple(last[VIDEO_KEY].shape) == (3, 96, 96)

    evidence = {
        "gate": "GATE-A (video)",
        "claim": "官方 LeRobotDataset 加载含视频 feature 的 v2.1 数据集并真实解码帧",
        "dataset": {
            "root": str(root),
            "frames": len(frames),
            "fps": FPS,
            "video_key": VIDEO_KEY,
            "video_shape": [3, 96, 96],
            "video_source": "lerobot/pusht blob f58d1185...（真实机器人推物数据）",
            "state_dim": len(states[0]),
            "action_dim": len(actions[0]),
        },
        "verification": {
            "frames_loaded": len(ds),
            "decoded_frames_checked": [0, len(ds) // 2, len(ds) - 1],
            "pixel_range_ok": True,
            "loader": "lerobot.datasets.lerobot_dataset.LeRobotDataset",
        },
        "video_file": video_info,
        "not_claimed": [
            "硬件采集（pusht 为公开数据集，B 级样本）",
            "全量 25,650 帧（只取前 100 帧验证解码链路）",
        ],
    }
    out = Path(__file__).resolve().parents[2] / (
        "docs/testing/evidence/2026-11-28-gate-a-video-loader/evidence.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
