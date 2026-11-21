"""ROS Bag（rosbag2 / rosbag1）结构只读探测（DDP-V21-N3-007）。

底层复用 ``rosbags`` SDK 的索引/metadata reader；不遍历 ``messages()``，不解码
消息体。topic 数、总消息数和文本长度均有界，超限显式失败。SDK 异常可能包含
绝对路径，因此统一转换为脱敏的平台异常且不保留异常链。
"""

from __future__ import annotations

import os

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from pathlib import Path
from typing import Literal, Protocol

from ddp_data.rosbag.errors import (
    RosbagInputError,
    RosbagMetadataError,
    RosbagMetadataLimitError,
    RosbagUnavailableError,
    UnsupportedRosbagError,
)
from ddp_data.rosbag.models import RosbagStructure, RosbagTopicSummary

try:
    import rosbags as _rosbags
    from rosbags.rosbag1 import Reader as _Rosbag1Reader
    from rosbags.rosbag1 import ReaderError as _Rosbag1ReaderError
    from rosbags.rosbag2 import Reader as _Rosbag2Reader
    from rosbags.rosbag2 import ReaderError as _Rosbag2ReaderError
except ImportError:  # pragma: no cover - 依赖缺失时保留显式错误路径
    _rosbags = None
    _Rosbag1Reader = None
    _Rosbag1ReaderError = None
    _Rosbag2Reader = None
    _Rosbag2ReaderError = None

ROSBAG1_MAGIC = b"#ROSBAG V2.0\n"
ROSBAG2_METADATA = "metadata.yaml"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_TOPICS = 10_000
MAX_MESSAGES = 1_000_000_000
MAX_TEXT_LENGTH = 4096
_DISPLAY_NAME_LIMIT = 128


class _TopicInfo(Protocol):
    msgtype: str
    msgcount: int


class _Reader(Protocol):
    topics: dict[str, _TopicInfo]
    message_count: int
    start_time: int
    end_time: int

    def __enter__(self) -> _Reader: ...

    def __exit__(self, *args: object) -> None: ...


def detect_rosbag1(value: bytes | bytearray | memoryview | os.PathLike[str]) -> bool:
    """检测 rosbag1 魔数；路径只读取固定长度前缀。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).startswith(ROSBAG1_MAGIC)
    try:
        path = Path(value)
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            return handle.read(len(ROSBAG1_MAGIC)) == ROSBAG1_MAGIC
    except (OSError, TypeError, ValueError):
        return False


def detect_rosbag2(path: str | os.PathLike[str]) -> bool:
    """按 rosbag2 目录布局检测；metadata 内容由 SDK 负责严格解码。"""
    try:
        candidate = Path(path)
        return candidate.is_dir() and (candidate / ROSBAG2_METADATA).is_file()
    except (OSError, TypeError, ValueError):
        return False


def read_rosbag_structure(
    path: str | os.PathLike[str],
    *,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    max_topics: int = MAX_TOPICS,
    max_messages: int = MAX_MESSAGES,
    max_text_length: int = MAX_TEXT_LENGTH,
) -> RosbagStructure:
    """只读 rosbag1/rosbag2 索引并返回有界结构摘要。"""
    _ensure_sdk()
    _validate_limits(max_metadata_bytes, max_topics, max_messages, max_text_length)
    candidate = _candidate(path)
    if detect_rosbag2(candidate):
        _precheck_rosbag2_metadata(candidate, max_metadata_bytes)
        return _read(candidate, "rosbag2", max_topics, max_messages, max_text_length)
    if detect_rosbag1(candidate):
        return _read(candidate, "rosbag1", max_topics, max_messages, max_text_length)
    raise UnsupportedRosbagError(f"不是受支持的 ROS Bag：{_display_name(candidate)}")


def _candidate(path: str | os.PathLike[str]) -> Path:
    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        raise RosbagInputError("ROS Bag 路径必须是字符串或 Path") from None
    try:
        if not candidate.exists():
            raise RosbagInputError(f"ROS Bag 路径不存在：{_display_name(candidate)}")
        if not candidate.is_file() and not candidate.is_dir():
            raise RosbagInputError(
                f"ROS Bag 路径必须是普通文件或目录：{_display_name(candidate)}"
            )
    except OSError:
        raise RosbagInputError(
            f"无法访问 ROS Bag：{_display_name(candidate)}"
        ) from None
    return candidate


def _read(
    path: Path,
    format_name: Literal["rosbag1", "rosbag2"],
    max_topics: int,
    max_messages: int,
    max_text_length: int,
) -> RosbagStructure:
    reader_type = _Rosbag2Reader if format_name == "rosbag2" else _Rosbag1Reader
    try:
        with reader_type(path) as reader:
            return _build_structure(
                reader,
                format_name,
                max_topics=max_topics,
                max_messages=max_messages,
                max_text_length=max_text_length,
            )
    except RosbagMetadataLimitError:
        raise
    except (
        _Rosbag1ReaderError,
        _Rosbag2ReaderError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ):
        raise RosbagMetadataError(
            f"{format_name} 索引或 metadata 无法解码：{_display_name(path)}"
        ) from None


def _build_structure(
    reader: _Reader,
    format_name: Literal["rosbag1", "rosbag2"],
    *,
    max_topics: int,
    max_messages: int,
    max_text_length: int,
) -> RosbagStructure:
    topic_count = len(reader.topics)
    message_count = int(reader.message_count)
    if topic_count > max_topics:
        raise RosbagMetadataLimitError(
            f"ROS Bag topic 数 {topic_count} 超过上限 {max_topics}"
        )
    if message_count < 0:
        raise RosbagMetadataError("ROS Bag 总消息数不能为负")
    if message_count > max_messages:
        raise RosbagMetadataLimitError(
            f"ROS Bag 消息数 {message_count} 超过上限 {max_messages}"
        )

    topics = tuple(
        RosbagTopicSummary(
            topic=_bounded_text(name, "topic", max_text_length),
            msgtype=_bounded_text(info.msgtype, "msgtype", max_text_length),
            message_count=_bounded_count(info.msgcount, max_messages),
        )
        for name, info in sorted(reader.topics.items())
    )
    if sum(item.message_count for item in topics) != message_count:
        raise RosbagMetadataError("topic 消息数之和与 ROS Bag 总消息数不一致")

    start_time: int | None = None
    end_time: int | None = None
    duration: int | None = None
    if message_count:
        start_time = int(reader.start_time)
        # rosbags Reader.end_time 是右开区间（最后时间戳 + 1ns）。
        end_time = int(reader.end_time) - 1
        if start_time < 0 or end_time < start_time:
            raise RosbagMetadataError("ROS Bag 时间范围无效")
        duration = end_time - start_time

    return RosbagStructure(
        adapter_id="rosbag.structure_readonly",
        adapter_version="1.0.0",
        format=format_name,
        topic_count=topic_count,
        message_count=message_count,
        start_time_ns=start_time,
        end_time_ns=end_time,
        duration_ns=duration,
        topics=topics,
    )


def _bounded_text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise RosbagMetadataError(f"{field} 不是字符串")
    if len(value) > limit:
        raise RosbagMetadataLimitError(f"{field} 超过 {limit} 字符上限")
    return value


def _bounded_count(value: int, limit: int) -> int:
    count = int(value)
    if count < 0:
        raise RosbagMetadataError("topic 消息数不能为负")
    if count > limit:
        raise RosbagMetadataLimitError(f"topic 消息数 {count} 超过上限 {limit}")
    return count


def _precheck_rosbag2_metadata(path: Path, limit: int) -> None:
    try:
        size = (path / ROSBAG2_METADATA).stat().st_size
    except OSError:
        raise RosbagInputError(
            f"无法访问 ROS Bag metadata：{_display_name(path)}"
        ) from None
    if size > limit:
        raise RosbagMetadataLimitError(
            f"rosbag2 metadata 大小 {size} 字节超过上限 {limit}"
        )

    try:
        metadata = YAML(typ="safe").load((path / ROSBAG2_METADATA).read_text())
        info = metadata["rosbag2_bagfile_information"]
        compression_mode = str(info.get("compression_mode", "")).lower()
    except (OSError, YAMLError, KeyError, TypeError, ValueError, AttributeError):
        raise RosbagMetadataError(
            f"rosbag2 metadata 无法解码：{_display_name(path)}"
        ) from None
    if compression_mode == "file":
        raise RosbagMetadataError(
            "rosbag2 file 级压缩会触发无界消息存储解压，本只读结构探测不支持"
        )


def _validate_limits(
    max_metadata_bytes: int,
    max_topics: int,
    max_messages: int,
    max_text_length: int,
) -> None:
    for value, name in (
        (max_metadata_bytes, "max_metadata_bytes"),
        (max_topics, "max_topics"),
        (max_messages, "max_messages"),
        (max_text_length, "max_text_length"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RosbagInputError(f"{name} 必须是正整数")


def _ensure_sdk() -> None:
    if _rosbags is None or _Rosbag1Reader is None or _Rosbag2Reader is None:
        raise RosbagUnavailableError(
            "rosbags SDK 不可用；请安装 data-platform/requirements.txt 锁定的版本"
        )


def _display_name(path: Path) -> str:
    name = path.name or "?"
    cleaned = "".join(char if char.isprintable() else "?" for char in name)
    return cleaned[:_DISPLAY_NAME_LIMIT]


__all__ = [
    "MAX_MESSAGES",
    "MAX_METADATA_BYTES",
    "MAX_TEXT_LENGTH",
    "MAX_TOPICS",
    "ROSBAG1_MAGIC",
    "ROSBAG2_METADATA",
    "detect_rosbag1",
    "detect_rosbag2",
    "read_rosbag_structure",
]
