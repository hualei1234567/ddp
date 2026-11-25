"""步骤 2（lerobot==0.3.3 隔离环境）：验证多 Episode 逐帧数据集全量与子集加载、跨集索引连续性与逐帧张量读取。"""

from __future__ import annotations

import json
from pathlib import Path

from lerobot.datasets.lerobot_dataset import LeRobotDataset

ROOT = Path("/tmp/ddp-multi-ep-frame-dataset")
META = json.loads(Path("/tmp/gate2-multi-ep-info.json").read_text(encoding="utf-8"))


def main() -> int:
    ds_all = LeRobotDataset("local/ddp-multi-ep", root=ROOT, episodes=None)
    ds_sub = LeRobotDataset("local/ddp-multi-ep", root=ROOT, episodes=[0, 2, 4])

    info = json.loads((ROOT / "meta/info.json").read_text(encoding="utf-8"))
    read_ok = 0
    seen_episodes: set[int] = set()
    global_indices: list[int] = []

    for i in range(ds_all.num_frames):
        row = ds_all[i]
        obs_dims = [k for k in row if str(k).startswith("observation.state")]
        act_dims = [k for k in row if str(k).startswith("action[")]
        if len(obs_dims) == 32 and len(act_dims) == 7:
            read_ok += 1
        ep_val = row["episode_index"]
        idx_val = row["index"]
        seen_episodes.add(int(ep_val.item() if hasattr(ep_val, "item") else ep_val))
        global_indices.append(
            int(idx_val.item() if hasattr(idx_val, "item") else idx_val)
        )

    expected_sub_frames = (
        META["episode_lengths"][0]
        + META["episode_lengths"][2]
        + META["episode_lengths"][4]
    )
    checks = [
        ("全集 Loader 构造成功（episodes=None）", ds_all.num_episodes == 5),
        (
            "全集总帧数精确匹配（5 个真实 demo）",
            ds_all.num_frames == META["total_frames"] and ds_all.num_frames > 200,
        ),
        ("全部 5 个 Episode 均被真实遍历", seen_episodes == {0, 1, 2, 3, 4}),
        (
            "全局 index 列跨 Episode 严格连续 0..N-1",
            global_indices == list(range(ds_all.num_frames)),
        ),
        ("全部帧 32 维观测 + 7 维动作逐帧读取成功", read_ok == ds_all.num_frames),
        (
            "子集选择加载成功（episodes=[0,2,4]）",
            ds_sub.num_frames == expected_sub_frames,
        ),
        (
            "loadable_scope=LOADER_FRAME",
            info["ddp"]["loadable_scope"] == "LOADER_FRAME",
        ),
    ]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    passed = all(ok for _, ok in checks)
    print(
        f"MULTI-EPISODE GATE-2: {'PASS' if passed else 'FAIL'} "
        f"(episodes={ds_all.num_episodes}, frames={ds_all.num_frames}, subset_frames={ds_sub.num_frames})"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
