"""ROS Bag（rosbag2 / rosbag1）只读结构探测（DDP-V21-N3-007）。

**样例来源**：rosbag2 样例由官方 ``rosbags`` 0.11.9 的 writer API 在测试运行时
生成（SQLite3 存储），属于合成数据；另有一条用例使用固定 revision 的 foxglove/mcap
公开样例（见 ``tests/fixtures/public/rosbag/``）。不包含任何客户/生产数据。

覆盖：

- rosbag2：topic/msgtype/消息数/起止时间/时长/连接数可核对，不读取消息体；
- 空 bag 按 0 消息报告，不当作失败；
- rosbag1：同一结构语义（topic/msgtype/消息数），数据源是 ``.bag`` 索引；
- 非 bag、截断、缺路径、非普通文件；
- topic 数、消息数配额、文本长度、SDK 缺失显式失败；
- 错误信息不带绝对路径；返回摘要不含消息体；
- 不注册 AdapterRegistry（原因见模块 docstring）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from ddp_data import rosbag as rosbag_module
from ddp_data.adapters import build_default_registry
from ddp_data.rosbag import (
    DEFAULT_ROSBAG_ADAPTER,
    RosbagInputError,
    RosbagMetadataError,
    RosbagMetadataLimitError,
    RosbagUnavailableError,
    UnsupportedRosbagError,
    detect_rosbag1,
    detect_rosbag2,
    read_rosbag_structure,
)
from rosbags.rosbag1 import Writer as Writer1
from rosbags.rosbag2 import StoragePlugin
from rosbags.rosbag2 import Writer as Writer2
from rosbags.typesys import Stores, get_typestore

_TYPESTORE = get_typestore(Stores.ROS2_HUMBLE)
_MSG = _TYPESTORE.types["std_msgs/msg/String"]


def _build_rosbag2(path: Path, *, empty: bool = False, version: int = 8) -> Path:
    with Writer2(path, version=version, storage_plugin=StoragePlugin.SQLITE3) as writer:
        if empty:
            return path
        camera = writer.add_connection(
            "/camera/front",
            _MSG.__msgtype__,
            typestore=_TYPESTORE,
        )
        imu = writer.add_connection("/imu", _MSG.__msgtype__, typestore=_TYPESTORE)
        for connection, log_time in (
            (camera, 1_000_000_000),
            (imu, 1_200_000_000),
            (camera, 1_500_000_000),
        ):
            payload = _TYPESTORE.serialize_cdr(_MSG(data=f"m{log_time}"), _MSG.__msgtype__)
            writer.write(connection, log_time, payload)
    return path


def _build_rosbag1(path: Path) -> Path:
    with Writer1(path) as writer:
        connection = writer.add_connection("/camera/front", _MSG.__msgtype__, typestore=_TYPESTORE)
        for log_time in (2_000_000_000, 2_500_000_000):
            payload = _TYPESTORE.serialize_ros1(_MSG(data=f"m{log_time}"), _MSG.__msgtype__)
            writer.write(connection, log_time, payload)
    return path


# --- 正常样例 ---------------------------------------------------------------


def test_detect_rosbag2_by_directory_layout(tmp_path: Path) -> None:
    bag = _build_rosbag2(tmp_path / "bag2")

    assert detect_rosbag2(bag) is True
    assert detect_rosbag2(tmp_path) is False
    assert detect_rosbag1(bag) is False


def test_rosbag2_structure_is_read_without_message_bodies(tmp_path: Path) -> None:
    structure = read_rosbag_structure(_build_rosbag2(tmp_path / "bag2"))

    assert structure.format == "rosbag2"
    assert structure.topic_count == 2
    assert structure.message_count == 3
    assert structure.start_time_ns == 1_000_000_000
    assert structure.end_time_ns == 1_500_000_000
    assert structure.duration_ns == 500_000_000

    topics = {item.topic: item for item in structure.topics}
    assert topics["/camera/front"].message_count == 2
    assert topics["/camera/front"].msgtype == _MSG.__msgtype__
    assert topics["/imu"].message_count == 1
    # 消息体绝不进入摘要。
    assert "m1000000000" not in repr(structure)


def test_empty_rosbag2_is_zero_messages_not_failure(tmp_path: Path) -> None:
    structure = read_rosbag_structure(_build_rosbag2(tmp_path / "empty2", empty=True))

    assert structure.format == "rosbag2"
    assert structure.topic_count == 0
    assert structure.message_count == 0
    assert structure.topics == ()
    assert structure.start_time_ns is None
    assert structure.duration_ns is None


def test_rosbag2_v9_split_storage_is_supported(tmp_path: Path) -> None:
    structure = read_rosbag_structure(_build_rosbag2(tmp_path / "bag2v9", version=9))
    assert structure.message_count == 3
    assert structure.format == "rosbag2"


def test_rosbag1_structure_is_read(tmp_path: Path) -> None:
    structure = read_rosbag_structure(_build_rosbag1(tmp_path / "legacy.bag"))

    assert structure.format == "rosbag1"
    assert structure.topic_count == 1
    assert structure.message_count == 2
    assert structure.start_time_ns == 2_000_000_000
    assert structure.end_time_ns == 2_500_000_000
    assert structure.topics[0].topic == "/camera/front"


def test_as_metadata_is_json_serialisable(tmp_path: Path) -> None:
    import json

    structure = read_rosbag_structure(_build_rosbag2(tmp_path / "serial.h5"))
    payload = structure.as_metadata()
    assert payload["format"] == "rosbag2"
    assert json.dumps(payload)
    assert "m1000000000" not in json.dumps(payload)


# --- 失败路径 ---------------------------------------------------------------


def test_missing_path_raises_input_error(tmp_path: Path) -> None:
    with pytest.raises(RosbagInputError):
        read_rosbag_structure(tmp_path / "absent")


def test_plain_file_raises_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "notabag.bin"
    path.write_bytes(b"definitely not a rosbag" * 8)
    with pytest.raises(UnsupportedRosbagError):
        read_rosbag_structure(path)


def test_empty_directory_raises_unsupported(tmp_path: Path) -> None:
    empty = tmp_path / "emptydir"
    empty.mkdir()
    with pytest.raises(UnsupportedRosbagError):
        read_rosbag_structure(empty)


def test_fifo_is_rejected_without_reading(tmp_path: Path) -> None:
    fifo = tmp_path / "stream.bag"
    os.mkfifo(fifo)
    with pytest.raises(RosbagInputError, match="普通文件或目录"):
        read_rosbag_structure(fifo)


def test_truncated_rosbag1_raises_metadata_error(tmp_path: Path) -> None:
    path = tmp_path / "truncated.bag"
    path.write_bytes(b"#ROSBAG V2.0\n" + b"broken-index")
    with pytest.raises(RosbagMetadataError):
        read_rosbag_structure(path)


def test_rosbag2_metadata_byte_limit_is_enforced(tmp_path: Path) -> None:
    bag = _build_rosbag2(tmp_path / "bag2")
    metadata_size = (bag / "metadata.yaml").stat().st_size
    with pytest.raises(RosbagMetadataLimitError):
        read_rosbag_structure(bag, max_metadata_bytes=metadata_size - 1)


def test_non_positive_limits_are_rejected(tmp_path: Path) -> None:
    bag = _build_rosbag2(tmp_path / "bag2")
    with pytest.raises(RosbagInputError):
        read_rosbag_structure(bag, max_metadata_bytes=0)


def test_file_compressed_rosbag2_fails_before_storage_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bag = _build_rosbag2(tmp_path / "bag2")
    metadata = bag / "metadata.yaml"
    metadata.write_text(
        metadata.read_text().replace("compression_mode: ''", "compression_mode: file")
    )

    opened = False

    def forbidden_open(*args: object, **kwargs: object) -> None:
        nonlocal opened
        opened = True
        raise AssertionError("不应打开压缩消息存储")

    monkeypatch.setattr(rosbag_module.metadata._Rosbag2Reader, "open", forbidden_open)
    with pytest.raises(RosbagMetadataError, match="file 级压缩"):
        read_rosbag_structure(bag)
    assert opened is False


def test_topic_limit_is_enforced(tmp_path: Path) -> None:
    with pytest.raises(RosbagMetadataLimitError):
        read_rosbag_structure(_build_rosbag2(tmp_path / "bag2"), max_topics=1)


def test_message_limit_is_enforced(tmp_path: Path) -> None:
    with pytest.raises(RosbagMetadataLimitError):
        read_rosbag_structure(_build_rosbag2(tmp_path / "bag2"), max_messages=2)


def test_text_length_limit_is_enforced(tmp_path: Path) -> None:
    with pytest.raises(RosbagMetadataLimitError):
        read_rosbag_structure(_build_rosbag2(tmp_path / "bag2"), max_text_length=4)


def test_error_messages_do_not_leak_absolute_path(tmp_path: Path) -> None:
    secret = tmp_path / "secret-dir-name" / "absent"
    with pytest.raises(RosbagInputError) as excinfo:
        read_rosbag_structure(secret)
    assert str(tmp_path) not in str(excinfo.value)


def test_sdk_unavailable_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bag = _build_rosbag2(tmp_path / "bag2")
    monkeypatch.setattr(rosbag_module.metadata, "_rosbags", None)
    with pytest.raises(RosbagUnavailableError):
        read_rosbag_structure(bag)


# --- 契约边界 ---------------------------------------------------------------


def test_adapter_is_not_registered_in_episode_registry() -> None:
    registry = build_default_registry()
    assert "rosbag.structure_readonly" not in {spec.adapter_id for spec in registry.list_specs()}


def test_adapter_entrypoint_is_stable(tmp_path: Path) -> None:
    structure = DEFAULT_ROSBAG_ADAPTER.inspect(_build_rosbag2(tmp_path / "stable"))
    assert structure.adapter_id == DEFAULT_ROSBAG_ADAPTER.adapter_id
    assert structure.adapter_version == DEFAULT_ROSBAG_ADAPTER.version
