# -*- coding: utf-8 -*-
"""V4.1 FR-034/035 步骤 3：隔离 venv 中用官方 LeRobotDataset 真实加载 DDP 导出产物。

判定口径（任务指定）：构造成功 + 能读出 total_episodes 与 episode 索引
即算「结构合规可加载」；逐帧迭代（__getitem__）不作为失败条件（交付边界
本来就是 METADATA_ONLY）。全程如实记录输出与异常。
"""

from __future__ import annotations

import json
import os
import sys
import traceback

print("=== 环境 ===", flush=True)
import lerobot

print("lerobot version:", getattr(lerobot, "__version__", "?"), flush=True)
import pyarrow

print("pyarrow:", pyarrow.__version__, flush=True)
import pandas

print("pandas:", pandas.__version__, flush=True)

print("\n=== import LeRobotDataset ===", flush=True)
from lerobot.datasets.lerobot_dataset import LeRobotDataset

print("import OK:", LeRobotDataset, flush=True)

print("\n=== 构造 LeRobotDataset ===", flush=True)
try:
    ds = LeRobotDataset(
        repo_id="local/ddp-export",
        root=os.environ.get("LEROBOT_ROOT", "/tmp/lerobot-dataset"),
        episodes=[0, 1, 2],
    )
    print("构造成功 (type):", type(ds).__name__, flush=True)
except Exception:
    traceback.print_exc()
    print("\nRESULT: CONSTRUCT_FAILED", flush=True)
    sys.exit(2)

print("\n=== 元数据读取 ===", flush=True)
try:
    meta = getattr(ds, "meta", None)
    if meta is not None:
        info = getattr(meta, "info", None)
        print("ds.meta.info:", json.dumps(info, ensure_ascii=False, default=str)[:2000], flush=True)
        print("ds.meta.total_episodes:", meta.total_episodes, flush=True)
        print("ds.meta.total_frames:", getattr(meta, "total_frames", "?"), flush=True)
        print("ds.meta.total_tasks:", getattr(meta, "total_tasks", "?"), flush=True)
        fps = getattr(meta, "fps", "?")
        print("ds.meta.fps:", fps, flush=True)
    else:
        print("ds.meta 不存在，尝试旧版 API ds.info:", flush=True)
        info = getattr(ds, "info", None)
        print("ds.info:", info, flush=True)

    print("\nds.num_frames:", getattr(ds, "num_frames", "?"), flush=True)

    # episode 索引读取
    episode_indices = None
    if meta is not None and hasattr(meta, "episodes") and meta.episodes is not None:
        try:
            import pyarrow.parquet as pq

            table = pq.read_table(os.environ.get("LEROBOT_ROOT", "/tmp/lerobot-dataset") + "/meta/episodes/chunk-000.parquet")
            episode_indices = table.column("episode_index").to_pylist()
            print("meta/episodes parquet episode_index:", episode_indices, flush=True)
        except Exception as exc:  # noqa: BLE001
            print("meta/episodes parquet 读取失败:", exc, flush=True)
    if episode_indices is None:
        # 从 data parquet 直接读 episode_index 列
        import pyarrow.parquet as pq

        all_indices = []
        for idx in range(3):
            table = pq.read_table(f"{os.environ.get('LEROBOT_ROOT', '/tmp/lerobot-dataset')}/data/chunk-000/episode_{idx:06d}.parquet")
            all_indices.extend(table.column("episode_index").to_pylist())
        episode_indices = sorted(all_indices)
        print("data parquet episode_index 列:", episode_indices, flush=True)

    print("\n=== 逐帧读取探测（边界外，不作为失败条件）===", flush=True)
    try:
        item0 = ds[0]
        print("ds[0] keys:", list(item0.keys()) if hasattr(item0, "keys") else type(item0), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ds[0] 失败（符合 METADATA_ONLY 边界预期）: {type(exc).__name__}: {exc}", flush=True)

    print("\nRESULT: LOADER_VERIFY_OK", flush=True)
    sys.exit(0)
except Exception:
    traceback.print_exc()
    print("\nRESULT: METADATA_READ_FAILED", flush=True)
    sys.exit(3)
