"""Parquet 只读 Adapter：footer 元数据、schema、表预览（DDP-V21-N3-003）。

只做“读元数据”，不做数据页解码、转码或落盘；结果有界、错误显式。
"""

from ddp_data.parquet._schema import (
    MAX_FIELD_NAME_BYTES,
    MAX_NESTING_DEPTH,
    MAX_SCHEMA_ELEMENTS,
)
from ddp_data.parquet.adapter import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    DEFAULT_PARQUET_ADAPTER,
    LEROBOT_TABULAR_COLUMNS,
    ExpectedColumn,
    ParquetColumnSummary,
    ParquetLayout,
    ParquetReadOnlyAdapter,
    ParquetTablePreview,
    detect_parquet,
    summarize_metadata,
    summarize_parquet,
)
from ddp_data.parquet.errors import (
    NotParquetError,
    ParquetEncryptedError,
    ParquetInputError,
    ParquetMetadataError,
    ParquetMetadataLimitError,
    ParquetProbeError,
)
from ddp_data.parquet.metadata import (
    MAX_CREATED_BY_BYTES,
    MAX_METADATA_BYTES,
    MAX_ROW_GROUPS,
    read_parquet_metadata,
    read_parquet_metadata_from,
)
from ddp_data.parquet.models import ParquetMetadata, ParquetSchemaField

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_PARQUET_ADAPTER",
    "LEROBOT_TABULAR_COLUMNS",
    "MAX_CREATED_BY_BYTES",
    "MAX_FIELD_NAME_BYTES",
    "MAX_METADATA_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_ROW_GROUPS",
    "MAX_SCHEMA_ELEMENTS",
    "ExpectedColumn",
    "NotParquetError",
    "ParquetColumnSummary",
    "ParquetEncryptedError",
    "ParquetInputError",
    "ParquetLayout",
    "ParquetMetadata",
    "ParquetMetadataError",
    "ParquetMetadataLimitError",
    "ParquetProbeError",
    "ParquetReadOnlyAdapter",
    "ParquetSchemaField",
    "ParquetTablePreview",
    "detect_parquet",
    "read_parquet_metadata",
    "read_parquet_metadata_from",
    "summarize_metadata",
    "summarize_parquet",
]
