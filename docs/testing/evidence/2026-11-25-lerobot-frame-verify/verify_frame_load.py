"""官方 lerobot==0.3.3 逐帧读取验证脚本（在 lerobot 隔离环境运行）。"""
import json
from pathlib import Path

from lerobot.datasets.lerobot_dataset import LeRobotDataset

root = Path("/tmp/ddp-frame-dataset")
meta = json.loads(Path("/tmp/gate2-info.json").read_text())

ds = LeRobotDataset("local/ddp-frame", root=root, episodes=None)
item = ds[0]
obs_keys = sorted(k for k in item if str(k).startswith("observation.state"))
act_keys = sorted(k for k in item if str(k).startswith("action["))
info = json.loads((root / "meta/info.json").read_text())
read_ok = sum(
    1
    for i in range(ds.num_frames)
    if len([k for k in ds[i] if str(k).startswith("observation.state")]) == 32
)

checks = [
    ("Loader 构造成功", True),
    ("总帧数为真实值（>1）", ds.num_frames > 1),
    ("fps 为真实值（>1）", info["fps"] > 1),
    ("observation.state 为 32 维", len(obs_keys) == 32),
    ("action 为 7 维", len(act_keys) == 7),
    ("全部帧可逐帧读取", read_ok == ds.num_frames),
    ("loadable_scope=LOADER_FRAME", info["ddp"]["loadable_scope"] == "LOADER_FRAME"),
]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"GATE-2: {'PASS' if all(o for _, o in checks) else 'FAIL'}")
print(f"frames={ds.num_frames} read_ok={read_ok} sha256={meta['sha'][:16]}...")
