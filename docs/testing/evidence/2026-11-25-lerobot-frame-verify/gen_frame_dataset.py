"""步骤1（backend 环境）：真实样本 → 逐帧数据集目录。"""
import sys, json, tempfile, hashlib
from pathlib import Path
sys.path.insert(0, "data-platform"); sys.path.insert(0, "backend")
from ddp_data.hdf5 import read_hdf5_dataset_slice
from app.application.processing import stream_extraction_stage as ses
from ddp_data.lerobot.frame_writer import (
    write_frame_episode, build_stats, build_stats_json, build_episodes_stats_jsonl)

sample = Path.home()/"ddp-samples/robomimic/demo_v15.hdf5"
layout = ses._LAYOUTS[0]
sha = hashlib.sha256(sample.read_bytes()).hexdigest()
print(f"样本: {sample.name} {sample.stat().st_size}B sha256={sha[:16]}...")

rec = ses.extract_robomimic_demo(read_slice=read_hdf5_dataset_slice, file_path=str(sample), demo_index=0, layout=layout)
n_obs = len(rec["observations"])
print(f"提取: observation={n_obs} action={len(rec['actions'])}")

frames = [{"observation": rec["observations"][i]["values"],
           "action": rec["actions"][i]["values"],
           "timestamp": i*0.05} for i in range(n_obs)]
result = write_frame_episode(frames=frames, episode_index=0, fps=20.0, observation_dim=32, action_dim=7)
print(f"导出: parquet={len(result.parquet_bytes)}B frames={result.frame_count}")

root = Path("/tmp/ddp-frame-dataset")
if root.exists():
    import shutil; shutil.rmtree(root)
(root/"data/chunk-000").mkdir(parents=True); (root/"meta").mkdir(parents=True)
(root/"data/chunk-000/episode_000000.parquet").write_bytes(result.parquet_bytes)
(root/"meta/info.json").write_bytes(result.info_json)
stats = build_stats(observations=[f["observation"] for f in frames], actions=[f["action"] for f in frames], timestamps=[f["timestamp"] for f in frames])
(root/"meta/stats.json").write_bytes(build_stats_json(stats, frame_count=result.frame_count))
(root/"meta/episodes_stats.jsonl").write_bytes(build_episodes_stats_jsonl(stats, episode_index=0))
(root/"meta/episodes.jsonl").write_text(json.dumps({"episode_index":0,"tasks":["ddp lift"],"length":result.frame_count})+"\n")
(root/"meta/tasks.jsonl").write_text(json.dumps({"task_index":0,"task":"ddp lift"})+"\n")
print(f"数据集: {root}")
Path("/tmp/gate2-info.json").write_text(json.dumps({"frames": result.frame_count, "sha": sha, "parquet_bytes": len(result.parquet_bytes)}))
