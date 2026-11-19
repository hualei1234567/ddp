"""Parquet 只读探测的异常族。

分两类：输入/格式（不是 Parquet、加密、不可读）与元数据（解码失败、超限、
schema 自相矛盾）。全部继承 :class:`ParquetProbeError`，调用方可以统一捕获，
但**不得**把它当成“没有数据”静默吞掉。
"""

from __future__ import annotations


class ParquetProbeError(Exception):
    """Parquet 只读探测失败基类；调用方必须显式处理。"""

    code = "parquet_probe_failed"


class ParquetInputError(ParquetProbeError):
    """输入不是可读的普通文件。"""

    code = "invalid_input"


class NotParquetError(ParquetProbeError):
    """内容不具备 Parquet 文件头/尾标记，或长度结构不成立。"""

    code = "not_parquet"


class ParquetEncryptedError(ParquetProbeError):
    """使用加密 footer（``PARE``），只读 Adapter 不支持。"""

    code = "encrypted_footer"


class ParquetMetadataError(ParquetProbeError):
    """footer 元数据无法解码或自相矛盾。"""

    code = "invalid_metadata"


class ParquetMetadataLimitError(ParquetMetadataError):
    """footer 长度、schema 或行组数超过有界上限。"""

    code = "metadata_limit_exceeded"


__all__ = [
    "NotParquetError",
    "ParquetEncryptedError",
    "ParquetInputError",
    "ParquetMetadataError",
    "ParquetMetadataLimitError",
    "ParquetProbeError",
]
