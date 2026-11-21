"""ROS Bag（rosbag1 / rosbag2）结构只读探测（DDP-V21-N3-007）。"""

from ddp_data.rosbag.adapter import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    DEFAULT_ROSBAG_ADAPTER,
    RosbagReadOnlyAdapter,
)
from ddp_data.rosbag.errors import (
    RosbagInputError,
    RosbagMetadataError,
    RosbagMetadataLimitError,
    RosbagProbeError,
    RosbagUnavailableError,
    UnsupportedRosbagError,
)
from ddp_data.rosbag.metadata import (
    MAX_MESSAGES,
    MAX_METADATA_BYTES,
    MAX_TEXT_LENGTH,
    MAX_TOPICS,
    ROSBAG1_MAGIC,
    ROSBAG2_METADATA,
    detect_rosbag1,
    detect_rosbag2,
    read_rosbag_structure,
)
from ddp_data.rosbag.models import RosbagStructure, RosbagTopicSummary

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_ROSBAG_ADAPTER",
    "MAX_MESSAGES",
    "MAX_METADATA_BYTES",
    "MAX_TEXT_LENGTH",
    "MAX_TOPICS",
    "ROSBAG1_MAGIC",
    "ROSBAG2_METADATA",
    "RosbagInputError",
    "RosbagMetadataError",
    "RosbagMetadataLimitError",
    "RosbagProbeError",
    "RosbagReadOnlyAdapter",
    "RosbagStructure",
    "RosbagTopicSummary",
    "RosbagUnavailableError",
    "UnsupportedRosbagError",
    "detect_rosbag1",
    "detect_rosbag2",
    "read_rosbag_structure",
]
