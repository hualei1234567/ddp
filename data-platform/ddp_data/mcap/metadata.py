"""MCAP 文件级元数据只读读取（DDP-V21-N3-005）。

**实现来源**

使用官方 **mcap** Python SDK（``mcap.reader.SeekingReader``），不自研 MCAP
二进制解析。SDK 只读取 footer 与 summary 索引，不解码消息体、不读取 chunk
数据页；本模块在此之上保留平台的统一语义：

- **有界**：先校验首尾 ``MCAP`` 魔数，再用计数包装器限制实际读取字节数；
  单条记录的读取长度也设上限。channel/schema 数量与文本长度同样有界。
  超限一律显式失败，绝不静默截断。
- **显式**：非 MCAP、损坏、缺文件、SDK 缺失、元数据超限全部抛
  :mod:`errors` 中的异常，绝不返回空 channel 列表或 0 消息冒充“读到了但没数据”。
  没有 summary 索引的合法 MCAP 会显式返回 ``has_summary=False`` 与 ``None``
  计数，而不是把“未知”说成 0。
- **脱敏**：对外错误消息不含文件绝对路径，也不回显原始字节或 schema 内容；
  底层 SDK 异常（可能携带偏移/内容）不作为错误文本透出。

只读范围：header 的 profile/library、channel/schema 摘要、statistics 中的
消息计数与时间范围。消息体、channel metadata 的具体值、schema 的 ``data``
均不在返回结果内。

**依赖**：``data-platform/requirements.txt`` 固定 ``mcap==1.6.0``；
后端测试 job 与本地 venv 必须安装该文件，不能依赖开发机巧合。
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from ddp_data.mcap.errors import (
    McapInputError,
    McapMetadataError,
    McapMetadataLimitError,
    McapUnavailableError,
    NotMcapError,
)
from ddp_data.mcap.models import McapChannelSummary, McapMetadata, McapSchemaSummary

try:
    import mcap.exceptions as _mcap_exceptions
    import mcap.reader as _mcap_reader
except ImportError:  # pragma: no cover - 依赖缺失时保留显式错误路径
    _mcap_exceptions = None
    _mcap_reader = None

if TYPE_CHECKING:
    from mcap.records import Channel, Header, Schema, Statistics
    from mcap.summary import Summary

MCAP_MAGIC = b"\x89MCAP0\r\n"
MIN_FILE_BYTES = len(MCAP_MAGIC) * 2 + 17 + 29
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_CHANNELS = 4096
MAX_SCHEMAS = 4096
MAX_TEXT_LENGTH = 4096
MAX_CHANNEL_METADATA_ENTRIES = 64
_DISPLAY_NAME_LIMIT = 128


class _BoundedReader:
    """统计并限制累计读取字节数；超限时抛显式异常而不是截断。"""

    def __init__(self, handle: BinaryIO, limit: int) -> None:
        self._handle = handle
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self.bytes_read += len(data)
        if self.bytes_read > self._limit:
            raise McapMetadataLimitError(f"MCAP 元数据读取超过 {self._limit} 字节上限")
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        return self._handle.tell()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return self._handle.closed


def read_mcap_metadata(
    path: str | os.PathLike[str],
    *,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    max_channels: int = MAX_CHANNELS,
    max_schemas: int = MAX_SCHEMAS,
) -> McapMetadata:
    """读取 MCAP 文件级元数据；只触碰文件头、尾部与 summary 区间。"""
    _ensure_sdk()
    _validate_limits(max_metadata_bytes, max_channels, max_schemas)
    try:
        candidate = Path(path)
    except TypeError:
        raise McapInputError("MCAP 路径必须是字符串或 Path") from None
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise McapInputError(f"MCAP 文件不存在：{_display_name(candidate)}") from None
    except OSError:
        raise McapInputError(f"无法访问 MCAP 文件：{_display_name(candidate)}") from None
    if not resolved.is_file():
        raise McapInputError(f"MCAP 路径不是普通文件：{_display_name(candidate)}")
    try:
        size = resolved.stat().st_size
        with resolved.open("rb", buffering=0) as handle:
            return read_mcap_metadata_from(
                handle,
                size=size,
                max_metadata_bytes=max_metadata_bytes,
                max_channels=max_channels,
                max_schemas=max_schemas,
            )
    except OSError:
        # 不链式保留原始异常：底层错误可能携带绝对路径。
        raise McapInputError(f"MCAP 文件读取失败：{_display_name(candidate)}") from None


def read_mcap_metadata_from(
    handle: BinaryIO,
    *,
    size: int,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    max_channels: int = MAX_CHANNELS,
    max_schemas: int = MAX_SCHEMAS,
) -> McapMetadata:
    """从可 seek 的二进制流读取；``size`` 必须与流内容长度一致。

    暴露这一层是为了让调用方使用对象存储下载到临时文件的句柄或测试替身，
    也便于验证“不会整文件读入内存”。
    """
    _ensure_sdk()
    _validate_limits(max_metadata_bytes, max_channels, max_schemas)
    try:
        _precheck_magic(handle, size)
    except OSError:
        raise McapInputError("MCAP 文件读取失败") from None
    bounded = _BoundedReader(handle, max_metadata_bytes)
    try:
        reader = _mcap_reader.SeekingReader(bounded, record_size_limit=max_metadata_bytes)
        header = reader.get_header()
        summary = reader.get_summary()
    except _mcap_exceptions.RecordLengthLimitExceeded:
        raise McapMetadataLimitError(f"MCAP 记录长度超过 {max_metadata_bytes} 字节上限") from None
    except (_mcap_exceptions.McapError, OSError, ValueError, KeyError, IndexError, struct.error):
        # 不链式保留原始异常：SDK 错误可能包含偏移或原始内容。
        raise McapMetadataError("MCAP footer/summary 元数据无法解码") from None
    return _build_metadata(
        header,
        summary,
        max_channels=max_channels,
        max_schemas=max_schemas,
    )


def _ensure_sdk() -> None:
    if _mcap_reader is None or _mcap_exceptions is None:  # pragma: no cover - 依赖缺失
        raise McapUnavailableError(
            "mcap 官方 SDK 不可用；请安装 data-platform/requirements.txt 锁定的 mcap 版本"
        )


def _validate_limits(max_metadata_bytes: int, max_channels: int, max_schemas: int) -> None:
    for value, name in (
        (max_metadata_bytes, "max_metadata_bytes"),
        (max_channels, "max_channels"),
        (max_schemas, "max_schemas"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise McapInputError(f"{name} 必须是正整数")


def _precheck_magic(handle: BinaryIO, size: int) -> None:
    if size < 0:
        raise McapInputError("文件大小不能为负")
    if size < MIN_FILE_BYTES:
        raise NotMcapError("文件小于 MCAP 最小长度")
    handle.seek(0)
    if handle.read(len(MCAP_MAGIC)) != MCAP_MAGIC:
        raise NotMcapError("缺少 MCAP 文件头魔数")
    handle.seek(size - len(MCAP_MAGIC))
    if handle.read(len(MCAP_MAGIC)) != MCAP_MAGIC:
        raise NotMcapError("缺少 MCAP 文件尾魔数")
    handle.seek(0)


def _build_metadata(
    header: Header,
    summary: Summary | None,
    *,
    max_channels: int,
    max_schemas: int,
) -> McapMetadata:
    profile = _bounded_text(header.profile, "profile")
    library = _bounded_text(header.library, "library")
    if summary is None:
        return McapMetadata(
            profile=profile,
            library=library,
            has_summary=False,
            channel_count=None,
            schema_count=None,
            message_count=None,
            message_start_time_ns=None,
            message_end_time_ns=None,
            channels=(),
            schemas=(),
        )

    if len(summary.channels) > max_channels:
        raise McapMetadataLimitError(
            f"MCAP channel 数 {len(summary.channels)} 超过上限 {max_channels}"
        )
    if len(summary.schemas) > max_schemas:
        raise McapMetadataLimitError(
            f"MCAP schema 数 {len(summary.schemas)} 超过上限 {max_schemas}"
        )

    schemas = _schema_summaries(summary.schemas)
    statistics = summary.statistics
    channel_count = _resolve_count(
        statistics.channel_count if statistics is not None else None,
        len(summary.channels),
        "channel",
    )
    schema_count = _resolve_count(
        statistics.schema_count if statistics is not None else None,
        len(summary.schemas),
        "schema",
    )
    if channel_count > max_channels:
        raise McapMetadataLimitError(f"MCAP channel 数 {channel_count} 超过上限 {max_channels}")
    if schema_count > max_schemas:
        raise McapMetadataLimitError(f"MCAP schema 数 {schema_count} 超过上限 {max_schemas}")
    channels = _channel_summaries(summary.channels, schemas, statistics)

    message_count = statistics.message_count if statistics is not None else None
    start_time: int | None = None
    end_time: int | None = None
    if statistics is not None and message_count:
        start_time = int(statistics.message_start_time)
        end_time = int(statistics.message_end_time)
        if end_time < start_time:
            raise McapMetadataError("statistics 的 message_end_time 早于 message_start_time")

    return McapMetadata(
        profile=profile,
        library=library,
        has_summary=True,
        channel_count=channel_count,
        schema_count=schema_count,
        message_count=message_count,
        message_start_time_ns=start_time,
        message_end_time_ns=end_time,
        channels=channels,
        schemas=schemas,
    )


def _resolve_count(declared: int | None, observed: int, kind: str) -> int:
    """解析权威数量。

    MCAP 允许 summary 只重复 statistics 而不重复 channel/schema 记录
    （writer 的 ``repeat_channels`` / ``repeat_schemas`` 关闭时）。这种情况下
    ``observed == 0`` 表示“明细未收录”，数量以 statistics 为准；如果两者都
    有值却不一致，则视为 self-contradictory，显式失败。
    """
    if declared is None:
        return observed
    if observed and declared != observed:
        raise McapMetadataError(f"statistics 的 {kind} 数与 summary 实际数量不一致")
    return declared


def _schema_summaries(schemas: dict[int, Schema]) -> tuple[McapSchemaSummary, ...]:
    return tuple(
        McapSchemaSummary(
            schema_id=int(schema.id),
            name=_bounded_text(schema.name, "schema.name"),
            encoding=_bounded_text(schema.encoding, "schema.encoding"),
            data_bytes=len(schema.data),
        )
        for _, schema in sorted(schemas.items())
    )


def _channel_summaries(
    channels: dict[int, Channel],
    schemas: tuple[McapSchemaSummary, ...],
    statistics: Statistics | None,
) -> tuple[McapChannelSummary, ...]:
    schema_index = {schema.schema_id: schema for schema in schemas}
    counts = statistics.channel_message_counts if statistics is not None else None
    result: list[McapChannelSummary] = []
    for _, channel in sorted(channels.items()):
        if len(channel.metadata) > MAX_CHANNEL_METADATA_ENTRIES:
            raise McapMetadataLimitError(
                f"channel metadata 条目数超过上限 {MAX_CHANNEL_METADATA_ENTRIES}"
            )
        _bounded_metadata(channel.metadata)
        schema_id = int(channel.schema_id)
        schema = schema_index.get(schema_id)
        if schema_id != 0 and schema is None:
            raise McapMetadataError("channel 引用了 summary 中不存在的 schema")
        result.append(
            McapChannelSummary(
                channel_id=int(channel.id),
                topic=_bounded_text(channel.topic, "channel.topic"),
                message_encoding=_bounded_text(
                    channel.message_encoding, "channel.message_encoding"
                ),
                schema_id=schema_id,
                schema_name=schema.name if schema is not None else None,
                schema_encoding=schema.encoding if schema is not None else None,
                message_count=int(counts.get(channel.id, 0)) if counts is not None else None,
                metadata_entry_count=len(channel.metadata),
            )
        )
    return tuple(result)


def _bounded_metadata(metadata: dict[str, str]) -> None:
    for key, value in metadata.items():
        _bounded_text(key, "channel.metadata.key")
        _bounded_text(value, "channel.metadata.value")


def _bounded_text(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise McapMetadataError(f"{field} 不是字符串")
    if len(value) > MAX_TEXT_LENGTH:
        raise McapMetadataLimitError(f"{field} 超过 {MAX_TEXT_LENGTH} 字符上限")
    return value


def _display_name(path: Path) -> str:
    """错误信息里只留可打印文件名；控制字符替换掉，避免日志注入。"""
    name = path.name or "?"
    cleaned = "".join(char if char.isprintable() else "?" for char in name)
    return cleaned[:_DISPLAY_NAME_LIMIT]


__all__ = [
    "MAX_CHANNELS",
    "MAX_CHANNEL_METADATA_ENTRIES",
    "MAX_METADATA_BYTES",
    "MAX_SCHEMAS",
    "MAX_TEXT_LENGTH",
    "MCAP_MAGIC",
    "MIN_FILE_BYTES",
    "McapChannelSummary",
    "McapMetadata",
    "McapSchemaSummary",
    "read_mcap_metadata",
    "read_mcap_metadata_from",
]
