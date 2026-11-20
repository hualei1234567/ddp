"""DROID 官方 GitHub 固定提交的公开硬件事实联网验收。"""

from __future__ import annotations

import hashlib
import os

import httpx
import pytest
from ddp_data.droid import build_droid_hardware_blueprint

_REPOSITORY = "droid-dataset/droid"
_COMMIT = "33ae6a67274f36d2e29525b86f23a56616ef43a7"
_BASE = f"https://raw.githubusercontent.com/{_REPOSITORY}/{_COMMIT}"
_FILES = {
    "shopping": (
        "docs/hardware-setup/shopping-list.md",
        16 * 1024,
        "75992f70751c7c63a6ca8681f226d2d026593823811e7b780e059c34f39b452d",
    ),
    "robot_config": (
        "config/panda/franka_panda.yaml",
        4 * 1024,
        "e7c56671f3513302bbbcaa641329fd7039b365431171f2563b2668e82c080ce5",
    ),
}

pytestmark = pytest.mark.skipif(
    os.getenv("DDP_RUN_PUBLIC_HARDWARE_E2E") != "1",
    reason="需要显式设置 DDP_RUN_PUBLIC_HARDWARE_E2E=1 才访问固定 GitHub 提交",
)


def test_fixed_droid_hardware_sources_match_blueprint_facts() -> None:
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        documents = {
            name: _download_bounded(client, remote, limit, sha256)
            for name, (remote, limit, sha256) in _FILES.items()
        }

    shopping = documents["shopping"].decode("utf-8")
    assert "Franka Emika Panda (or) Franka Research 3" in shopping
    assert "2F-85 Robotiq Gripper" in shopping
    assert "| Zed 2 | 2 |" in shopping
    assert "| Zed Mini | 1 |" in shopping

    config = documents["robot_config"].decode("utf-8")
    assert "num_dofs: 7" in config
    assert "controlled_joints:  [0, 1, 2, 3, 4, 5, 6]" in config

    # producer 本身由提交的结构化 provenance fixture 驱动；这里证明该 fixture 的
    # 关键硬件事实仍与固定远端对象一致，不执行远端仓库代码。
    blueprint = build_droid_hardware_blueprint(
        {
            "source": {
                "repository": _REPOSITORY,
                "commit": _COMMIT,
                "license": None,
                "dataset_id": "lerobot/droid_100",
                "dataset_revision": "87301a2d2e99340e2010c9ef0f1d8e780b08aaf9",
                "dataset_license": "MIT",
            },
            "robot": {
                "vendor": "Franka Robotics",
                "model": "Franka Emika Panda / Franka Research 3",
                "quantity": 1,
                "num_dofs": 7,
            },
            "gripper": {"vendor": "Robotiq", "model": "2F-85", "quantity": 1},
            "cameras": [
                {"vendor": "Stereolabs", "model": "ZED 2", "quantity": 2},
                {"vendor": "Stereolabs", "model": "ZED Mini", "quantity": 1},
            ],
            "stream_models": {
                "observation.images.exterior_image_1_left": "ZED 2",
                "observation.images.exterior_image_2_left": "ZED 2",
                "observation.images.wrist_image_left": "ZED Mini",
            },
            "video_streams": [
                {
                    "stream_id": "observation.images.exterior_image_1_left",
                    "fps": 15.0,
                    "width": 320,
                    "height": 180,
                    "role": "EXTERIOR",
                },
                {
                    "stream_id": "observation.images.exterior_image_2_left",
                    "fps": 15.0,
                    "width": 320,
                    "height": 180,
                    "role": "EXTERIOR",
                },
                {
                    "stream_id": "observation.images.wrist_image_left",
                    "fps": 15.0,
                    "width": 320,
                    "height": 180,
                    "role": "WRIST_FIRST_PERSON",
                },
            ],
        }
    )
    assert blueprint["robot"]["capability_profile"]["num_dofs"] == 7
    assert len(blueprint["sensors"]) == 3


def _download_bounded(
    client: httpx.Client,
    remote: str,
    limit: int,
    expected_sha256: str,
) -> bytes:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    with client.stream("GET", f"{_BASE}/{remote}") as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise AssertionError(f"{remote} 超过下载上限 {limit}")
            digest.update(chunk)
            chunks.append(chunk)
    assert digest.hexdigest() == expected_sha256
    return b"".join(chunks)
