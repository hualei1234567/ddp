"""Parquet 只读元数据的数据模型（无 IO、无外部依赖）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParquetColumn:
    """PyArrow 元数据中的一个**叶子**列。

    ``path`` 使用 Parquet 的规范点分路径（如 ``observation.state.list.element``）；
    顶层字段名本身可能含点号，因此判断“关键列”只看
    :attr:`ParquetMetadata.top_level_fields`，不解析点分路径。
    """

    name: str
    path: str
    physical_type: str
    logical_type: str | None
    converted_type: str | None
    repetition: str
    max_definition_level: int
    max_repetition_level: int


@dataclass(frozen=True, slots=True)
class ParquetMetadata:
    """footer 中的文件级元数据；只含 schema 与计数，不含行数据。"""

    format_version: str
    num_rows: int
    row_group_count: int
    created_by: str | None
    top_level_fields: tuple[str, ...]
    columns: tuple[ParquetColumn, ...]

    @property
    def leaf_columns(self) -> tuple[ParquetColumn, ...]:
        return self.columns

    def column_by_path(self, path: str) -> ParquetColumn | None:
        for column in self.columns:
            if column.path == path:
                return column
        return None


__all__ = ["ParquetColumn", "ParquetMetadata"]
