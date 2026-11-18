"""媒体文件只读探测（MP4 metadata 等）。

只做“读元数据”，不做转码、切片或落盘；结果有界、错误显式。
"""

from ddp_data.media.mp4 import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    MAX_STREAMS,
    MediaProbeError,
    MediaStreamInfo,
    Mp4ProbeResult,
    NotMp4Error,
    ProbeFailedError,
    ProbeInputError,
    ProbeOutputLimitError,
    ProbeTimeoutError,
    ProbeUnavailableError,
    probe_mp4,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_OUTPUT_BYTES",
    "MAX_STREAMS",
    "MediaProbeError",
    "Mp4ProbeResult",
    "NotMp4Error",
    "ProbeFailedError",
    "ProbeInputError",
    "ProbeOutputLimitError",
    "ProbeTimeoutError",
    "ProbeUnavailableError",
    "MediaStreamInfo",
    "probe_mp4",
]
