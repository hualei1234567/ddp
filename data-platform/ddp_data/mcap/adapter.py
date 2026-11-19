"""MCAP 只读 Adapter：元数据探测入口（DDP-V21-N3-005）。

**输出为什么不是 Canonical Episode**

``CanonicalEpisode`` 的必填字段是 ``episode_key`` / ``task_name`` /
``instruction`` / ``media_assets`` 等**数据集语义**。一个孤立的 MCAP 文件只有
header、channel、schema 与带时间的消息流，没有 Episode 标识、指令文本或媒体
资产；若硬映射，只能编造默认值，这违反 Canonical 的“不凭空补齐”原则。
因此本 Adapter 输出 :class:`McapMetadata`：真实的 profile/library、有界的
channel/schema 摘要，以及 summary 可得时的消息计数与时间范围。

**为什么不注册进 ``AdapterRegistry``**

理由与 ``parquet.table_readonly``（DDP-V21-N3-003）一致：注册表的 ``parser``
契约是 ``bytes -> tuple[CanonicalEpisode, ...]``，且注册时必须穷尽声明
Canonical Episode 字段。把本模块注册进去只有两种结果：伪造 Episode 字段，
或让 ``detect`` 命中 MCAP 后在导入链路里必然失败。两者都违反“未知/不支持必须
显式失败、不得伪装成功”。因此本 Adapter 由调用方显式选择，不在内置 Episode
注册表中占位；``adapter_id`` / ``version`` 保持稳定以便留痕。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ddp_data.mcap.metadata import (
    MAX_CHANNELS,
    MAX_METADATA_BYTES,
    MAX_SCHEMAS,
    MCAP_MAGIC,
    MIN_FILE_BYTES,
    read_mcap_metadata,
)
from ddp_data.mcap.models import McapMetadata

ADAPTER_ID = "mcap.metadata_readonly"
ADAPTER_VERSION = "1.0.0"


def detect_mcap(sample: bytes) -> bool:
    """按**完整文件字节**判断是否为 MCAP；截断片段不会命中。

    MCAP 的格式证据是首尾 8 字节魔数。只有文件头没有文件尾不能证明是
    MCAP，因此不做“前缀命中”的宽松判断。
    """
    if len(sample) < MIN_FILE_BYTES:
        return False
    if not sample.startswith(MCAP_MAGIC):
        return False
    return sample.endswith(MCAP_MAGIC)


def summarize_mcap(
    path: str | os.PathLike[str],
    *,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    max_channels: int = MAX_CHANNELS,
    max_schemas: int = MAX_SCHEMAS,
) -> McapMetadata:
    """读取 MCAP 并产出有界元数据摘要。"""
    return read_mcap_metadata(
        path,
        max_metadata_bytes=max_metadata_bytes,
        max_channels=max_channels,
        max_schemas=max_schemas,
    )


@dataclass(frozen=True, slots=True)
class McapReadOnlyAdapter:
    """稳定的只读入口；``adapter_id``/``version`` 用于调用方留痕。

    它刻意只做 ``detect`` / ``read_metadata`` / ``inspect``：没有 ``parse``
    到 Canonical Episode 的能力，也不声称有（原因见模块 docstring）。
    """

    adapter_id: str = ADAPTER_ID
    version: str = ADAPTER_VERSION

    def detect(self, sample: bytes) -> bool:
        return detect_mcap(sample)

    def read_metadata(
        self,
        path: str | os.PathLike[str],
        *,
        max_metadata_bytes: int = MAX_METADATA_BYTES,
        max_channels: int = MAX_CHANNELS,
        max_schemas: int = MAX_SCHEMAS,
    ) -> McapMetadata:
        return read_mcap_metadata(
            path,
            max_metadata_bytes=max_metadata_bytes,
            max_channels=max_channels,
            max_schemas=max_schemas,
        )

    def inspect(
        self,
        path: str | os.PathLike[str],
        *,
        max_metadata_bytes: int = MAX_METADATA_BYTES,
        max_channels: int = MAX_CHANNELS,
        max_schemas: int = MAX_SCHEMAS,
    ) -> McapMetadata:
        return summarize_mcap(
            path,
            max_metadata_bytes=max_metadata_bytes,
            max_channels=max_channels,
            max_schemas=max_schemas,
        )


DEFAULT_MCAP_ADAPTER = McapReadOnlyAdapter()


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_MCAP_ADAPTER",
    "McapReadOnlyAdapter",
    "detect_mcap",
    "summarize_mcap",
]
