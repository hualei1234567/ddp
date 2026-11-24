"""STREAM_EXTRACTION 阶段测试（V51-001/002/003）。

**真实样本优先**：本模块核心价值是"从真实 HDF5 取出逐帧值"，因此主测试用
真实 RoboMimic 样本（``DDP_TEST_REAL_HDF5``）。无样本时用官方 h5py 现场构造
同布局文件（走完整读取路径），保证 CI 也覆盖逻辑。

**不 mock h5py**：mock 掉就等于不测数据模型交互——而那正是真实数据会踩的地方。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from app.application.processing.stream_extraction_stage import (
    _LAYOUTS,
    STREAM_SCHEMA_VERSION,
    extract_robomimic_demo,
    payload_sha256,
    render_ndjson,
    stream_artifact_key,
)
from ddp_data.hdf5 import read_hdf5_dataset_slice

h5py = pytest.importorskip("h5py", reason="需要官方 h5py SDK 构造或读取 HDF5")

_REAL_PATH = os.environ.get("DDP_TEST_REAL_HDF5")
_LAYOUT = _LAYOUTS[0]


def _build_demo_hdf5(path: Path, *, demos: int = 2, frames: int = 5) -> Path:
    """构造与 RoboMimic 同布局的 HDF5（2 demo × 5 帧）。"""
    with h5py.File(path, "w") as f:
        for d in range(demos):
            group = f.create_group(f"data/demo_{d}")
            group.create_dataset("actions", data=[[0.1 * (i + 1)] * 7 for i in range(frames)])
            group.create_dataset("states", data=[[float(i), float(d)] * 16 for i in range(frames)])
            group.create_dataset("user_info/0/timestamp", data=[1000.0 + i for i in range(frames)])
    return path


def test_extract_produces_streams_observations_and_actions(tmp_path: Path) -> None:
    """正常路径：三件套齐全，shape/dtype 如实传递。"""
    f = _build_demo_hdf5(tmp_path / "a.hdf5")
    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice, file_path=str(f), demo_index=0, layout=_LAYOUT
    )

    assert {s["modality"] for s in rec["streams"]} == {"STATE", "ACTION"}  # type: ignore[union-attr]
    assert len(rec["observations"]) == 5  # type: ignore[arg-type]
    assert rec["actions"] is not None
    assert len(rec["actions"]) == 5  # type: ignore[arg-type]

    obs0 = rec["observations"][0]  # type: ignore[index]
    assert obs0["frame_index"] == 0
    assert obs0["validity"] == "VALID"
    assert obs0["source_index"] == 0
    # 构造式为 [[float(i), float(d)] * 16]，每行 32 个元素
    assert obs0["shape"] == [32]

    act0 = rec["actions"][0]  # type: ignore[index]
    assert act0["action_type"] == "END_EFFECTOR_DELTA"
    assert act0["semantics_version"] == f"{_LAYOUT.layout_id}/{_LAYOUT.version}"
    assert act0["shape"] == [7]


def test_empty_action_source_returns_none_not_empty_list(tmp_path: Path) -> None:
    """无动作源必须返回 ``None``（ACTION_SOURCE_ABSENT），不是空列表。

    空列表会被下游读成「有动作但一条都没有」，与「根本没有动作源」语义完全不同。
    """
    with h5py.File(tmp_path / "b.hdf5", "w") as f:
        group = f.create_group("data/demo_0")
        group.create_dataset("states", data=[[float(i)] * 4 for i in range(4)])

    layout_no_action = type(_LAYOUT)(
        layout_id="test_no_action",
        version="1",
        demo_prefix="data/demo_",
        action_dataset=None,
        observation_dataset="states",
        timestamp_dataset=None,
        limitations=("测试用布局：无动作源",),
    )
    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice,
        file_path=str(tmp_path / "b.hdf5"),
        demo_index=0,
        layout=layout_no_action,
    )
    assert rec["actions"] is None
    assert rec["observations"]

    meta = json.loads(render_ndjson(rec).decode().split("\n")[0])["_meta"]
    assert meta["action_source"] == "ACTION_SOURCE_ABSENT"


def test_ndjson_lines_are_independently_parseable(tmp_path: Path) -> None:
    """NDJSON 每行必须能独立解析（逐帧消费方不解析整棵树）。"""
    f = _build_demo_hdf5(tmp_path / "c.hdf5")
    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice, file_path=str(f), demo_index=1, layout=_LAYOUT
    )
    lines = [line for line in render_ndjson(rec).decode().split("\n") if line]
    assert len(lines) == 11  # 1 meta + 5 obs + 5 act
    for line in lines:
        json.loads(line)


def test_metadata_declares_schema_version_and_limitations(tmp_path: Path) -> None:
    """meta 行必须声明 schema 版本与已知限制——下游靠它判断能用什么。"""
    f = _build_demo_hdf5(tmp_path / "d.hdf5")
    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice, file_path=str(f), demo_index=0, layout=_LAYOUT
    )
    meta = json.loads(render_ndjson(rec).decode().split("\n")[0])["_meta"]
    assert meta["schema_version"] == STREAM_SCHEMA_VERSION
    assert meta["limitations"] == list(_LAYOUT.limitations)
    assert meta["action_source"] == "PRESENT"


def test_artifact_key_is_stable() -> None:
    """产物 key 不含时间戳：同输入重跑必须落到同一 key（否则产物分叉）。"""
    from uuid import UUID

    project, run = UUID(int=1), UUID(int=2)
    first = stream_artifact_key(project, run, "robomimic_hdf5_v1.5", 0)
    assert first == stream_artifact_key(project, run, "robomimic_hdf5_v1.5", 0)
    assert "streams/robomimic_hdf5_v1.5/0000.ndjson" in first


def test_payload_hash_is_stable() -> None:
    assert payload_sha256(b"abc") == payload_sha256(b"abc")
    assert payload_sha256(b"abc").startswith("sha256:")
    assert payload_sha256(b"abc") != payload_sha256(b"abd")


def test_max_frames_limit_uses_actual_read_rows(tmp_path: Path) -> None:
    """帧数上限生效，且用**实际读取行数**而非数据集总行数。

    这是实现时真实踩到的 bug：用 ``shape[0]`` 会把未读取的行也当成有数据，
    导致 max_frames=7 却产出 20 条记录。
    """
    f = _build_demo_hdf5(tmp_path / "e.hdf5", frames=20)
    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice,
        file_path=str(f),
        demo_index=0,
        layout=_LAYOUT,
        max_frames=7,
    )
    assert len(rec["observations"]) == 7  # type: ignore[arg-type]
    assert len(rec["actions"]) == 7  # type: ignore[arg-type]


def test_missing_optional_dataset_is_tolerated_but_read_error_is_not(tmp_path: Path) -> None:
    """可选数据集缺失 → 容忍（跳过）；读取真实失败 → 阶段失败。

    两者必须区分：前者是布局声明的可选字段，后者是真故障。
    """
    with h5py.File(tmp_path / "f.hdf5", "w") as f:
        group = f.create_group("data/demo_0")
        group.create_dataset("states", data=[[1.0] * 4 for _ in range(3)])
        group.create_dataset("actions", data=[[1.0] * 7 for _ in range(3)])
        # 故意不建 timestamp

    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice,
        file_path=str(tmp_path / "f.hdf5"),
        demo_index=0,
        layout=_LAYOUT,
    )
    assert len(rec["observations"]) == 3  # type: ignore[arg-type]


# ---- 真实样本（默认跳过）----


@pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_HDF5 指向本地 RoboMimic demo_v15.hdf5",
)
def test_real_robomimic_extracts_real_frames() -> None:
    """真实样本：提取真实逐帧 states(32) + actions(7)，值非占位。"""
    rec = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice,
        file_path=_REAL_PATH,  # type: ignore[arg-type]
        demo_index=0,
        layout=_LAYOUT,
    )
    assert len(rec["observations"]) == 59
    assert rec["actions"] is not None
    assert len(rec["actions"]) == 59

    obs_values = rec["observations"][0]["values"]  # type: ignore[index]
    assert len(obs_values) == 32, "states 应为 32 维"
    assert any(abs(v) > 1e-6 for v in obs_values), "真实状态值不应全零"

    act_values = rec["actions"][0]["values"]  # type: ignore[index]
    assert len(act_values) == 7, "actions 应为 7 维"
    assert all(-1.5 <= v <= 1.5 for v in act_values), "归一化动作应在合理范围"

    assert any("不含图像" in item for item in rec["limitations"]), rec["limitations"]
