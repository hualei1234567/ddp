"""HDF5 只读结构探测（DDP-V21-N3-006）。

**样例来源**：全部由官方 ``h5py`` 3.16.0 的 writer API 在测试运行时生成，
属于合成数据，不包含任何客户/生产数据，也不下载外部文件。

覆盖：

- 正常嵌套组/数据集/属性：路径、形状、dtype、压缩、分块可核对；
- 合法空文件按 0 数据集报告，不当作失败；
- 外部链接 / 软链接显式标记且**不跟随**，不把不存在的目标伪装成缺失失败；
- 非 HDF5、截断、非普通文件、路径不存在；
- 数据集数、属性数、属性文本长度、读取字节超限显式失败；
- 错误信息不带绝对路径，返回摘要不含数据值；
- 不注册进 AdapterRegistry（原因见模块 docstring）。
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
from ddp_data import hdf5 as hdf5_module
from ddp_data.adapters import build_default_registry
from ddp_data.hdf5 import (
    DEFAULT_HDF5_ADAPTER,
    Hdf5InputError,
    Hdf5MetadataError,
    Hdf5MetadataLimitError,
    Hdf5UnavailableError,
    NotHdf5Error,
    detect_hdf5,
    read_hdf5_structure,
)

_SIGNATURE = b"\x89HDF\r\n\x1a\n"


def _build_hdf5(path: Path, *, empty: bool = False) -> Path:
    with h5py.File(path, "w") as handle:
        handle.attrs["ddp_schema"] = "ddp.synthetic/1.0.0"
        if empty:
            return path
        images = handle.create_group("observations/images")
        camera = images.create_dataset(
            "cam_high", shape=(4, 6), dtype="uint8", chunks=(2, 6), compression="gzip"
        )
        camera.attrs["fps"] = np.int64(30)
        camera.attrs["label"] = "front"
        handle.create_dataset("action", data=np.zeros((4, 7), dtype="float32"))
        handle.create_dataset("language", data=np.array([1, 2, 3], dtype="int16"))
        handle.attrs["nested"] = np.array([1.5, 2.5], dtype="float64")
    return path


# --- 正常样例 ---------------------------------------------------------------


def test_detect_requires_signature(tmp_path: Path) -> None:
    raw = _build_hdf5(tmp_path / "sample.h5").read_bytes()

    assert detect_hdf5(raw) is True
    assert detect_hdf5(raw[:4]) is False
    assert detect_hdf5(b"not an hdf5 file") is False


def test_detect_accepts_user_block_prefix(tmp_path: Path) -> None:
    """HDF5 允许 512 字节以上 user block，签名不在偏移 0。"""
    path = tmp_path / "userblock.h5"
    with h5py.File(path, "w", userblock_size=1024) as handle:
        handle.create_dataset("x", data=np.zeros(2))
    raw = path.read_bytes()
    assert raw[:8] != _SIGNATURE
    assert detect_hdf5(raw) is True


def test_nested_structure_is_bounded_and_explicit(tmp_path: Path) -> None:
    structure = read_hdf5_structure(_build_hdf5(tmp_path / "nested.h5"))

    # 根 1 + 数组属性 1 + 相机 fps/label 2 = 4
    assert structure.attribute_count == 4
    assert structure.dataset_count == 3
    assert structure.group_count == 2
    assert structure.truncated is False

    datasets = {item.path: item for item in structure.datasets}
    camera = datasets["/observations/images/cam_high"]
    assert camera.shape == (4, 6)
    assert camera.dtype == "uint8"
    assert camera.compression == "gzip"
    assert camera.chunks == (2, 6)
    assert camera.attributes == ("fps", "label")

    assert datasets["/action"].shape == (4, 7)
    assert datasets["/action"].dtype == "float32"
    assert datasets["/action"].compression is None
    assert datasets["/language"].dtype == "int16"

    groups = {item.path for item in structure.groups}
    assert groups == {"/observations", "/observations/images"}


def test_links_are_reported_but_not_followed(tmp_path: Path) -> None:
    path = tmp_path / "links.h5"
    with h5py.File(path, "w") as handle:
        handle.create_dataset("real", data=np.zeros(2))
        handle["ext"] = h5py.ExternalLink("missing-target.h5", "/data")
        handle["soft"] = h5py.SoftLink("/real")

    structure = read_hdf5_structure(path)

    links = {item.path: item for item in structure.links}
    assert links["/ext"].link_type == "external"
    assert links["/ext"].target == "missing-target.h5:/data"
    assert links["/soft"].link_type == "soft"
    assert links["/soft"].target == "/real"
    # 不跟随：软链接不会把 real 再计一次，外部链接也不会因目标缺失而失败。
    assert [item.path for item in structure.datasets] == ["/real"]


def test_empty_file_is_zero_datasets_not_failure(tmp_path: Path) -> None:
    structure = read_hdf5_structure(_build_hdf5(tmp_path / "empty.h5", empty=True))

    assert structure.dataset_count == 0
    assert structure.group_count == 0
    assert structure.datasets == ()
    assert structure.groups == ()
    assert structure.attribute_count == 1


def test_attribute_values_are_not_exposed(tmp_path: Path) -> None:
    structure = read_hdf5_structure(_build_hdf5(tmp_path / "attrs.h5"))
    payload = repr(structure)
    assert "front" not in payload
    assert "1.5" not in payload


# --- 失败路径 ---------------------------------------------------------------


def test_missing_path_raises_input_error(tmp_path: Path) -> None:
    with pytest.raises(Hdf5InputError):
        read_hdf5_structure(tmp_path / "absent.h5")


def test_directory_raises_input_error(tmp_path: Path) -> None:
    with pytest.raises(Hdf5InputError):
        read_hdf5_structure(tmp_path)


def test_non_hdf5_raises_not_hdf5(tmp_path: Path) -> None:
    path = tmp_path / "fake.h5"
    path.write_bytes(b"this is not hdf5 at all" * 4)
    with pytest.raises(NotHdf5Error):
        read_hdf5_structure(path)


def test_truncated_file_raises_not_hdf5(tmp_path: Path) -> None:
    raw = _build_hdf5(tmp_path / "full.h5").read_bytes()
    path = tmp_path / "truncated.h5"
    path.write_bytes(raw[: len(raw) // 2])
    with pytest.raises((NotHdf5Error, Hdf5MetadataError)):
        read_hdf5_structure(path)


def test_dataset_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "many.h5"
    with h5py.File(path, "w") as handle:
        for index in range(6):
            handle.create_dataset(f"d{index}", data=np.zeros(1))
    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_structure(path, max_datasets=5)


def test_attribute_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "manyattrs.h5"
    with h5py.File(path, "w") as handle:
        for index in range(6):
            handle.attrs[f"a{index}"] = index
    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_structure(path, max_attributes=5)


def test_attribute_name_length_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "longattrname.h5"
    with h5py.File(path, "w") as handle:
        handle.attrs["a" * 64] = 1
    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_structure(path, max_text_length=16)


def test_long_attribute_value_is_not_read_as_text(tmp_path: Path) -> None:
    """属性**值**不进入摘要，因此超长字符串值不应触发文本上限。"""
    path = tmp_path / "longattrval.h5"
    with h5py.File(path, "w") as handle:
        handle.attrs["blob"] = "x" * 64
    structure = read_hdf5_structure(path, max_text_length=16)
    assert structure.datasets == ()
    assert "x" * 8 not in repr(structure)


def test_bytes_read_limit_is_enforced(tmp_path: Path) -> None:
    path = _build_hdf5(tmp_path / "big.h5")
    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_structure(path, max_metadata_bytes=8)


def test_error_messages_do_not_leak_absolute_path(tmp_path: Path) -> None:
    path = tmp_path / "secret-dir-name" / "absent.h5"
    with pytest.raises(Hdf5InputError) as excinfo:
        read_hdf5_structure(path)
    assert str(tmp_path) not in str(excinfo.value)


def test_sdk_unavailable_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hdf5_module.metadata, "_h5py", None)
    with pytest.raises(Hdf5UnavailableError):
        read_hdf5_structure(_build_hdf5(tmp_path / "x.h5"))


# --- 契约边界 ---------------------------------------------------------------


def test_group_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "manygroups.h5"
    with h5py.File(path, "w") as handle:
        for index in range(4):
            handle.create_group(f"g{index}")
    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_structure(path, max_groups=3)


def test_as_metadata_is_json_serialisable_and_bounded(tmp_path: Path) -> None:
    import json

    structure = read_hdf5_structure(_build_hdf5(tmp_path / "serialise.h5"))
    payload = structure.as_metadata()
    encoded = json.dumps(payload)
    assert payload["dataset_count"] == structure.dataset_count
    assert payload["adapter_id"] == "hdf5.structure_readonly"
    assert "front" not in encoded
    assert "1.5" not in encoded


def test_deeply_nested_groups_do_not_recurse(tmp_path: Path) -> None:
    """深层嵌套在链接遍历中必须用显式栈，不能撞 RecursionError 绕过上限。"""
    path = tmp_path / "deep.h5"
    depth = 300
    with h5py.File(path, "w") as handle:
        current = handle
        for index in range(depth):
            current = current.create_group(f"level{index}")
        current.create_dataset("leaf", data=np.zeros(2))
        handle["link_at_top"] = h5py.ExternalLink("nowhere.h5", "/x")

    structure = read_hdf5_structure(path, max_groups=depth + 10, max_datasets=10)

    assert structure.group_count == depth
    assert [item.path for item in structure.datasets] == [
        "/" + "/".join(f"level{i}" for i in range(depth)) + "/leaf"
    ]
    assert [item.link_type for item in structure.links] == ["external"]


def test_link_traversal_past_group_budget_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "budget.h5"
    with h5py.File(path, "w") as handle:
        current = handle
        for index in range(10):
            current = current.create_group(f"g{index}")

    with pytest.raises(Hdf5MetadataLimitError):
        read_hdf5_structure(path, max_groups=3)


def test_adapter_is_not_registered_in_episode_registry() -> None:
    registry = build_default_registry()
    assert "hdf5.structure_readonly" not in {spec.adapter_id for spec in registry.list_specs()}


def test_adapter_entrypoint_is_stable(tmp_path: Path) -> None:
    structure = DEFAULT_HDF5_ADAPTER.inspect(_build_hdf5(tmp_path / "stable.h5"))
    assert structure.adapter_id == DEFAULT_HDF5_ADAPTER.adapter_id
    assert structure.adapter_version == DEFAULT_HDF5_ADAPTER.version
    # 签名在偏移 0 即合法 HDF5；detect 只做签名判定，不冒充结构校验。
    assert DEFAULT_HDF5_ADAPTER.detect(_SIGNATURE + b"\x00" * 16) is True
    assert DEFAULT_HDF5_ADAPTER.detect(b"\x89HDF\r\n\x1a") is False
