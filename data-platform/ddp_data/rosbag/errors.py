"""ROS Bag 只读结构探测的异常族（DDP-V21-N3-007）。

分四类：输入（缺路径/不是 bag）、格式（不认识或已损坏）、环境（官方 SDK
不可用）、元数据（超限、自相矛盾）。全部继承 :class:`RosbagProbeError`，
调用方可以统一捕获，但**不得**把它当成“没有数据”静默吞掉。
"""

from __future__ import annotations


class RosbagProbeError(Exception):
    """ROS Bag 只读探测失败基类；调用方必须显式处理。"""

    code = "rosbag_probe_failed"


class RosbagInputError(RosbagProbeError):
    """输入路径不存在或不可访问。"""

    code = "invalid_input"


class UnsupportedRosbagError(RosbagProbeError):
    """输入既不是 rosbag2 目录也不是 rosbag1 文件，或结构已损坏。"""

    code = "not_rosbag"


class RosbagUnavailableError(RosbagProbeError):
    """运行环境缺少官方 rosbags SDK。"""

    code = "rosbag_unavailable"


class RosbagMetadataError(RosbagProbeError):
    """bag 元数据无法解码或自相矛盾。"""

    code = "invalid_metadata"


class RosbagMetadataLimitError(RosbagMetadataError):
    """topic 数、消息数或文本长度超过有界上限。"""

    code = "metadata_limit_exceeded"


__all__ = [
    "RosbagInputError",
    "RosbagMetadataError",
    "RosbagMetadataLimitError",
    "RosbagProbeError",
    "RosbagUnavailableError",
    "UnsupportedRosbagError",
]
