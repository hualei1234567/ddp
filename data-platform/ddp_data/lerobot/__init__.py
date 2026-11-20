"""LeRobot 格式解析。

支持 Human-1M preview 元数据，以及由真实 LeRobot v3 ``info.json`` 和 episode
元数据生成 DDP 多流时间 manifest。媒体数据本体仍由对象存储链路管理。
"""

from ddp_data.lerobot.parser import (
    CanonicalEpisode,
    CanonicalMediaAsset,
    LeRobotParseError,
    parse_preview_metadata,
)
from ddp_data.lerobot.v3_time_manifest import build_v3_time_alignment_manifest

__all__ = [
    "CanonicalEpisode",
    "CanonicalMediaAsset",
    "LeRobotParseError",
    "build_v3_time_alignment_manifest",
    "parse_preview_metadata",
]
