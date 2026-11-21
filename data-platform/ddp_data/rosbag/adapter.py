"""ROS Bag 只读 Adapter：rosbag1/rosbag2 结构探测入口。

不注册 ``AdapterRegistry``：注册表 parser 契约必须产出 Canonical Episode，而
ROS Bag 索引不包含 task/instruction/media asset 等权威语义。硬映射只能编造字段，
因此调用方显式选择本 Adapter，并用稳定 adapter_id/version 留痕。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ddp_data.rosbag.metadata import read_rosbag_structure
from ddp_data.rosbag.models import RosbagStructure

ADAPTER_ID = "rosbag.structure_readonly"
ADAPTER_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class RosbagReadOnlyAdapter:
    """稳定的只读结构探测入口。"""

    adapter_id: str = ADAPTER_ID
    version: str = ADAPTER_VERSION

    def inspect(self, path: str | os.PathLike[str]) -> RosbagStructure:
        return read_rosbag_structure(path)


DEFAULT_ROSBAG_ADAPTER = RosbagReadOnlyAdapter()


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_ROSBAG_ADAPTER",
    "RosbagReadOnlyAdapter",
]
