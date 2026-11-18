"""Parquet 只读 Adapter：footer 元数据、schema、表预览（DDP-V21-N3-003）。

只做“读元数据”，不做数据页解码、转码或落盘；结果有界、错误显式。
底层使用官方 PyArrow。
"""

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
    MAX_METADATA_BYTES,
    read_parquet_metadata,
    read_parquet_metadata_from,
)
from ddp_data.parquet.models import ParquetColumn, ParquetMetadata

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_PARQUET_ADAPTER",
    "LEROBOT_TABULAR_COLUMNS",
    "MAX_METADATA_BYTES",
    "ExpectedColumn",
    "NotParquetError",
    "ParquetColumn",
    "ParquetColumnSummary",
    "ParquetEncryptedError",
    "ParquetInputError",
    "ParquetLayout",
    "ParquetMetadata",
    "ParquetMetadataError",
    "ParquetMetadataLimitError",
    "ParquetProbeError",
    "ParquetReadOnlyAdapter",
    "ParquetTablePreview",
    "detect_parquet",
    "read_parquet_metadata",
    "read_parquet_metadata_from",
    "summarize_metadata",
    "summarize_parquet",
]
