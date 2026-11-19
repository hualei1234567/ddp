"""MCAP 只读元数据 Adapter：footer/summary/channel/schema（DDP-V21-N3-005）。

只做“读元数据”，不解码消息体、不读取 chunk、不写文件；结果有界、错误显式。
底层使用官方 mcap Python SDK。
"""

from ddp_data.mcap.adapter import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    DEFAULT_MCAP_ADAPTER,
    McapReadOnlyAdapter,
    detect_mcap,
    summarize_mcap,
)
from ddp_data.mcap.errors import (
    McapInputError,
    McapMetadataError,
    McapMetadataLimitError,
    McapProbeError,
    McapUnavailableError,
    NotMcapError,
)
from ddp_data.mcap.metadata import (
    MAX_CHANNELS,
    MAX_METADATA_BYTES,
    MAX_SCHEMAS,
    read_mcap_metadata,
    read_mcap_metadata_from,
)
from ddp_data.mcap.models import McapChannelSummary, McapMetadata, McapSchemaSummary

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_MCAP_ADAPTER",
    "MAX_CHANNELS",
    "MAX_METADATA_BYTES",
    "MAX_SCHEMAS",
    "McapChannelSummary",
    "McapInputError",
    "McapMetadata",
    "McapMetadataError",
    "McapMetadataLimitError",
    "McapProbeError",
    "McapReadOnlyAdapter",
    "McapSchemaSummary",
    "McapUnavailableError",
    "NotMcapError",
    "detect_mcap",
    "read_mcap_metadata",
    "read_mcap_metadata_from",
    "summarize_mcap",
]
