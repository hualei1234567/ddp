"""公开 DROID-100 真实元数据的联网时间同步验收。

默认跳过；显式设置 ``DDP_RUN_PUBLIC_DATA_E2E=1`` 才下载固定 revision 的三个
小型元数据文件，并对三路公开 MP4 做 32 字节 Range 探测。外部文件均按不可信
输入处理：固定 SHA、下载上限、超时、禁止执行其中代码。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
from app.domain.episodes.sync_analysis import analyze_stream_sync
from app.domain.episodes.sync_drift import SyncDriftStatus
from app.domain.episodes.time_alignment_manifest import parse_time_alignment_manifest
from ddp_data.lerobot import build_v3_time_alignment_manifest

_REPO = "lerobot/droid_100"
_REVISION = "87301a2d2e99340e2010c9ef0f1d8e780b08aaf9"
_BASE = f"https://huggingface.co/datasets/{_REPO}/resolve/{_REVISION}"
_FILES = {
    "info.json": (
        "meta/info.json",
        64 * 1024,
        "6718973f1f5a5ca76cfdfdddafc177da83636d1a38e50aedca37c090991d5d78",
    ),
    "episodes.parquet": (
        "meta/episodes/chunk-000/file-000.parquet",
        512 * 1024,
        "a22cd872be7140555b7f8dfa6c34ff64642b0308d933c9aa658a89b69746fbbb",
    ),
}
_VIDEO_KEYS = (
    "observation.images.exterior_image_1_left",
    "observation.images.exterior_image_2_left",
    "observation.images.wrist_image_left",
)

pytestmark = pytest.mark.skipif(
    os.getenv("DDP_RUN_PUBLIC_DATA_E2E") != "1",
    reason="需要显式设置 DDP_RUN_PUBLIC_DATA_E2E=1 才访问固定公开数据",
)


def test_droid_100_all_episodes_produce_three_stream_manifests(tmp_path: Path) -> None:
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        downloaded = {
            name: _download_bounded(client, tmp_path, name, remote, limit, sha256)
            for name, (remote, limit, sha256) in _FILES.items()
        }
        for video_key in _VIDEO_KEYS:
            _assert_public_mp4_range(client, video_key)

    info = json.loads(downloaded["info.json"].read_text(encoding="utf-8"))
    episodes = pq.read_table(downloaded["episodes.parquet"]).to_pylist()
    assert len(episodes) == 100

    aligned = 0
    for row in episodes:
        payload = build_v3_time_alignment_manifest(
            dataset_id=_REPO,
            info=info,
            episode=row,
        )
        group = parse_time_alignment_manifest(payload).groups[0]
        assert len(group.windows) == 3
        assert "observation.images.wrist_image_left" in group.profiles
        report = analyze_stream_sync(group.windows, group.profiles)
        assert report.status is SyncDriftStatus.ALIGNED
        assert all(
            detail.correction is None or detail.correction.applied is False
            for detail in report.streams
        )
        aligned += 1

    assert aligned == 100


def _download_bounded(
    client: httpx.Client,
    root: Path,
    name: str,
    remote: str,
    limit: int,
    expected_sha256: str,
) -> Path:
    destination = root / name
    digest = hashlib.sha256()
    size = 0
    with client.stream("GET", f"{_BASE}/{remote}") as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise AssertionError(f"{name} 超过下载上限 {limit}")
                digest.update(chunk)
                handle.write(chunk)
    assert digest.hexdigest() == expected_sha256
    return destination


def _assert_public_mp4_range(client: httpx.Client, video_key: str) -> None:
    remote = f"videos/{video_key}/chunk-000/file-000.mp4"
    response = client.get(f"{_BASE}/{remote}", headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert len(response.content) == 32
    assert response.content[4:8] == b"ftyp"
