"""Parquet 只读元数据的数据模型（无 IO、无依赖）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParquetSchemaField:
    """schema 树中的一个字段（根节点不包含在内）。

    ``path`` 是完整路径元组（含自身）；字段名本身可能含点号（如 LeRobot 的
    ``observation.state``），因此路径不做字符串拼接解析。
    """

    name: str
    path: tuple[str, ...]
    physical_type: str | None
    logical_type: str | None
    converted_type: str | None
    repetition: str
    num_children: int
    type_length: int | None = None
    scale: int | None = None
    precision: int | None = None
    field_id: int | None = None

    @property
    def is_leaf(self) -> bool:
        return self.num_children == 0


@dataclass(frozen=True, slots=True)
class ParquetMetadata:
    """footer 中的文件级元数据；只含 schema 与计数，不含行数据。"""

    version: int
    num_rows: int
    row_group_count: int
    root_name: str
    created_by: str | None
    fields: tuple[ParquetSchemaField, ...]

    @property
    def top_level_fields(self) -> tuple[ParquetSchemaField, ...]:
        return tuple(field for field in self.fields if len(field.path) == 1)

    @property
    def leaf_columns(self) -> tuple[ParquetSchemaField, ...]:
        return tuple(field for field in self.fields if field.is_leaf)

    def field_by_path(self, path: tuple[str, ...]) -> ParquetSchemaField | None:
        for field in self.fields:
            if field.path == path:
                return field
        return None


__all__ = ["ParquetMetadata", "ParquetSchemaField"]
