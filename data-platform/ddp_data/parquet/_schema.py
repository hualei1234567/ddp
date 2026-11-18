"""Parquet schema 解析与校验（内部模块）。

输入是 Thrift 解码后的 ``SchemaElement`` 列表（Parquet 约定为**前序扁平**：
第一个元素是根，每个元素用 ``num_children`` 声明子节点数）。本模块把它还原成
树，并只产出可解释的字段摘要：

- 物理类型、重复性、converted type 用枚举名，不返回裸数字；
- 逻辑类型解码到 ``STRING`` / ``DECIMAL(scale, precision)`` /
  ``TIMESTAMP(unit, utc)`` / ``INTEGER(bitWidth, signed)`` 这类可核对文本；
- 未知枚举值、叶子缺物理类型、组字段带物理类型、``num_children`` 与实际结构
  不一致都显式失败，不猜测、不截断。

不读数据页，也不做列统计或编码推断。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ddp_data.parquet.errors import ParquetMetadataError, ParquetMetadataLimitError
from ddp_data.parquet.models import ParquetSchemaField

MAX_SCHEMA_ELEMENTS = 10_000
MAX_FIELD_NAME_BYTES = 1_024
MAX_NESTING_DEPTH = 64

_PHYSICAL_TYPES: Mapping[int, str] = {
    0: "BOOLEAN",
    1: "INT32",
    2: "INT64",
    3: "INT96",
    4: "FLOAT",
    5: "DOUBLE",
    6: "BYTE_ARRAY",
    7: "FIXED_LEN_BYTE_ARRAY",
}
_REPETITIONS: Mapping[int, str] = {0: "REQUIRED", 1: "OPTIONAL", 2: "REPEATED"}
_CONVERTED_TYPES: Mapping[int, str] = {
    0: "UTF8",
    1: "MAP",
    2: "MAP_KEY_VALUE",
    3: "LIST",
    4: "ENUM",
    5: "DECIMAL",
    6: "DATE",
    7: "TIME_MILLIS",
    8: "TIME_MICROS",
    9: "TIMESTAMP_MILLIS",
    10: "TIMESTAMP_MICROS",
    11: "UINT_8",
    12: "UINT_16",
    13: "UINT_32",
    14: "UINT_64",
    15: "INT_8",
    16: "INT_16",
    17: "INT_32",
    18: "INT_64",
    19: "JSON",
    20: "BSON",
    21: "INTERVAL",
}
_LOGICAL_MEMBERS: Mapping[int, str] = {
    1: "STRING",
    2: "MAP",
    3: "LIST",
    4: "ENUM",
    5: "DECIMAL",
    6: "DATE",
    7: "TIME",
    8: "TIMESTAMP",
    10: "INTEGER",
    11: "UNKNOWN",
    12: "JSON",
    13: "BSON",
    14: "UUID",
    15: "FLOAT16",
    16: "VARIANT",
    17: "GEOMETRY",
    18: "GEOGRAPHY",
}
_TIME_UNITS: Mapping[int, str] = {1: "MILLIS", 2: "MICROS", 3: "NANOS"}


@dataclass(frozen=True, slots=True)
class _RawSchemaElement:
    physical_type: int | None
    type_length: int | None
    repetition: int | None
    name: str | None
    num_children: int | None
    converted_type: int | None
    scale: int | None
    precision: int | None
    field_id: int | None
    logical_type: str | None


def decode_schema(raw: list[object]) -> tuple[tuple[ParquetSchemaField, ...], str]:
    """把 Thrift 解码后的 schema 列表还原为字段序列与根节点名。"""
    if not raw:
        raise ParquetMetadataError("schema 为空")
    if len(raw) > MAX_SCHEMA_ELEMENTS:
        raise ParquetMetadataLimitError(f"schema 元素数量超过 {MAX_SCHEMA_ELEMENTS} 上限")
    elements = tuple(_schema_element_of(item) for item in raw)
    return _build_schema_tree(elements)


def _schema_element_of(value: object) -> _RawSchemaElement:
    if not isinstance(value, dict):
        raise ParquetMetadataError("schema 元素必须是结构体")
    return _RawSchemaElement(
        physical_type=_optional_enum(value, 1, _PHYSICAL_TYPES, "schema.type"),
        type_length=_optional_int(value, 2, "schema.type_length"),
        repetition=_optional_enum(value, 3, _REPETITIONS, "schema.repetition_type"),
        name=_optional_field_text(value, 4, "schema.name"),
        num_children=_optional_int(value, 5, "schema.num_children"),
        converted_type=_optional_enum(value, 6, _CONVERTED_TYPES, "schema.converted_type"),
        scale=_optional_int(value, 7, "schema.scale"),
        precision=_optional_int(value, 8, "schema.precision"),
        field_id=_optional_int(value, 9, "schema.field_id"),
        logical_type=_optional_logical_type(value, 10),
    )


def _optional_int(values: Mapping[int, object], field_id: int, label: str) -> int | None:
    value = values.get(field_id)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParquetMetadataError(f"{label} 必须是整数")
    return value


def _optional_enum(
    values: Mapping[int, object], field_id: int, mapping: Mapping[int, str], label: str
) -> str | None:
    value = _optional_int(values, field_id, label)
    if value is None:
        return None
    name = mapping.get(value)
    if name is None:
        raise ParquetMetadataError(f"{label} 取值未知：{value}")
    return name


def _optional_field_text(values: Mapping[int, object], field_id: int, label: str) -> str | None:
    value = values.get(field_id)
    if value is None:
        return None
    if not isinstance(value, bytes):
        raise ParquetMetadataError(f"{label} 必须是字节串")
    if len(value) > MAX_FIELD_NAME_BYTES:
        raise ParquetMetadataError(f"{label} 超过 {MAX_FIELD_NAME_BYTES} 字节上限")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParquetMetadataError(f"{label} 不是合法 UTF-8") from exc


def _optional_logical_type(values: Mapping[int, object], field_id: int) -> str | None:
    value = values.get(field_id)
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) != 1:
        raise ParquetMetadataError("logicalType 必须恰好包含一个成员")
    member_id, detail = next(iter(value.items()))
    name = _LOGICAL_MEMBERS.get(member_id)
    if name is None:
        return f"UNKNOWN({member_id})"
    if member_id == 5:
        return _decimal_type(detail)
    if member_id in (7, 8):
        return _temporal_type(name, detail)
    if member_id == 10:
        return _integer_type(detail)
    return name


def _decimal_type(detail: object) -> str:
    values = _detail_of(detail, "DECIMAL")
    scale = _detail_int(values, 1, "DECIMAL.scale")
    precision = _detail_int(values, 2, "DECIMAL.precision")
    return f"DECIMAL(scale={scale}, precision={precision})"


def _temporal_type(name: str, detail: object) -> str:
    values = _detail_of(detail, name)
    adjusted = _detail_bool(values, 1, f"{name}.isAdjustedToUTC")
    unit_values = _detail_of(values.get(2), f"{name}.unit")
    if len(unit_values) != 1:
        raise ParquetMetadataError(f"{name}.unit 必须恰好包含一个成员")
    unit_id = next(iter(unit_values))
    unit = _TIME_UNITS.get(unit_id)
    if unit is None:
        raise ParquetMetadataError(f"{name}.unit 取值未知：{unit_id}")
    return f"{name}(unit={unit}, utc={adjusted})"


def _integer_type(detail: object) -> str:
    values = _detail_of(detail, "INTEGER")
    bit_width = _detail_int(values, 1, "INTEGER.bitWidth")
    signed = _detail_bool(values, 2, "INTEGER.isSigned")
    return f"INTEGER(bitWidth={bit_width}, signed={signed})"


def _detail_of(detail: object, label: str) -> Mapping[int, object]:
    if not isinstance(detail, dict):
        raise ParquetMetadataError(f"{label} 成员必须是结构体")
    return detail


def _detail_int(values: Mapping[int, object], field_id: int, label: str) -> int:
    value = values.get(field_id)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParquetMetadataError(f"{label} 必须是整数")
    return value


def _detail_bool(values: Mapping[int, object], field_id: int, label: str) -> bool:
    value = values.get(field_id)
    if not isinstance(value, bool):
        raise ParquetMetadataError(f"{label} 必须是布尔值")
    return value


def _build_schema_tree(
    elements: tuple[_RawSchemaElement, ...],
) -> tuple[tuple[ParquetSchemaField, ...], str]:
    root = elements[0]
    if root.name is None or not root.name:
        raise ParquetMetadataError("schema 根节点缺少名称")
    if root.physical_type is not None:
        raise ParquetMetadataError("schema 根节点不能带物理类型")
    if root.num_children is None or root.num_children < 0:
        raise ParquetMetadataError("schema 根节点缺少合法的 num_children")
    fields: list[ParquetSchemaField] = []
    cursor = _SchemaCursor(elements)
    for _ in range(root.num_children):
        cursor.consume((), 0, fields)
    if cursor.index != len(elements):
        raise ParquetMetadataError("schema 层级与 num_children 声明不一致")
    return tuple(fields), root.name


@dataclass(slots=True)
class _SchemaCursor:
    elements: tuple[_RawSchemaElement, ...]
    index: int = 1

    def consume(
        self, parent_path: tuple[str, ...], depth: int, fields: list[ParquetSchemaField]
    ) -> None:
        if depth > MAX_NESTING_DEPTH:
            raise ParquetMetadataError("schema 嵌套超过上限")
        if self.index >= len(self.elements):
            raise ParquetMetadataError("schema 子节点数量与 num_children 声明不一致")
        element = self.elements[self.index]
        self.index += 1
        if element.name is None or not element.name:
            raise ParquetMetadataError("schema 字段缺少名称")
        path = parent_path + (element.name,)
        children = element.num_children or 0
        if children < 0:
            raise ParquetMetadataError("schema 字段的 num_children 不能为负")
        if element.repetition is None:
            raise ParquetMetadataError(f"schema 字段缺少 repetition_type：{'/'.join(path)}")
        if children == 0:
            if element.physical_type is None:
                raise ParquetMetadataError(f"叶子字段缺少物理类型：{'/'.join(path)}")
        elif element.physical_type is not None:
            raise ParquetMetadataError(f"组字段不应带物理类型：{'/'.join(path)}")
        fields.append(
            ParquetSchemaField(
                name=element.name,
                path=path,
                physical_type=element.physical_type,
                logical_type=element.logical_type,
                converted_type=element.converted_type,
                repetition=element.repetition,
                num_children=children,
                type_length=element.type_length,
                scale=element.scale,
                precision=element.precision,
                field_id=element.field_id,
            )
        )
        for _ in range(children):
            self.consume(path, depth + 1, fields)


__all__ = [
    "MAX_FIELD_NAME_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_SCHEMA_ELEMENTS",
    "decode_schema",
]
