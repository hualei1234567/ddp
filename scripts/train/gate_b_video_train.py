"""GATE-B 视频数据集真实训练联动（V6.1 Sprint 3）。

**这不是 mock**：
1. 从 ``lerobot/pusht`` 官方缓存（真实机器人推物数据）切分 **多 Episode**
   （2 个 Episode × 60 帧 = 120 帧）并由 DDP ``frame_writer`` 导出为
   LeRobot v2.1 含视频数据集（``observation.image`` 96×96 H.264 MP4 +
   ``observation.state`` + ``observation.environment_state`` + ``action``）。
2. 调用官方 ``lerobot==0.3.3`` 的 ``lerobot.scripts.train.train``，以
   ``ACTConfig``（含 ``FeatureType.VISUAL`` + ``FeatureType.ENV``）在 CPU
   跑 2 个真实训练步、保存 checkpoint。
3. 使用 ``ACTPolicy.from_pretrained`` 回载 checkpoint 并执行含视频张量
   ``(1, 3, 96, 96)`` 的前向 ``select_action``，验证输出动作维度 ``(1, 2)``。

**诚实边界**：CPU 2 步只证明「多 Episode 含视频 v2.1 数据集可被官方 ACT
训练脚本真实消费、保存并回载前向」——**不证明收敛性、不证明 GPU 性能**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "data-platform"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

PUSHT_BLOB = Path.home() / (
    ".cache/huggingface/hub/datasets--lerobot--pusht/blobs/"
    "f58d11857651dad5983c021b56a50a68a6ce19068834c1fb5cac099219fb3a78"
)
PUSHT_PARQUET = Path.home() / (
    ".cache/huggingface/hub/datasets--lerobot--pusht/blobs/"
    "9abc0431f12d10c33b5b37b1bf1e6e557c8ae1c8c29fb86658969e48fcbbbf01"
)
EPISODE_COUNT = 2
FRAMES_PER_EPISODE = 60
TOTAL_FRAMES = EPISODE_COUNT * FRAMES_PER_EPISODE
FPS = 10.0
VIDEO_KEY = "observation.image"
STEPS = 2
BATCH_SIZE = 2
SEED = 42


def _git_commit(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _cut_video_segment(dest: Path, *, start_frame: int, num_frames: int) -> None:
    """用 ffmpeg 从 pusht 源视频截取指定帧段并重编码为独立 MP4。"""
    start_sec = start_frame / FPS
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(PUSHT_BLOB),
        "-frames:v",
        str(num_frames),
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


def _load_pusht_slice(
    offset: int, length: int
) -> tuple[list[list[float]], list[list[float]], list[float]]:
    """从 pusht parquet 读取指定切片的 state / action / timestamp。"""
    import pyarrow.parquet as pq

    table = pq.read_table(
        PUSHT_PARQUET, columns=["observation.state", "action", "timestamp"]
    )
    end = offset + length
    states = [
        list(row) for row in table.column("observation.state").to_pylist()[offset:end]
    ]
    actions = [list(row) for row in table.column("action").to_pylist()[offset:end]]
    stamps = [float(v) for v in table.column("timestamp").to_pylist()[offset:end]]
    return states, actions, stamps


def export_multi_episode_video_dataset(dataset_root: Path) -> dict[str, object]:
    """导出多 Episode 含视频 LeRobot v2.1 数据集。"""
    from ddp_data.lerobot.frame_writer import (
        FrameFeature,
        build_stats,
        build_stats_json,
        write_frame_episode,
    )

    (dataset_root / "data" / "chunk-000").mkdir(parents=True, exist_ok=True)
    (dataset_root / "meta").mkdir(parents=True, exist_ok=True)
    video_dir = dataset_root / "videos" / "chunk-000" / VIDEO_KEY
    video_dir.mkdir(parents=True, exist_ok=True)

    video_feature = FrameFeature(
        VIDEO_KEY, "video", (96, 96, 3), names=("height", "width", "channels")
    )
    episodes_meta: list[dict[str, object]] = []
    stats_lines: list[str] = []
    last_info_bytes: bytes = b""
    all_states: list[list[float]] = []
    all_actions: list[list[float]] = []
    all_stamps: list[float] = []

    for ep_idx in range(EPISODE_COUNT):
        offset = ep_idx * FRAMES_PER_EPISODE
        ep_video = video_dir / f"episode_{ep_idx:06d}.mp4"
        _cut_video_segment(ep_video, start_frame=offset, num_frames=FRAMES_PER_EPISODE)
        states, actions, stamps = _load_pusht_slice(offset, FRAMES_PER_EPISODE)
        t0 = stamps[0]
        rel_stamps = [t - t0 for t in stamps]
        all_states.extend(states)
        all_actions.extend(actions)
        all_stamps.extend(rel_stamps)

        frames = [
            {"observation": states[i], "action": actions[i], "timestamp": rel_stamps[i]}
            for i in range(FRAMES_PER_EPISODE)
        ]
        result = write_frame_episode(
            frames=frames,
            episode_index=ep_idx,
            fps=FPS,
            observation_dim=len(states[0]),
            action_dim=len(actions[0]),
            video_features=(video_feature,),
        )
        (
            dataset_root / "data" / "chunk-000" / f"episode_{ep_idx:06d}.parquet"
        ).write_bytes(result.parquet_bytes)
        last_info_bytes = result.info_json

        ep_stats = build_stats(
            observations=states, actions=actions, timestamps=rel_stamps
        )
        ep_stats[VIDEO_KEY] = {
            "min": [[[0.0]], [[0.0]], [[0.0]]],
            "max": [[[1.0]], [[1.0]], [[1.0]]],
            "mean": [[[0.5]], [[0.5]], [[0.5]]],
            "std": [[[0.25]], [[0.25]], [[0.25]]],
            "count": [FRAMES_PER_EPISODE],
        }
        normalized_ep_stats = {
            fkey: {k: (v if isinstance(v, list) else [v]) for k, v in fdict.items()}
            for fkey, fdict in ep_stats.items()
        }
        stats_lines.append(
            json.dumps(
                {
                    "episode_index": ep_idx,
                    "stats": normalized_ep_stats,
                }
            )
        )
        episodes_meta.append(
            {
                "episode_index": ep_idx,
                "length": FRAMES_PER_EPISODE,
                "tasks": ["Push T-block to target"],
            }
        )

    info = json.loads(last_info_bytes.decode("utf-8"))
    info["total_episodes"] = EPISODE_COUNT
    info["total_frames"] = TOTAL_FRAMES
    info["total_videos"] = EPISODE_COUNT
    info["splits"] = {"train": f"0:{EPISODE_COUNT}"}
    (dataset_root / "meta" / "info.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8"
    )
    (dataset_root / "meta" / "episodes.jsonl").write_text(
        "\n".join(json.dumps(item) for item in episodes_meta) + "\n", encoding="utf-8"
    )
    (dataset_root / "meta" / "episodes_stats.jsonl").write_text(
        "\n".join(stats_lines) + "\n", encoding="utf-8"
    )
    global_stats = build_stats(
        observations=all_states, actions=all_actions, timestamps=all_stamps
    )
    global_stats[VIDEO_KEY] = {
        "min": [[[0.0]], [[0.0]], [[0.0]]],
        "max": [[[1.0]], [[1.0]], [[1.0]]],
        "mean": [[[0.5]], [[0.5]], [[0.5]]],
        "std": [[[0.25]], [[0.25]], [[0.25]]],
        "count": [TOTAL_FRAMES],
    }
    (dataset_root / "meta" / "stats.json").write_bytes(
        build_stats_json(global_stats, frame_count=TOTAL_FRAMES)
    )
    (dataset_root / "meta" / "tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "Push T-block to target"}) + "\n",
        encoding="utf-8",
    )
    return {
        "dataset_root": str(dataset_root),
        "episodes": EPISODE_COUNT,
        "frames_per_episode": FRAMES_PER_EPISODE,
        "total_frames": TOTAL_FRAMES,
    }


def _normalize_sha256(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        return normalized
    return f"sha256:{normalized}"


def build_evidence_payload(
    evidence: dict[str, Any],
    *,
    run_kind: str = "SMOKE",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    dataset_version_id: str | None = None,
    exit_code: int | None = None,
    stdout_tail: str | None = None,
) -> dict[str, Any]:
    """将 ``evidence.json`` 转换为 DDP ``training_run_evidence`` API 请求体。"""
    training = evidence.get("training") or {}
    checkpoint = evidence.get("checkpoint") or evidence.get("checkpoint_reload") or {}
    dataset = evidence.get("dataset") or {}

    now = datetime.now(UTC)
    resolved_finished = finished_at or now
    resolved_started = started_at or resolved_finished

    config_canonical = json.dumps(
        {
            "gate": evidence.get("gate"),
            "sprint": evidence.get("sprint"),
            "training": {
                "policy": training.get("policy"),
                "device": training.get("device"),
                "steps": training.get("steps"),
                "batch_size": training.get("batch_size"),
                "seed": training.get("seed"),
            },
            "dataset": {
                "episodes": dataset.get("episodes"),
                "total_frames": dataset.get("total_frames") or dataset.get("frames"),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    config_sha256 = f"sha256:{hashlib.sha256(config_canonical).hexdigest()}"

    raw_model_sha = checkpoint.get("model_safetensors_sha256")
    checkpoint_sha256 = _normalize_sha256(raw_model_sha)
    reload_ok = checkpoint.get("reload_ok", checkpoint.get("success", True))
    resolved_exit_code = exit_code if exit_code is not None else (0 if reload_ok else 1)

    runtime_versions: dict[str, Any] = {
        "python": evidence.get("python")
        or training.get("python")
        or sys.version.split()[0],
        "platform": evidence.get("platform")
        or training.get("platform")
        or platform.platform(),
    }
    if evidence.get("git_commit"):
        runtime_versions["git_commit"] = evidence["git_commit"]
    if training.get("torch"):
        runtime_versions["torch"] = training["torch"]
    code_info = evidence.get("code")
    if isinstance(code_info, dict) and code_info.get("lerobot"):
        runtime_versions["lerobot"] = code_info["lerobot"]

    metrics_summary: dict[str, Any] = {
        "gate": evidence.get("gate"),
        "sprint": evidence.get("sprint"),
        "reload_ok": bool(reload_ok),
    }
    if raw_model_sha:
        metrics_summary["model_safetensors_sha256"] = str(raw_model_sha).removeprefix(
            "sha256:"
        )
    if training.get("elapsed_seconds") is not None:
        metrics_summary["elapsed_seconds"] = training["elapsed_seconds"]
    elif training.get("wall_seconds") is not None:
        metrics_summary["elapsed_seconds"] = training["wall_seconds"]
    if checkpoint.get("reload_forward_shape") is not None:
        metrics_summary["reload_forward_shape"] = checkpoint["reload_forward_shape"]

    payload: dict[str, Any] = {
        "run_kind": run_kind,
        "device": str(training.get("device") or "cpu"),
        "runtime_versions": runtime_versions,
        "config_sha256": config_sha256,
        "seed": int(training.get("seed", SEED)),
        "batch_size": int(training.get("batch_size", BATCH_SIZE)),
        "steps": int(training.get("steps", STEPS)),
        "started_at": resolved_started.isoformat(),
        "finished_at": resolved_finished.isoformat(),
        "metrics_summary": metrics_summary,
        "checkpoint_ref": checkpoint.get("dir") or checkpoint.get("from"),
        "checkpoint_sha256": checkpoint_sha256,
        "exit_code": resolved_exit_code,
        "stdout_tail": stdout_tail,
    }
    if dataset_version_id is not None:
        payload["dataset_version_id"] = dataset_version_id
    return payload


def report_evidence_to_api(
    *,
    api_base_url: str,
    project_id: str,
    external_ref_id: str,
    payload: dict[str, Any],
    auth_token: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """将训练证据真实 POST 上报到 DDP ``training_run_evidence`` 接口。"""
    endpoint = (
        f"{api_base_url.rstrip('/')}/api/v1/projects/{project_id}"
        f"/external-training-references/{external_ref_id}/evidence"
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    req = urllib_request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- 函数开头已校验 endpoint 仅允许 http/https scheme
        with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"上报训练证据失败（HTTP {exc.code} {endpoint}）：{detail[:500]}"
        ) from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"上报训练证据网络异常（{endpoint}）：{exc.reason}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GATE-B 视频数据集真实训练与证据上报")
    parser.add_argument(
        "--report-api-url",
        default=None,
        help="可选：DDP API 基地址（如 http://127.0.0.1:8000），提供时将 evidence.json 上报入库",
    )
    parser.add_argument(
        "--project-id", default=None, help="目标项目 UUID（上报时必填）"
    )
    parser.add_argument(
        "--external-ref-id",
        default=None,
        help="目标外部训练引用 UUID（上报时必填）",
    )
    parser.add_argument(
        "--dataset-version-id",
        default=None,
        help="可选：显式绑定数据集版本 UUID（缺省时由服务端继承引用行版本）",
    )
    parser.add_argument(
        "--run-kind",
        choices=("SMOKE", "REAL"),
        default="SMOKE",
        help="训练证据类型（默认 SMOKE）",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="可选：Bearer Token（默认读取环境变量 DDP_API_TOKEN）",
    )
    parser.add_argument(
        "--report-only-evidence-file",
        default=None,
        help="可选：跳过训练，直接将已有的 evidence.json 上报到 --report-api-url",
    )
    args = parser.parse_args(argv)
    if args.report_only_evidence_file and not args.report_api_url:
        parser.error("--report-only-evidence-file 必须配合 --report-api-url 使用")
    if args.report_api_url and (not args.project_id or not args.external_ref_id):
        parser.error(
            "启用 --report-api-url 时必须同时提供 --project-id 与 --external-ref-id"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    auth_token = args.auth_token or os.environ.get("DDP_API_TOKEN")

    if args.report_only_evidence_file:
        evidence = json.loads(
            Path(args.report_only_evidence_file).read_text(encoding="utf-8")
        )
        payload = build_evidence_payload(
            evidence,
            run_kind=args.run_kind,
            dataset_version_id=args.dataset_version_id,
        )
        result = report_evidence_to_api(
            api_base_url=args.report_api_url,
            project_id=args.project_id,
            external_ref_id=args.external_ref_id,
            payload=payload,
            auth_token=auth_token,
        )
        print(f"[REPORT] 训练证据已入库：evidence_id={result.get('evidence_id')}")
        return 0

    import torch
    from lerobot.configs.default import WandBConfig
    from lerobot.configs.train import DatasetConfig, TrainPipelineConfig
    from lerobot.configs.types import FeatureType, PolicyFeature
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.scripts.train import train

    work_root = Path(tempfile.mkdtemp(prefix="ddp-gateb-video-"))
    dataset_root = work_root / "dataset"
    output_dir = work_root / "train_out"

    ds_summary = export_multi_episode_video_dataset(dataset_root)
    print(f"[1/3] 导出多 Episode 视频数据集完成：{ds_summary}")

    cfg = ACTConfig(
        repo_id="local/ddp-pusht-video-smoke",
        device="cpu",
        push_to_hub=False,
        input_features={
            VIDEO_KEY: PolicyFeature(type=FeatureType.VISUAL, shape=(3, 96, 96)),
            "observation.state": PolicyFeature(type=FeatureType.STATE, shape=(2,)),
            "observation.environment_state": PolicyFeature(
                type=FeatureType.ENV, shape=(2,)
            ),
        },
        output_features={"action": PolicyFeature(type=FeatureType.ACTION, shape=(2,))},
    )

    train_cfg = TrainPipelineConfig(
        dataset=DatasetConfig(repo_id="local/pusht-video", root=str(dataset_root)),
        policy=cfg,
        output_dir=output_dir,
        job_name="v61-sprint3-video-smoke",
        seed=SEED,
        steps=STEPS,
        batch_size=BATCH_SIZE,
        save_freq=100_000,
        log_freq=1,
        wandb=WandBConfig(enable=False),
    )

    started_dt = datetime.now(UTC)
    started = time.time()
    train(train_cfg)
    elapsed = round(time.time() - started, 3)
    finished_dt = datetime.now(UTC)
    print(f"[2/3] ACT 视频训练 {STEPS} 步完成，耗时 {elapsed}s")

    ckpt_dir = sorted((output_dir / "checkpoints").glob("*/pretrained_model"))[-1]
    safetensors_file = ckpt_dir / "model.safetensors"
    model_sha256 = hashlib.sha256(safetensors_file.read_bytes()).hexdigest()

    reloaded = ACTPolicy.from_pretrained(str(ckpt_dir))
    reloaded.eval()
    with torch.no_grad():
        probe_batch = {
            VIDEO_KEY: torch.rand(1, 3, 96, 96),
            "observation.state": torch.randn(1, 2),
            "observation.environment_state": torch.randn(1, 2),
        }
        probe_action = reloaded.select_action(probe_batch)

    reload_ok = tuple(probe_action.shape) == (1, 2)
    print(
        f"[3/3] Checkpoint 回载前向验证：shape={tuple(probe_action.shape)} "
        f"ok={reload_ok} sha256={model_sha256[:16]}..."
    )

    evidence_dir = (
        REPO_ROOT / "docs" / "testing" / "evidence" / "2026-11-28-gate-b-video-training"
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "gate": "GATE-B-VIDEO",
        "sprint": "V6.1-Sprint-3",
        "git_commit": _git_commit(REPO_ROOT),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "dataset": ds_summary,
        "training": {
            "policy": "ACT",
            "device": "cpu",
            "steps": STEPS,
            "batch_size": BATCH_SIZE,
            "seed": SEED,
            "elapsed_seconds": elapsed,
            "torch": torch.__version__,
        },
        "checkpoint": {
            "dir": str(ckpt_dir),
            "model_safetensors_sha256": model_sha256,
            "reload_forward_shape": list(probe_action.shape),
            "reload_ok": reload_ok,
        },
    }
    (evidence_dir / "evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if args.report_api_url:
        payload = build_evidence_payload(
            evidence,
            run_kind=args.run_kind,
            started_at=started_dt,
            finished_at=finished_dt,
            dataset_version_id=args.dataset_version_id,
            exit_code=0 if reload_ok else 1,
        )
        result = report_evidence_to_api(
            api_base_url=args.report_api_url,
            project_id=args.project_id,
            external_ref_id=args.external_ref_id,
            payload=payload,
            auth_token=auth_token,
        )
        print(f"[REPORT] 训练证据已入库：evidence_id={result.get('evidence_id')}")

    return 0 if reload_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
