"""export_writer 单测（V31-042 切片 2）：确定性、结构合规、诚实边界。"""

from __future__ import annotations

import io
import json

import pytest
from ddp_data.lerobot.export_writer import (
    EPISODES_PER_CHUNK,
    LEROBOT_CODEBASE_VERSION,
    required_lerobot_paths,
    write_lerobot_metadata,
)


def make_episode(index: int, split: str = "train", length: int = 100) -> dict[str, object]:
    return {
        "episode_id": f"00000000-0000-0000-0000-{index:012d}",
        "episode_version": 1,
        "task": "put object on table",
        "length": length,
        "split": split,
    }


def make_episodes(count: int) -> list[dict[str, object]]:
    return [make_episode(i) for i in range(count)]


def read_parquet_row(data: bytes) -> dict[str, object]:
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(data))
    return {name: table.column(name).to_pylist() for name in table.column_names}


# ---- 确定性 ----


def test_same_input_produces_identical_bytes() -> None:
    """方案 §4.2 硬要求：同输入两次产出字节一致。"""
    episodes = make_episodes(3)
    first = write_lerobot_metadata(
        episodes=episodes,
        dataset_version_id="v-1",
        created_at_iso="2026-11-23T00:00:00+00:00",
    )
    second = write_lerobot_metadata(
        episodes=list(reversed(episodes)),
        dataset_version_id="v-1",
        created_at_iso="2026-11-23T00:00:00+00:00",
    )
    assert [a.path for a in first] == [a.path for a in second]
    for left, right in zip(first, second, strict=True):
        assert left.data == right.data, left.path


def test_row_order_does_not_change_episode_index_assignment() -> None:
    """episode_index 由排序后位置决定，与调用方传入顺序无关。"""
    one = write_lerobot_metadata(
        episodes=make_episodes(3), dataset_version_id="v", created_at_iso="t"
    )
    two = write_lerobot_metadata(
        episodes=list(reversed(make_episodes(3))),
        dataset_version_id="v",
        created_at_iso="t",
    )
    # info.json 不再由 writer 产出：顺序无关性改由 parquet 集合与 stats 断言。
    assert [a.path for a in one] == [a.path for a in two]
    assert all(a.data == b.data for a, b in zip(one, two, strict=True))
    stats_one = json.loads(next(a for a in one if a.path == "meta/stats.json").data)
    stats_two = json.loads(next(a for a in two if a.path == "meta/stats.json").data)
    assert stats_one["length"]["count"] == stats_two["length"]["count"] == [3.0]


# ---- 结构合规 ----


def test_writer_does_not_produce_info_json() -> None:
    """info.json 由 backend 权威生成（DDP 自有键 + LeRobot 必需键合并），
    writer 不得产出同名文件造成双条目（切片 3 接线时的真实缺陷，回归守卫）。"""
    artifacts = write_lerobot_metadata(
        episodes=make_episodes(2),
        dataset_version_id="v-1",
        created_at_iso="2026-11-23T00:00:00+00:00",
    )
    assert not any(a.path == "meta/info.json" for a in artifacts)
    assert {a.path for a in artifacts} >= {"meta/tasks.jsonl", "meta/stats.json"}


def test_features_declare_episode_level_columns_only() -> None:
    """backend info 合并 LeRobot 键后：features 显式只含 episode 级列。"""
    from datetime import datetime

    from app.domain.datasets.enums import Split
    from app.domain.datasets.exporting import build_export_artifacts

    from tests.unit.test_export_domain import make_item, make_job, make_version

    artifacts = build_export_artifacts(
        make_job(),
        make_version(),
        (make_item(0, Split.TRAIN), make_item(1, Split.TEST)),
        created_at=datetime(2026, 11, 9, 12, 0),
    )
    info = json.loads(next(a for a in artifacts if a.path == "meta/info.json").data)
    for key in (
        "codebase_version",
        "fps",
        "features",
        "data_path",
        "video_path",
        "chunks_size",
        "total_episodes",
        "total_tasks",
        "total_frames",
    ):
        assert key in info, f"info.json 缺 LeRobot 必需键 {key}"
    assert info["codebase_version"] == LEROBOT_CODEBASE_VERSION
    features = info["features"]
    assert set(features) == {"episode_index", "task_index", "length", "split"}
    assert not any(key.startswith("observation.") for key in features)
    assert not any(key.startswith("action") for key in features)
    assert info["loadable_scope"] == "METADATA_ONLY"
    assert info["verification_status"] == "UNVERIFIED"
    assert info["frame_level_mapping"] is False


def test_tasks_jsonl_has_placeholder_task() -> None:
    artifacts = write_lerobot_metadata(
        episodes=make_episodes(2), dataset_version_id="v", created_at_iso="t"
    )
    tasks_raw = next(a for a in artifacts if a.path == "meta/tasks.jsonl").data.decode("utf-8")
    tasks = [json.loads(line) for line in tasks_raw.splitlines()]
    assert tasks == [{"task_index": 0, "task": "put object on table"}]


def test_stats_json_exists_with_episode_level_stats() -> None:
    episodes = [make_episode(0, length=10), make_episode(1, length=30)]
    artifacts = write_lerobot_metadata(
        episodes=episodes, dataset_version_id="v", created_at_iso="t"
    )
    stats = json.loads(next(a for a in artifacts if a.path == "meta/stats.json").data)
    assert stats["length"]["min"] == [10]
    assert stats["length"]["max"] == [30]
    assert stats["length"]["total"] == [40.0]
    assert stats["per_split"]["train"]["episodes"] == 2


def test_per_episode_parquet_layout_and_content() -> None:
    """v2.1 布局：每 Episode 一个 parquet，只含 episode 级列，行数=1。"""
    episodes = [make_episode(0), make_episode(1, split="validation")]
    artifacts = write_lerobot_metadata(
        episodes=episodes, dataset_version_id="v", created_at_iso="t"
    )
    parquet_artifacts = [a for a in artifacts if a.path.startswith("data/chunk-")]
    assert len(parquet_artifacts) == 2
    assert {a.path for a in parquet_artifacts} == {
        "data/chunk-000/episode_000000.parquet",
        "data/chunk-000/episode_000001.parquet",
    }
    for artifact in parquet_artifacts:
        columns = read_parquet_row(artifact.data)
        assert set(columns) == {"episode_index", "task_index", "length", "split"}
        assert len(columns["episode_index"]) == 1  # 元数据行，不是帧数


def test_chunking_crosses_directory_at_episodes_per_chunk() -> None:
    """episode_index 越过 EPISODES_PER_CHUNK 时进入 chunk-001（chunk 归属来自显式序号）。"""
    episodes = [
        make_episode(0, length=1),
        make_episode(1, length=1),
    ]
    episodes.append(
        {
            "episode_id": f"00000000-0000-0000-0000-{EPISODES_PER_CHUNK:012d}",
            "episode_index": EPISODES_PER_CHUNK,
            "episode_version": 1,
            "task": "t",
            "length": 1,
            "split": "train",
        }
    )
    artifacts = write_lerobot_metadata(
        episodes=episodes, dataset_version_id="v", created_at_iso="t"
    )
    paths = {a.path for a in artifacts if a.path.startswith("data/")}
    assert "data/chunk-000/episode_000000.parquet" in paths
    assert "data/chunk-001/episode_001000.parquet" in paths


# ---- 诚实边界 ----


def test_videos_directory_is_not_produced() -> None:
    """媒体未按 LeRobot 布局落盘，绝不虚构 videos/ 产物。"""
    artifacts = write_lerobot_metadata(
        episodes=make_episodes(1), dataset_version_id="v", created_at_iso="t"
    )
    assert not any(a.path.startswith("videos/") for a in artifacts)


def test_empty_episodes_rejected() -> None:
    with pytest.raises(ValueError, match="不得为空"):
        write_lerobot_metadata(episodes=[], dataset_version_id="v", created_at_iso="t")


def test_required_paths_helper() -> None:
    assert required_lerobot_paths([]) == {"meta/info.json", "meta/tasks.jsonl", "meta/stats.json"}
