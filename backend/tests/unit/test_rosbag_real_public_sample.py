"""固定 revision 的公开 ROS Bag 样例复验（默认跳过，不以 Mock 冒充）。

设置 ``DDP_TEST_REAL_ROSBAG`` 指向 foxglove/mcap 的
``noetic-multitopic-none.bag`` 后执行。来源、许可、SHA256 与观察事实见
``backend/tests/fixtures/public/rosbag/facts.json``。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from ddp_data.rosbag import read_rosbag_structure

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "public" / "rosbag"
_FACTS = json.loads((_FIXTURE_DIR / "facts.json").read_text())
_REAL_PATH = os.environ.get("DDP_TEST_REAL_ROSBAG")
pytestmark = pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_ROSBAG 指向固定公开 noetic-multitopic-none.bag",
)


def test_real_public_rosbag_matches_pinned_hash() -> None:
    assert _REAL_PATH is not None
    digest = hashlib.sha256(Path(_REAL_PATH).read_bytes()).hexdigest()
    assert digest == _FACTS["source"]["file_sha256"]
    assert Path(_REAL_PATH).stat().st_size == _FACTS["source"]["file_size_bytes"]


def test_real_public_rosbag_structure_matches_recorded_facts() -> None:
    assert _REAL_PATH is not None
    actual = read_rosbag_structure(_REAL_PATH).as_metadata()
    expected = _FACTS["observed_structure"]

    for key in (
        "format",
        "topic_count",
        "message_count",
        "start_time_ns",
        "end_time_ns",
        "duration_ns",
        "topics",
    ):
        assert actual[key] == expected[key]
