"""MCAP 只读探测的异常族（DDP-V21-N3-005）。

分三类：输入（缺文件/不是普通文件）、格式（不是 MCAP、损坏、SDK 不可用）、
元数据（解码失败、超限、summary 自相矛盾）。全部继承 :class:`McapProbeError`，
调用方可以统一捕获，但**不得**把它当成“没有数据”静默吞掉。
"""

from __future__ import annotations


class McapProbeError(Exception):
    """MCAP 只读探测失败基类；调用方必须显式处理。"""

    code = "mcap_probe_failed"


class McapInputError(McapProbeError):
    """输入不是可读的普通文件。"""

    code = "invalid_input"


class NotMcapError(McapProbeError):
    """内容缺少 MCAP 首尾魔数，或长度结构不成立。"""

    code = "not_mcap"


class McapUnavailableError(McapProbeError):
    """运行环境缺少 mcap 官方 SDK。"""

    code = "mcap_unavailable"


class McapMetadataError(McapProbeError):
    """footer/summary 元数据无法解码或自相矛盾。"""

    code = "invalid_metadata"


class McapMetadataLimitError(McapMetadataError):
    """summary 字节、channel/schema 数或文本长度超过有界上限。"""

    code = "metadata_limit_exceeded"


__all__ = [
    "McapInputError",
    "McapMetadataError",
    "McapMetadataLimitError",
    "McapProbeError",
    "McapUnavailableError",
    "NotMcapError",
]
