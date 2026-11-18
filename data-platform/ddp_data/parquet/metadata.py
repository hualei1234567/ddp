"""Parquet 文件级元数据只读读取（DDP-V21-N3-003）。

**能力边界**

本模块只读 Parquet footer 中的 ``FileMetaData``：格式版本、写入器、行数、
行组数、schema 树（字段名/物理类型/逻辑类型/重复性，解析在
:mod:`ddp_data.parquet._schema`）。它**不读取数据页**，因此：

- 不返回任何行值、统计量或编码信息；
- 不支持加密 footer（``PARE`` 魔数）与加密列；
- 不把整个文件读入内存——只读文件头 4 字节、尾部 8 字节与声明的 footer 区间，
  footer 长度本身有上限（``MAX_METADATA_BYTES``）；
- 行组列表只取数量后整体跳过，不把每个行组物化成 Python 对象。

**依赖决策（重要）**

``CLAUDE.md`` 要求协议类能力优先使用官方 SDK；但本仓库当前没有 PyArrow，
且任务约束禁止未评估新增依赖。Parquet footer 是公开稳定的 Thrift Compact
Protocol 结构，只解码它即可获得真实 schema 与行数。该实现是**唯一例外**：
只读、有界、不含数据页解码；一旦后续批准 PyArrow，应优先替换为官方实现，
本模块的返回结构可作为兼容层保留。详细理由写入交付说明与 PR。

**错误纪律**

非 Parquet、损坏、加密、元数据超限、schema 自相矛盾全部抛显式异常；
绝不返回空 schema / 0 行来冒充“读到了但没数据”。错误消息不带文件路径，
也不回显原始字节。
"""

from __future__ import annotations

import os
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

from ddp_data.parquet._schema import MAX_SCHEMA_ELEMENTS, decode_schema
from ddp_data.parquet._thrift import CompactReader, CompactType, ThriftDecodeError
from ddp_data.parquet.errors import (
    NotParquetError,
    ParquetEncryptedError,
    ParquetInputError,
    ParquetMetadataError,
    ParquetMetadataLimitError,
)
from ddp_data.parquet.models import ParquetMetadata

MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_ROW_GROUPS = 100_000
MAX_CREATED_BY_BYTES = 1_024
MAX_NESTING_DEPTH = 64

_HEADER_BYTES = 4
_TRAILER_BYTES = 8
_MIN_FILE_BYTES = _HEADER_BYTES + _TRAILER_BYTES
_MAGIC = b"PAR1"
_ENCRYPTED_MAGIC = b"PARE"
_SUPPORTED_FORMAT_VERSIONS = frozenset({1, 2})

_FIELD_VERSION = 1
_FIELD_SCHEMA = 2
_FIELD_NUM_ROWS = 3
_FIELD_ROW_GROUPS = 4
_FIELD_CREATED_BY = 6


def read_parquet_metadata(path: str | os.PathLike[str]) -> ParquetMetadata:
    """读取文件级元数据；只触碰文件头、尾部与 footer 区间。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise ParquetInputError("Parquet 输入不是可读的普通文件")
    try:
        size = file_path.stat().st_size
        with file_path.open("rb") as handle:
            return read_parquet_metadata_from(handle, size=size)
    except OSError as exc:
        raise ParquetInputError("Parquet 文件读取失败") from exc


def read_parquet_metadata_from(handle: BinaryIO, *, size: int) -> ParquetMetadata:
    """从可 seek 的二进制流读取；``size`` 必须与流内容长度一致。

    暴露这一层是为了让调用方使用**对象存储下载到临时文件的句柄**或测试替身，
    也便于验证“不会整文件读入内存”。
    """
    _check_size(size)
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
    handle.seek(size - _TRAILER_BYTES - metadata_length)
    payload = handle.read(metadata_length)
    if len(payload) != metadata_length:
        raise NotParquetError("footer 元数据读取不完整")
    return _decode_file_metadata(payload)


def _check_size(size: int) -> None:
    if size < 0:
        raise ParquetInputError("文件大小不能为负")
    if size < _MIN_FILE_BYTES:
        raise NotParquetError("文件小于 Parquet 最小长度")


def _decode_file_metadata(payload: bytes) -> ParquetMetadata:
    reader = CompactReader(payload, max_depth=MAX_NESTING_DEPTH)
    try:
        values = reader.read_struct_with(
            {
                _FIELD_VERSION: lambda type_id: _read_int_field(
                    reader, type_id, CompactType.I32, "version"
                ),
                _FIELD_SCHEMA: lambda type_id: _read_schema_list(reader, type_id),
                _FIELD_NUM_ROWS: lambda type_id: _read_int_field(
                    reader, type_id, CompactType.I64, "num_rows"
                ),
                _FIELD_ROW_GROUPS: lambda type_id: _read_row_group_count(reader, type_id),
                _FIELD_CREATED_BY: lambda type_id: _read_text_field(reader, type_id, "created_by"),
            }
        )
    except ThriftDecodeError as exc:
        raise ParquetMetadataError(f"footer 元数据无法解码：{exc}") from exc
    version = _required_i32(values, _FIELD_VERSION, "version")
    num_rows = _required_i64(values, _FIELD_NUM_ROWS, "num_rows")
    row_group_count = _required_int(values, _FIELD_ROW_GROUPS, "row_groups")
    created_by = _optional_str(values, _FIELD_CREATED_BY, "created_by")
    schema = _required_sequence(values, _FIELD_SCHEMA, "schema")
    if version not in _SUPPORTED_FORMAT_VERSIONS:
        raise ParquetMetadataError(f"不支持的 Parquet 格式版本：{version}")
    if num_rows < 0:
        raise ParquetMetadataError("num_rows 不能为负")
    if row_group_count < 0 or row_group_count > MAX_ROW_GROUPS:
        raise ParquetMetadataLimitError(
            f"行组数量超过 {MAX_ROW_GROUPS} 上限或为负：{row_group_count}"
        )
    fields, root_name = decode_schema(schema)
    return ParquetMetadata(
        version=version,
        num_rows=num_rows,
        row_group_count=row_group_count,
        root_name=root_name,
        created_by=created_by,
        fields=fields,
    )


def _expect_type(type_id: int, expected: CompactType, label: str) -> None:
    if type_id != expected:
        raise ParquetMetadataError(f"{label} 的 Thrift 类型非法")


def _read_int_field(reader: CompactReader, type_id: int, expected: CompactType, label: str) -> int:
    _expect_type(type_id, expected, label)
    if expected is CompactType.I32:
        return reader.read_i32()
    return reader.read_i64()


def _read_schema_list(reader: CompactReader, type_id: int) -> list[object]:
    _expect_type(type_id, CompactType.LIST, "schema")
    size, element_type = reader.read_list_header()
    _expect_type(element_type, CompactType.STRUCT, "schema element")
    if size > MAX_SCHEMA_ELEMENTS:
        raise ParquetMetadataLimitError(f"schema 元素数量超过 {MAX_SCHEMA_ELEMENTS} 上限")
    return [reader.read_value(CompactType.STRUCT) for _ in range(size)]


def _read_row_group_count(reader: CompactReader, type_id: int) -> int:
    """只取行组数量；行组内容整体跳过，不物化成 Python 对象。"""
    _expect_type(type_id, CompactType.LIST, "row_groups")
    size, element_type = reader.read_list_header()
    _expect_type(element_type, CompactType.STRUCT, "row_groups element")
    if size > MAX_ROW_GROUPS:
        raise ParquetMetadataLimitError(f"行组数量超过 {MAX_ROW_GROUPS} 上限：{size}")
    for _ in range(size):
        reader.skip(CompactType.STRUCT)
    return size


def _read_text_field(reader: CompactReader, type_id: int, label: str) -> str:
    _expect_type(type_id, CompactType.BINARY, label)
    return reader.read_text(max_bytes=MAX_CREATED_BY_BYTES)


def _required_i32(values: Mapping[int, object], field_id: int, label: str) -> int:
    value = _required_int(values, field_id, label)
    if not -(2**31) <= value < 2**31:
        raise ParquetMetadataError(f"{label} 超出 i32 范围")
    return value


def _required_i64(values: Mapping[int, object], field_id: int, label: str) -> int:
    value = _required_int(values, field_id, label)
    if not -(2**63) <= value < 2**63:
        raise ParquetMetadataError(f"{label} 超出 i64 范围")
    return value


def _required_int(values: Mapping[int, object], field_id: int, label: str) -> int:
    value = values.get(field_id)
    if value is None:
        raise ParquetMetadataError(f"footer 缺少必填字段 {label}")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParquetMetadataError(f"{label} 必须是整数")
    return value


def _required_sequence(values: Mapping[int, object], field_id: int, label: str) -> list[object]:
    value = values.get(field_id)
    if value is None:
        raise ParquetMetadataError(f"footer 缺少必填字段 {label}")
    if not isinstance(value, list):
        raise ParquetMetadataError(f"{label} 必须是列表")
    return value


def _optional_str(values: Mapping[int, object], field_id: int, label: str) -> str | None:
    value = values.get(field_id)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ParquetMetadataError(f"{label} 必须是字符串")
    return value


__all__ = [
    "MAX_CREATED_BY_BYTES",
    "MAX_METADATA_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_ROW_GROUPS",
    "NotParquetError",
    "ParquetEncryptedError",
    "ParquetInputError",
    "ParquetMetadata",
    "ParquetMetadataError",
    "ParquetMetadataLimitError",
    "read_parquet_metadata",
    "read_parquet_metadata_from",
]
