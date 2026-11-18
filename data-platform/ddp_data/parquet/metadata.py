"""Parquet 文件级元数据只读读取（DDP-V21-N3-003）。

**实现来源**

使用官方 **PyArrow**（``pyarrow.parquet.read_metadata`` / ``ParquetSchema``），
不自研 footer 协议解析。PyArrow 只读取 footer 元数据，不解码数据页；本模块
在此之上保留平台的统一语义：

- **有界**：读取前先校验首尾 ``PAR1`` 标记、footer 长度上限与长度/文件大小
  一致性；加密 footer（``PARE``）与非法结构显式失败。
- **显式**：非 Parquet、损坏、加密、元数据超限全部抛 :mod:`errors` 中的异常，
  绝不返回空 schema / 0 行冒充“读到了但没数据”。
- **脱敏**：对外错误消息不含文件路径，也不回显原始字节；底层 PyArrow 异常
  （可能带路径）不作为错误文本透出。

只读范围：schema/行数/行组数/写入器与逻辑类型。行值、列统计、压缩编码不在
本模块范围内。

**依赖**：``data-platform/requirements.txt`` 固定 ``pyarrow==23.0.1``；
后端测试 job 与本地 venv 必须安装该文件，不能依赖开发机巧合。
"""

from __future__ import annotations

import json
import os
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq

from ddp_data.parquet.errors import (
    NotParquetError,
    ParquetEncryptedError,
    ParquetInputError,
    ParquetMetadataError,
    ParquetMetadataLimitError,
)
from ddp_data.parquet.models import ParquetColumn, ParquetMetadata

MAX_METADATA_BYTES = 16 * 1024 * 1024

_HEADER_BYTES = 4
_TRAILER_BYTES = 8
_MIN_FILE_BYTES = _HEADER_BYTES + _TRAILER_BYTES
_MAGIC = b"PAR1"
_ENCRYPTED_MAGIC = b"PARE"
_TIME_UNITS: Mapping[str, str] = {
    "milliseconds": "MILLIS",
    "microseconds": "MICROS",
    "nanoseconds": "NANOS",
}


def read_parquet_metadata(path: str | os.PathLike[str]) -> ParquetMetadata:
    """读取文件级元数据；只触碰文件头、尾部与 footer 区间。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise ParquetInputError("Parquet 输入不是可读的普通文件")
    try:
        size = file_path.stat().st_size
        with file_path.open("rb") as handle:
            return read_parquet_metadata_from(handle, size=size)
    except OSError:
        # 不链式保留原始异常：底层错误可能携带绝对路径。
        raise ParquetInputError("Parquet 文件读取失败") from None


def read_parquet_metadata_from(handle: BinaryIO, *, size: int) -> ParquetMetadata:
    """从可 seek 的二进制流读取；``size`` 必须与流内容长度一致。

    暴露这一层是为了让调用方使用**对象存储下载到临时文件的句柄**或测试替身，
    也便于验证“不会整文件读入内存”。
    """
    _precheck_footer(handle, size)
    try:
        arrow_metadata = pq.read_metadata(handle)
    except (pa.ArrowException, OSError):
        # 不链式保留原始异常：PyArrow/Thrift 错误可能包含路径或原始内容。
        raise ParquetMetadataError("footer 元数据无法解码") from None
    return _to_model(arrow_metadata)


def _precheck_footer(handle: BinaryIO, size: int) -> None:
    if size < 0:
        raise ParquetInputError("文件大小不能为负")
    if size < _MIN_FILE_BYTES:
        raise NotParquetError("文件小于 Parquet 最小长度")
    handle.seek(0)
    if handle.read(_HEADER_BYTES) != _MAGIC:
        raise NotParquetError("缺少 Parquet 文件头标记")
    handle.seek(size - _TRAILER_BYTES)
    trailer = handle.read(_TRAILER_BYTES)
    if len(trailer) != _TRAILER_BYTES:
        raise NotParquetError("Parquet 文件尾读取不完整")
    metadata_length = struct.unpack("<I", trailer[:_HEADER_BYTES])[0]
    magic = trailer[_HEADER_BYTES:]
    if magic == _ENCRYPTED_MAGIC:
        raise ParquetEncryptedError("Parquet 使用加密 footer，只读 Adapter 不支持")
    if magic != _MAGIC:
        raise NotParquetError("缺少 Parquet 文件尾标记")
    if metadata_length == 0:
        raise ParquetMetadataError("footer 元数据长度为 0")
    if metadata_length > MAX_METADATA_BYTES:
        raise ParquetMetadataLimitError(f"footer 元数据长度超过 {MAX_METADATA_BYTES} 字节上限")
    if metadata_length > size - _MIN_FILE_BYTES:
        raise NotParquetError("footer 元数据长度超出文件大小")


def _to_model(arrow_metadata: pq.FileMetaData) -> ParquetMetadata:
    schema = arrow_metadata.schema
    arrow_schema = schema.to_arrow_schema()
    columns = tuple(_column_of(schema.column(index)) for index in range(len(schema)))
    num_rows = int(arrow_metadata.num_rows)
    if num_rows < 0:  # pragma: no cover - PyArrow 不会返回负行数
        raise ParquetMetadataError("num_rows 不能为负")
    return ParquetMetadata(
        format_version=str(arrow_metadata.format_version),
        num_rows=num_rows,
        row_group_count=int(arrow_metadata.num_row_groups),
        created_by=arrow_metadata.created_by or None,
        top_level_fields=tuple(arrow_schema.names),
        columns=columns,
    )


def _column_of(column: pq.ColumnSchema) -> ParquetColumn:
    max_definition_level = int(column.max_definition_level)
    max_repetition_level = int(column.max_repetition_level)
    return ParquetColumn(
        name=str(column.name),
        path=str(column.path),
        physical_type=str(column.physical_type),
        logical_type=_logical_type_text(column.logical_type),
        converted_type=_normalize_converted(column.converted_type),
        repetition=_repetition(max_definition_level, max_repetition_level),
        max_definition_level=max_definition_level,
        max_repetition_level=max_repetition_level,
    )


def _repetition(max_definition_level: int, max_repetition_level: int) -> str:
    if max_repetition_level > 0:
        return "REPEATED"
    if max_definition_level > 0:
        return "OPTIONAL"
    return "REQUIRED"


def _normalize_converted(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return None if text in ("", "NONE") else text


def _logical_type_text(logical_type: pq.ParquetLogicalType | None) -> str | None:
    if logical_type is None:
        return None
    name = logical_type.type
    if not name or name == "NONE":
        return None
    detail = _logical_detail(logical_type)
    if name == "DECIMAL":
        return _decimal_text(detail)
    if name in ("TIME", "TIMESTAMP"):
        return _temporal_text(str(name), detail)
    if name == "INT":
        return _integer_text(detail)
    return str(name)


def _logical_detail(logical_type: pq.ParquetLogicalType) -> Mapping[str, object]:
    try:
        value = json.loads(logical_type.to_json())
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _decimal_text(detail: Mapping[str, object]) -> str:
    scale = detail.get("scale")
    precision = detail.get("precision")
    return f"DECIMAL(scale={scale}, precision={precision})"


def _temporal_text(name: str, detail: Mapping[str, object]) -> str:
    unit = _TIME_UNITS.get(str(detail.get("timeUnit")))
    adjusted = detail.get("isAdjustedToUTC")
    if unit is None or not isinstance(adjusted, bool):
        return name
    return f"{name}(unit={unit}, utc={adjusted})"


def _integer_text(detail: Mapping[str, object]) -> str:
    bit_width = detail.get("bitWidth")
    signed = detail.get("isSigned")
    if bit_width is None or not isinstance(signed, bool):
        return "INTEGER"
    return f"INTEGER(bitWidth={bit_width}, signed={signed})"


__all__ = [
    "MAX_METADATA_BYTES",
    "NotParquetError",
    "ParquetColumn",
    "ParquetEncryptedError",
    "ParquetInputError",
    "ParquetMetadata",
    "ParquetMetadataError",
    "ParquetMetadataLimitError",
    "read_parquet_metadata",
    "read_parquet_metadata_from",
]
