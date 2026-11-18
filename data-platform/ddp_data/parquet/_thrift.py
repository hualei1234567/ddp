"""Parquet footer 使用的 Thrift Compact Protocol 最小只读解码器（内部模块）。

**为什么手写这一层（依赖评估结论）**

仓库当前没有 PyArrow、Pandas 或任何 Parquet 运行时：``backend/requirements.txt``
与 ``data-platform/requirements.txt`` 都没有声明，本任务约束也禁止未评估新增
依赖。而 Parquet 的**文件级**信息——schema、行数、行组数、写入器版本——全部在
文件尾部的 Thrift Compact Protocol 结构里；读取它不需要解码数据页、不需要压缩
库，也不需要列式运行时。因此这里只实现**只读、有界、可解释**的最小子集，
而不是手写 Parquet 解码器：

- 只解码结构体字段头、整数、二进制、布尔、double、list/map，以及跳过未知值；
- 所有长度、集合元素数与嵌套深度都有上限，越界显式失败，不截断后冒充成功；
- 错误信息只带字段号/长度，不带文件内容。

数据页、字典页、压缩编码、列统计等均**不在**本模块范围内。
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from enum import IntEnum


class CompactType(IntEnum):
    """Thrift Compact Protocol 的线类型编号。"""

    STOP = 0
    BOOLEAN_TRUE = 1
    BOOLEAN_FALSE = 2
    BYTE = 3
    I16 = 4
    I32 = 5
    I64 = 6
    DOUBLE = 7
    BINARY = 8
    LIST = 9
    SET = 10
    MAP = 11
    STRUCT = 12


class ThriftDecodeError(ValueError):
    """Compact Protocol 字节流非法或超过上限；调用方必须显式失败。"""


_INTEGER_TYPES = frozenset({CompactType.I16, CompactType.I32, CompactType.I64})
_COLLECTION_TYPES = frozenset({CompactType.LIST, CompactType.SET})
_MAX_VARINT_BYTES = 10
_I32_RANGE = (-(2**31), 2**31 - 1)
_I64_RANGE = (-(2**63), 2**63 - 1)


class CompactReader:
    """有界只读游标；不持有文件句柄，只在给定 ``bytes`` 上移动。"""

    def __init__(
        self,
        data: bytes,
        *,
        max_depth: int = 64,
        max_collection_items: int = 100_000,
    ) -> None:
        self._data = data
        self._position = 0
        self._max_depth = max_depth
        self._max_collection_items = max_collection_items

    @property
    def position(self) -> int:
        return self._position

    @property
    def remaining(self) -> int:
        return len(self._data) - self._position

    def read_struct(self) -> dict[int, object]:
        """读取一个结构体；未知字段按线类型跳过，不保留原始字节。"""
        return self._read_struct(depth=0)

    def read_struct_with(
        self,
        handlers: Mapping[int, Callable[[int], object]],
    ) -> dict[int, object]:
        """只解码指定字段，其余字段按线类型跳过。

        用于 ``FileMetaData`` 这类可能含大列表的结构：行组列表只需要数量，
        不应把每个行组都物化成 Python 对象。
        """
        fields: dict[int, object] = {}
        last_field_id = 0
        while True:
            field_id, type_id, is_stop = self.read_field_header(last_field_id)
            if is_stop:
                return fields
            last_field_id = field_id
            handler = handlers.get(field_id)
            if handler is None:
                self.skip(type_id)
                continue
            fields[field_id] = handler(type_id)

    def _read_struct(self, *, depth: int) -> dict[int, object]:
        if depth > self._max_depth:
            raise ThriftDecodeError("结构体嵌套超过上限")
        fields: dict[int, object] = {}
        last_field_id = 0
        while True:
            field_id, type_id, is_stop = self.read_field_header(last_field_id)
            if is_stop:
                return fields
            last_field_id = field_id
            # 结构体字段的布尔值编码在类型 nibble 里，不占额外字节。
            if type_id in (CompactType.BOOLEAN_TRUE, CompactType.BOOLEAN_FALSE):
                fields[field_id] = type_id == CompactType.BOOLEAN_TRUE
            else:
                fields[field_id] = self.read_value(type_id, depth=depth)

    def read_field_header(self, last_field_id: int) -> tuple[int, int, bool]:
        """返回 ``(field_id, type_id, is_stop)``；未知字段的 id 交给调用方跳过。"""
        header = self._read_byte()
        if header == CompactType.STOP:
            return 0, CompactType.STOP, True
        type_id = header & 0x0F
        delta = (header >> 4) & 0x0F
        if delta:
            field_id = last_field_id + delta
        else:
            field_id = self._read_zigzag()
        if field_id <= 0:
            raise ThriftDecodeError("字段号必须为正数")
        return field_id, type_id, False

    def read_i32(self) -> int:
        return self._read_signed("i32", _I32_RANGE)

    def read_i64(self) -> int:
        return self._read_signed("i64", _I64_RANGE)

    def read_i16(self) -> int:
        return self._read_signed("i16", (-(2**15), 2**15 - 1))

    def read_double(self) -> float:
        return struct.unpack("<d", self._read_exact(8))[0]

    def read_binary(self) -> bytes:
        size = self._read_varint(max_bytes=5)
        return self._read_exact(size)

    def read_text(self, *, max_bytes: int) -> str:
        raw = self.read_binary()
        if len(raw) > max_bytes:
            raise ThriftDecodeError(f"字符串超过 {max_bytes} 字节上限")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ThriftDecodeError("字符串不是合法 UTF-8") from exc

    def read_list_header(self) -> tuple[int, int]:
        header = self._read_byte()
        size = (header >> 4) & 0x0F
        type_id = header & 0x0F
        if size == 15:
            size = self._read_varint(max_bytes=5)
        if type_id == CompactType.STOP:
            raise ThriftDecodeError("集合元素类型不能是 STOP")
        if size > self._max_collection_items:
            raise ThriftDecodeError("集合元素数量超过上限")
        return size, type_id

    def read_map_header(self) -> tuple[int, int, int]:
        size = self._read_varint(max_bytes=5)
        if size > self._max_collection_items:
            raise ThriftDecodeError("map 元素数量超过上限")
        if size == 0:
            return 0, CompactType.STOP, CompactType.STOP
        types = self._read_byte()
        return size, (types >> 4) & 0x0F, types & 0x0F

    def read_value(self, type_id: int, *, depth: int = 0) -> object:
        """按线类型解码一个值；仅供元数据中的小结构使用。"""
        if depth > self._max_depth:
            raise ThriftDecodeError("结构体嵌套超过上限")
        compact = _as_type(type_id)
        if compact in (CompactType.BOOLEAN_TRUE, CompactType.BOOLEAN_FALSE):
            # 容器中的布尔值是 1 字节元素（1=TRUE，2=FALSE）。
            return self._read_bool_element()
        if compact is CompactType.BYTE:
            return self._read_signed_byte()
        if compact is CompactType.I16:
            return self.read_i16()
        if compact is CompactType.I32:
            return self.read_i32()
        if compact is CompactType.I64:
            return self.read_i64()
        if compact is CompactType.DOUBLE:
            return self.read_double()
        if compact is CompactType.BINARY:
            return self.read_binary()
        if compact in _COLLECTION_TYPES:
            size, element_type = self.read_list_header()
            return [self.read_value(element_type, depth=depth + 1) for _ in range(size)]
        if compact is CompactType.MAP:
            size, key_type, value_type = self.read_map_header()
            return [
                (
                    self.read_value(key_type, depth=depth + 1),
                    self.read_value(value_type, depth=depth + 1),
                )
                for _ in range(size)
            ]
        if compact is CompactType.STRUCT:
            return self._read_struct(depth=depth + 1)
        raise ThriftDecodeError(f"不支持的 Thrift 类型：{type_id}")

    def skip(self, type_id: int, *, depth: int = 0, as_element: bool = False) -> None:
        """按线类型前进，不构造任何对象；用于跳过不需要的字段。

        ``as_element=True`` 表示当前值位于 list/set/map 中：容器里的布尔值
        占 1 字节，而结构体字段的布尔值编码在字段头里、不占额外字节。
        """
        if depth > self._max_depth:
            raise ThriftDecodeError("结构体嵌套超过上限")
        compact = _as_type(type_id)
        if compact in (CompactType.BOOLEAN_TRUE, CompactType.BOOLEAN_FALSE):
            if as_element:
                self._read_exact(1)
            return
        if compact is CompactType.BYTE:
            self._read_exact(1)
            return
        if compact in _INTEGER_TYPES:
            self._read_varint(max_bytes=_MAX_VARINT_BYTES)
            return
        if compact is CompactType.DOUBLE:
            self._read_exact(8)
            return
        if compact is CompactType.BINARY:
            self._read_exact(self._read_varint(max_bytes=5))
            return
        if compact in _COLLECTION_TYPES:
            size, element_type = self.read_list_header()
            for _ in range(size):
                self.skip(element_type, depth=depth + 1, as_element=True)
            return
        if compact is CompactType.MAP:
            size, key_type, value_type = self.read_map_header()
            for _ in range(size):
                self.skip(key_type, depth=depth + 1, as_element=True)
                self.skip(value_type, depth=depth + 1, as_element=True)
            return
        if compact is CompactType.STRUCT:
            last_field_id = 0
            while True:
                field_id, inner_type, is_stop = self.read_field_header(last_field_id)
                if is_stop:
                    return
                last_field_id = field_id
                self.skip(inner_type, depth=depth + 1)
        raise ThriftDecodeError(f"不支持的 Thrift 类型：{type_id}")

    def _read_signed(self, label: str, bounds: tuple[int, int]) -> int:
        value = self._read_zigzag()
        if not bounds[0] <= value <= bounds[1]:
            raise ThriftDecodeError(f"{label} 超出取值范围")
        return value

    def _read_zigzag(self) -> int:
        value = self._read_varint(max_bytes=_MAX_VARINT_BYTES)
        return (value >> 1) ^ -(value & 1)

    def _read_varint(self, *, max_bytes: int) -> int:
        result = 0
        for shift in range(0, max_bytes * 7, 7):
            byte = self._read_byte()
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
        raise ThriftDecodeError("varint 超过最大长度")

    def _read_bool_element(self) -> bool:
        raw = self._read_byte()
        if raw == CompactType.BOOLEAN_TRUE:
            return True
        if raw == CompactType.BOOLEAN_FALSE:
            return False
        raise ThriftDecodeError("容器中的布尔元素编码非法")

    def _read_signed_byte(self) -> int:
        value = self._read_byte()
        return value - 256 if value >= 128 else value

    def _read_byte(self) -> int:
        if self._position >= len(self._data):
            raise ThriftDecodeError("字节流提前结束")
        value = self._data[self._position]
        self._position += 1
        return value

    def _read_exact(self, size: int) -> bytes:
        end = self._position + size
        if size < 0 or end > len(self._data):
            raise ThriftDecodeError("字节流提前结束")
        chunk = self._data[self._position : end]
        self._position = end
        return chunk


def _as_type(type_id: int) -> CompactType:
    try:
        return CompactType(type_id)
    except ValueError as exc:
        raise ThriftDecodeError(f"未知的 Thrift 类型编号：{type_id}") from exc


__all__ = ["CompactReader", "CompactType", "ThriftDecodeError"]
