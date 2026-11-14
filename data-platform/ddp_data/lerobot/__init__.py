"""LeRobot 格式解析。

当前支持 Human-1M 的 ``preview/metadata.jsonl`` 平铺元数据
（每个相机一个视频文件 + 一行元数据）。真正的 LeRobot v3.0 完整格式
（Parquet 分片 + meta/info.json + episodes/*.parquet）留接口，等拿到
有 parquet 的真实来源再实现——不凭 schema 文档硬造解析器。
"""

from ddp_data.lerobot.parser import (
    CanonicalEpisode,
    CanonicalMediaAsset,
    LeRobotParseError,
    parse_preview_metadata,
)

__all__ = [
    "CanonicalEpisode",
    "CanonicalMediaAsset",
    "LeRobotParseError",
    "parse_preview_metadata",
]
