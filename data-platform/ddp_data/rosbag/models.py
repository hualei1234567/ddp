"""ROS Bag 只读结构摘要模型（无 IO、无外部依赖）。

摘要只保留 topic、消息类型、数量与时间范围，不携带消息体、消息定义、QoS
详情或本地路径，避免把大对象和潜在敏感内容带出探测层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RosbagTopicSummary:
    """一个 topic 的有界结构摘要。"""

    topic: str
    msgtype: str
    message_count: int


@dataclass(frozen=True, slots=True)
class RosbagStructure:
    """一次 rosbag1/rosbag2 只读结构探测结果。"""

    adapter_id: str
    adapter_version: str
    format: Literal["rosbag1", "rosbag2"]
    topic_count: int
    message_count: int
    start_time_ns: int | None
    end_time_ns: int | None
    duration_ns: int | None
    topics: tuple[RosbagTopicSummary, ...]

    def as_metadata(self) -> dict[str, object]:
        """返回有界、可 JSON 序列化且不含消息体的摘要。"""
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "format": self.format,
            "topic_count": self.topic_count,
            "message_count": self.message_count,
            "start_time_ns": self.start_time_ns,
            "end_time_ns": self.end_time_ns,
            "duration_ns": self.duration_ns,
            "topics": [
                {
                    "topic": item.topic,
                    "msgtype": item.msgtype,
                    "message_count": item.message_count,
                }
                for item in self.topics
            ],
        }


__all__ = ["RosbagStructure", "RosbagTopicSummary"]
