"""ROS Bag 完整索引到 Processing Stage 有界摘要的投影。"""

from __future__ import annotations

import json

from ddp_data.rosbag import RosbagStructure

#: Stage 最多保留的 topic 明细；完整 topic/message 总数始终保留。
MAX_ROSBAG_TOPIC_DETAILS = 256
#: 给 256 KiB Stage 外层文档和 source_ref 预留空间。
MAX_ROSBAG_PAYLOAD_BYTES = 192 * 1024


def rosbag_stage_payload(structure: RosbagStructure) -> dict[str, object]:
    """投影 ROS Bag 索引，不读取消息体，并显式报告省略的 topic。"""
    payload: dict[str, object] = {
        "adapter_id": structure.adapter_id,
        "adapter_version": structure.adapter_version,
        "format": structure.format,
        "topic_count": structure.topic_count,
        "message_count": structure.message_count,
        "start_time_ns": structure.start_time_ns,
        "end_time_ns": structure.end_time_ns,
        "duration_ns": structure.duration_ns,
        "topics": [],
        "omitted_topic_count": structure.topic_count,
        "details_truncated": bool(structure.topic_count),
    }
    topics = payload["topics"]
    if not isinstance(topics, list):  # pragma: no cover - 本地构造保证
        raise TypeError("ROS Bag metadata topics 必须是列表")
    for item in structure.topics[:MAX_ROSBAG_TOPIC_DETAILS]:
        topics.append(
            {
                "topic": item.topic,
                "msgtype": item.msgtype,
                "message_count": item.message_count,
            }
        )
        payload["omitted_topic_count"] = structure.topic_count - len(topics)
        payload["details_truncated"] = bool(payload["omitted_topic_count"])
        if len(_encode(payload)) > MAX_ROSBAG_PAYLOAD_BYTES:
            topics.pop()
            payload["omitted_topic_count"] = structure.topic_count - len(topics)
            payload["details_truncated"] = True
            break
    return payload


def _encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


__all__ = [
    "MAX_ROSBAG_PAYLOAD_BYTES",
    "MAX_ROSBAG_TOPIC_DETAILS",
    "rosbag_stage_payload",
]
