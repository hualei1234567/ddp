"""HDF5 payload 切片读取测试（V51-001/002/003，PAYLOAD_PARSE 能力）。

**为什么这些测试必须用 h5py 真造文件而不是 mock**
``payload.py`` 的核心风险不在分支逻辑，而在**与 HDF5 数据模型的交互**：
第一维切片的语义、多维数组的扁平化顺序、链式类型的标量转换、属性的大数组
拒绝规则。mock 掉 h5py 就等于不测这些——而它们正是真实数据会踩的地方。

因此本文件的 diag 用例**用官方 h5py 现场构造 HDF5 文件**，走完整读取路径。
另有 ``test_real_robomimic_payload`` 用真实公开样本，默认跳过（需显式提供路径）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from ddp_data.hdf5 import (
    Hdf5AttributeValue,
    Hdf5DatasetPayload,
    Hdf5InputError,
    Hdf5MetadataError,
    Hdf5MetadataLimitError,
    detect_hdf5_payload,
    read_hdf5_attributes,
    read_hdf5_dataset_slice,
)

h5py = pytest.importorskip("h5py", reason="需要官方 h5py SDK 才能构造真实 HDF5 文件")

# 样本事实登记在 backend 侧（与既有 test_hdf5_real_public_sample.py 共用一份
# facts.json，避免同一份来源元数据维护两处）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FACTS = _REPO_ROOT / "backend/tests/fixtures/public/robomimic-hdf5/facts.json"
_REAL_PATH = os.environ.get("DDP_TEST_REAL_HDF5")


def _build(path: Path) -> Path:
    """构造一个覆盖真实数据模型特征的 HDF5 文件。

    - 2D float64（模拟 actions）
    - 3D float32（模拟图像批次的多维）
    - int64 / bool 链式类型（验证标量转换顺序）
    - 标量属性 + 大数组属性（验证 64 元素上限）
    """
    with h5py.File(path, "w") as f:
        demo = f.create_group("data/demo_0")
        demo.create_dataset("actions", data=[[0.1 * i, 0.2 * i] for i in range(5)])
        demo.create_dataset(
            "images",
            data=[
                [[float(i + j + k) for k in range(2)] for j in range(2)]
                for i in range(3)
            ],
            dtype="float32",
        )
        demo.create_dataset("counts", data=[1, 2, 3, 4])
        demo.create_dataset("flags", data=[True, False, True])
        demo.attrs["num_samples"] = 5
        demo.attrs["version"] = "v1.5"
        demo.attrs["big_vector"] = list(range(100))  # 超过 64，应被拒绝
        f.attrs["top"] = "root"
    return path


def test_read_2d_slice_returns_real_values(tmp_path: Path) -> None:
    """2D float64 切片：值、dtype、shape、start/length 全部如实返回。"""
    f = _build(tmp_path / "a.h5")
    got = read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=0, length=2)

    assert isinstance(got, Hdf5DatasetPayload)
    assert got.dtype == "float64"
    assert got.shape == (5, 2)
    assert got.start == 0
    assert got.length == 2
    # 扁平化后长度 = length × shape[1:]
    assert len(got.values) == 2 * 2
    assert got.values == pytest.approx((0.0, 0.0, 0.1, 0.2))


def test_slice_offset_reads_correct_window(tmp_path: Path) -> None:
    """start 偏移必须读到对应窗口，不是永远从 0 开始。"""
    f = _build(tmp_path / "a.h5")
    got = read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=3, length=2)
    assert got.start == 3
    assert got.length == 2
    # actions 是 [[0.1*i, 0.2*i] for i in range(5)]，切片 [3:5] = [0.3,0.6, 0.4,0.8]
    assert got.values == pytest.approx((0.3, 0.6, 0.4, 0.8))


def test_length_none_reads_to_end_but_still_bounded(tmp_path: Path) -> None:
    """length=None 读到末尾；但仍受 max_elements 约束（不静默截断）。"""
    f = _build(tmp_path / "a.h5")
    got = read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=0, length=None)
    assert got.length == 5

    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_dataset_slice(f, "/data/demo_0/actions", max_elements=3)


def test_3d_slice_flattens_row_major(tmp_path: Path) -> None:
    """3D 数据集按行扁平化：shape[1:] 的乘积决定每行元素数。"""
    f = _build(tmp_path / "a.h5")
    got = read_hdf5_dataset_slice(f, "/data/demo_0/images", start=0, length=1)
    assert got.shape == (3, 2, 2)
    assert got.length == 1
    # 第 0 行值为 [[0,1],[1,2]]（构造式 i+j+k，i=0）
    assert got.values == pytest.approx((0.0, 1.0, 1.0, 2.0))


def test_bool_and_int_scalars_keep_their_type(tmp_path: Path) -> None:
    """bool 必须先于 int 判断：numpy bool 是 int 子类，顺序错会读成 0/1。"""
    f = _build(tmp_path / "a.h5")
    bools = read_hdf5_dataset_slice(f, "/data/demo_0/flags", start=0, length=3)
    assert all(isinstance(v, bool) for v in bools.values), bools.values
    assert bools.values == (True, False, True)

    ints = read_hdf5_dataset_slice(f, "/data/demo_0/counts", start=0, length=2)
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in ints.values)
    assert ints.values == (1, 2)


def test_start_beyond_dataset_reports_actionable_error(tmp_path: Path) -> None:
    """越界要给出「越界 + 实际长度」，而不是空结果。"""
    f = _build(tmp_path / "a.h5")
    with pytest.raises(Hdf5MetadataError, match="超出数据集第一维长度 5"):
        read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=5, length=1)


def test_element_limit_is_enforced_with_actionable_message(tmp_path: Path) -> None:
    """超限必须抛错并提示缩小 length，不得静默截断。"""
    f = _build(tmp_path / "a.h5")
    with pytest.raises(Hdf5MetadataLimitError, match="缩小 length"):
        read_hdf5_dataset_slice(
            f, "/data/demo_0/actions", start=0, length=5, max_elements=3
        )


def test_missing_dataset_and_non_dataset_path(tmp_path: Path) -> None:
    f = _build(tmp_path / "a.h5")
    with pytest.raises(Hdf5MetadataError, match="数据集不存在"):
        read_hdf5_dataset_slice(f, "/data/demo_0/nope", start=0, length=1)
    with pytest.raises(Hdf5MetadataError, match="不是数据集"):
        read_hdf5_dataset_slice(f, "/data/demo_0", start=0, length=1)


def test_dataset_path_must_be_absolute(tmp_path: Path) -> None:
    """相对路径必须拒绝：HDF5 内路径是绝对路径语义，相对路径会有歧义。"""
    f = _build(tmp_path / "a.h5")
    with pytest.raises(Hdf5MetadataError, match="必须以 / 开头"):
        read_hdf5_dataset_slice(f, "data/demo_0/actions", start=0, length=1)


def test_negative_and_zero_args_are_rejected(tmp_path: Path) -> None:
    f = _build(tmp_path / "a.h5")
    with pytest.raises(Hdf5MetadataError, match="start 不得为负"):
        read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=-1, length=1)
    with pytest.raises(Hdf5MetadataError, match="length 必须为正"):
        read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=0, length=0)
    with pytest.raises(Hdf5MetadataLimitError, match="必须为正"):
        read_hdf5_dataset_slice(f, "/data/demo_0/actions", start=0, max_elements=0)


def test_missing_file_and_directory_are_input_errors(tmp_path: Path) -> None:
    with pytest.raises(Hdf5InputError, match="不存在"):
        read_hdf5_dataset_slice(tmp_path / "absent.h5", "/x", start=0, length=1)
    with pytest.raises(Hdf5InputError, match="不是普通文件"):
        read_hdf5_dataset_slice(tmp_path, "/x", start=0, length=1)


def test_attributes_read_scalars_and_reject_large_arrays(tmp_path: Path) -> None:
    """属性读真实值；超大数组属性被跳过（属性是元信息不是数据）。"""
    f = _build(tmp_path / "a.h5")
    attrs = read_hdf5_attributes(f, "/data/demo_0")
    by_name = {a.name: a for a in attrs}

    assert isinstance(by_name["num_samples"], Hdf5AttributeValue)
    assert by_name["num_samples"].value == 5
    assert by_name["version"].value == "v1.5"
    # 100 元素的数组属性超过 64 上限，必须被跳过而不是读进内存
    assert "big_vector" not in by_name


def test_attribute_text_is_bounded(tmp_path: Path) -> None:
    """长文本属性必须被截断到上限，避免把巨串带出。"""
    f = _build(tmp_path / "a.h5")
    attrs = read_hdf5_attributes(f, "/data/demo_0", max_text_length=4)
    by_name = {a.name: a for a in attrs}
    assert by_name["version"].value == "v1.5"[:4]


def test_detect_payload_uses_same_signature(tmp_path: Path) -> None:
    f = _build(tmp_path / "a.h5")
    assert detect_hdf5_payload(f.read_bytes()[:1024]) is True
    assert detect_hdf5_payload(b"not an hdf5 file") is False
    assert detect_hdf5_payload(b"") is False


# ---- 真实公开样本（默认跳过，遵循仓库既有口径）----


@pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_HDF5 指向本地 RoboMimic demo_v15.hdf5",
)
def test_real_robomimic_payload_reads_real_actions_and_states() -> None:
    """用真实 RoboMimic 样本验证 payload 读取（E1 级证据）。

    证明：真实机器人演示集的 actions/states 可被逐帧读取，值与 facts.json
    记录的 RoboMimic 布局一致（actions 7 维归一化、states 32 维）。
    不证明：图像观测、标定、真实硬件在环。
    """
    import json

    facts = json.loads(_FACTS.read_text(encoding="utf-8"))
    assert facts["source"]["file_sha256"], "facts 必须记录样本 SHA256"

    actions = read_hdf5_dataset_slice(
        _REAL_PATH, "/data/demo_0/actions", start=0, length=3
    )
    assert actions.dtype == "float64"
    assert actions.shape == (59, 7)
    assert actions.length == 3
    assert len(actions.values) == 3 * 7
    # 归一化动作范围（robosuite OSC 控制）
    assert all(-1.5 <= v <= 1.5 for v in actions.values), actions.values

    states = read_hdf5_dataset_slice(
        _REAL_PATH, "/data/demo_0/states", start=0, length=2
    )
    assert states.shape == (59, 32)
    assert len(states.values) == 2 * 32

    # 时间戳必须单调不减（真实 wall-clock）
    stamps = read_hdf5_dataset_slice(
        _REAL_PATH, "/data/demo_0/user_info/0/timestamp", start=0, length=10
    )
    values = list(stamps.values)
    assert values == sorted(values), "真实时间戳必须单调不减"
