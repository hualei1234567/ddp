"""LeRobot v2.1 逐帧导出 writer 测试（V51-011）。

**本模块的判定标准**：产出的数据集能被官方 ``lerobot==0.3.3`` Loader **逐帧读取**，
且 ``ds[0]`` 里真的有 ``observation.*`` 与 ``action`` 数据。

**为什么必须用真实 pyarrow 而非 mock**：逐帧 Parquet 的风险在列类型、列序、
时间戳精度与官方 Loader 的读取假设之间——mock 掉 pyarrow 等于不测这些。

官方 Loader 的读取验证在 ``docs/testing/evidence/`` 下另存脚本（需 lerobot 环境）。
本文件覆盖产物的结构正确性与边界，可在普通 CI 跑。
"""

from __future__ import annotations

import json

import pytest

from ddp_data.lerobot.frame_writer import (
    FRAME_SCHEMA_VERSION,
    FRAME_SCOPE,
    build_frame_features,
    build_stats,
    write_frame_episode,
)

pyarrow = pytest.importorskip("pyarrow", reason="逐帧 Parquet 需要 pyarrow")


def _frames(count: int = 5, *, obs_dim: int = 4, act_dim: int = 3) -> list[dict]:
    return [
        {
            "observation": [float(i + d) for d in range(obs_dim)],
            "action": [float(i * 0.1 + d) for d in range(act_dim)],
            "timestamp": i * 0.05,
        }
        for i in range(count)
    ]


# ---- 正常路径 ----


def test_writes_frame_level_parquet_and_info() -> None:
    """产出逐帧 Parquet + info.json，且声明 LOADER_FRAME 而非 METADATA_ONLY。"""
    result = write_frame_episode(
        frames=_frames(5), episode_index=0, fps=20.0, observation_dim=4, action_dim=3
    )

    assert result.frame_count == 5
    assert result.feature_names == ("observation.state", "action", "timestamp")
    assert result.parquet_bytes[:4] == b"PAR1"  # 合法 Parquet 魔数

    info = json.loads(result.info_json)
    assert info["codebase_version"] == "v2.1"
    # 关键：真实帧数与真实 fps（不是 METADATA_ONLY 的 1 帧 / 标称 1Hz）
    assert info["total_frames"] == 5
    assert info["fps"] == 20.0
    assert info["ddp"]["loadable_scope"] == FRAME_SCOPE
    assert info["ddp"]["frame_level_mapping"] is True
    assert info["ddp"]["schema_version"] == FRAME_SCHEMA_VERSION
    assert info["ddp"]["fps_source"] == "MEASURED_FROM_FRAMES"
    # features 必须含 observation 与 action（这是"逐帧"的定义）
    assert "observation.state" in info["features"]
    assert "action" in info["features"]


def test_parquet_columns_are_expanded_by_dimension() -> None:
    """逐帧 Parquet 的 observation/action 必须按维度展开成独立列。

    官方 Loader 读取时按 ``<feature>[<dim>]`` 逐列取值；写成嵌套 list 列会读不出来。
    """
    result = write_frame_episode(
        frames=_frames(3, obs_dim=4, act_dim=3),
        episode_index=0,
        fps=10.0,
        observation_dim=4,
        action_dim=3,
    )
    import io

    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(result.parquet_bytes))
    columns = set(table.column_names)

    # 三元组自洽
    for required in (
        "episode_index",
        "frame_index",
        "index",
        "task_index",
        "timestamp",
    ):
        assert required in columns, f"缺少必需列：{required}"
    # 逐维展开
    for dim in range(4):
        assert f"observation.state[{dim}]" in columns
    for dim in range(3):
        assert f"action[{dim}]" in columns
    assert table.num_rows == 3


def test_video_path_is_template_not_none() -> None:
    """``video_path`` 必须是模板串，不能是 None。

    实测：传 None 会让官方 Loader 在建 split 时失败（即使本数据集无视频）。
    """
    result = write_frame_episode(
        frames=_frames(2), episode_index=0, fps=10.0, observation_dim=4, action_dim=3
    )
    info = json.loads(result.info_json)
    assert isinstance(info["video_path"], str)
    assert "{episode_chunk:03d}" in info["video_path"]
    # data_path 也必须是模板形式
    assert "{episode_index:06d}" in info["data_path"]
    # chunks_size 是必需数字段（缺失会 KeyError）
    assert isinstance(info["chunks_size"], int)


def test_stats_are_generated_for_every_feature() -> None:
    """stats.json 必须覆盖每个数值 feature。

    官方 Loader 会尝试加载 meta/stats.json；缺失会导致构造失败。
    """
    frames = _frames(4, obs_dim=2, act_dim=2)
    observations = [f["observation"] for f in frames]
    actions = [f["action"] for f in frames]
    timestamps = [f["timestamp"] for f in frames]

    stats = build_stats(
        observations=observations, actions=actions, timestamps=timestamps
    )
    assert "timestamp" in stats
    assert "observation.state[0]" in stats
    assert "action[1]" in stats
    for name, values in stats.items():
        assert values["min"] <= values["max"], name
        assert values["count"] == 4, name


# ---- 边界与失败路径（严格校验是对训练数据的保护）----


def test_rejects_empty_frames() -> None:
    """空数据集不能标 FRAME_LEVEL——那会让下游以为有逐帧数据。"""
    with pytest.raises(ValueError, match="至少需要 1 帧"):
        write_frame_episode(
            frames=[], episode_index=0, fps=10.0, observation_dim=4, action_dim=3
        )


def test_rejects_non_positive_fps() -> None:
    """fps 必须为正：官方 check_timestamps_sync 依赖它。"""
    with pytest.raises(ValueError, match="fps 必须为正"):
        write_frame_episode(
            frames=_frames(2), episode_index=0, fps=0.0, observation_dim=4, action_dim=3
        )


def test_rejects_dimension_mismatch() -> None:
    """维度不符必须失败——产出「看起来像逐帧但维度错位」的数据集比失败更糟。"""
    bad = _frames(3)
    bad[1]["observation"] = [1.0, 2.0]  # 期望 4 维
    with pytest.raises(ValueError, match="维度不符"):
        write_frame_episode(
            frames=bad, episode_index=0, fps=10.0, observation_dim=4, action_dim=3
        )


def test_rejects_non_monotonic_timestamps() -> None:
    """时间戳必须严格递增：非单调意味着数据错位，官方 Loader 也会校验。"""
    bad = _frames(4)
    bad[2]["timestamp"] = bad[1]["timestamp"]  # 重复
    with pytest.raises(ValueError, match="必须严格递增"):
        write_frame_episode(
            frames=bad, episode_index=0, fps=10.0, observation_dim=4, action_dim=3
        )


def test_rejects_non_numeric_values() -> None:
    """非数值元素必须拒绝（bool 也不行——它是 int 子类但语义不对）。"""
    bad = _frames(2)
    bad[0]["action"] = [True, 1.0, 2.0]
    with pytest.raises(ValueError, match="非数值"):
        write_frame_episode(
            frames=bad, episode_index=0, fps=10.0, observation_dim=4, action_dim=3
        )


def test_output_is_deterministic() -> None:
    """同输入两次导出必须字节一致（可复现是训练数据的基本要求）。"""
    args = dict(
        frames=_frames(5), episode_index=0, fps=20.0, observation_dim=4, action_dim=3
    )
    first = write_frame_episode(**args)
    second = write_frame_episode(**args)
    assert first.parquet_bytes == second.parquet_bytes
    assert first.info_json == second.info_json
    assert first.source_digest == second.source_digest


def test_feature_declarations_match_parquet() -> None:
    """info.features 的声明必须与 Parquet 实际列一一对应。

    不对应会让 Loader 读不到某列（或读到多余列），是逐帧数据集最常见的坑。
    """
    obs_dim, act_dim = 6, 2
    result = write_frame_episode(
        frames=_frames(3, obs_dim=obs_dim, act_dim=act_dim),
        episode_index=0,
        fps=10.0,
        observation_dim=obs_dim,
        action_dim=act_dim,
    )
    info = json.loads(result.info_json)
    features = info["features"]
    assert features["observation.state"]["shape"] == [obs_dim]
    assert features["action"]["shape"] == [act_dim]
    assert features["timestamp"]["dtype"] == "float32"

    declared = build_frame_features(observation_dim=obs_dim, action_dim=act_dim)
    assert tuple(f.name for f in declared) == result.feature_names
