"""步骤 1（backend 环境）：从真实 RoboMimic HDF5 提取前 5 个 Episode 经 NDJSON 物化产物构建多 Episode LeRobot v2.1 逐帧数据集。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "data-platform")
sys.path.insert(0, "backend")

from app.application.processing.stream_extraction_stage import (  # noqa: E402
    LAYOUTS,
    extract_robomimic_demo,
    render_ndjson,
)
from ddp_data.hdf5 import read_hdf5_dataset_slice  # noqa: E402
from ddp_data.lerobot.frame_dataset import (  # noqa: E402
    parse_stream_extraction_ndjson,
    write_frame_dataset,
)

SAMPLE_PATH = Path.home() / "ddp-samples/robomimic/demo_v15.hdf5"
OUT_ROOT = Path("/tmp/ddp-multi-ep-frame-dataset")
META_OUT = Path("/tmp/gate2-multi-ep-info.json")
EPISODE_COUNT = 5
FPS = 20.0


def main() -> int:
    sha = hashlib.sha256(SAMPLE_PATH.read_bytes()).hexdigest()
    layout = LAYOUTS[0]
    episodes_spec = []

    for demo_idx in range(EPISODE_COUNT):
        record = extract_robomimic_demo(
            read_slice=read_hdf5_dataset_slice,
            file_path=str(SAMPLE_PATH),
            demo_index=demo_idx,
            layout=layout,
        )
        ndjson_bytes = render_ndjson(record)
        frames = parse_stream_extraction_ndjson(ndjson_bytes, fps=FPS)
        episodes_spec.append(
            {"frames": frames, "task": "robomimic lift ph", "split": "train"}
        )

    bundle = write_frame_dataset(
        episodes=episodes_spec,
        fps=FPS,
        observation_dim=32,
        action_dim=7,
    )

    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    for rel_path, content in bundle.files.items():
        target = OUT_ROOT / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    summary = {
        "sample_sha256": sha,
        "total_episodes": bundle.total_episodes,
        "total_frames": bundle.total_frames,
        "episode_lengths": list(bundle.episode_lengths),
        "source_digest": bundle.source_digest,
        "file_count": len(bundle.files),
    }
    META_OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
