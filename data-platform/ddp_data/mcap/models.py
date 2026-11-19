"""MCAP 只读元数据的数据模型（无 IO、无外部依赖）。

所有字段都是**有界摘要**：不携带消息体、schema 原始字节或 channel metadata
的具体值，避免把大对象或敏感内容带出探测层。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McapSchemaSummary:
    """summary 中的一个 schema；只保留标识与长度，不保留 ``data`` 内容。"""

    schema_id: int
    name: str
    encoding: str
    data_bytes: int


@dataclass(frozen=True, slots=True)
class McapChannelSummary:
    """summary 中一个 channel 的 topic/编码/schema 关联。

    ``schema_id == 0`` 表示 channel 不引用 schema，此时 ``schema_name`` 与
    ``schema_encoding`` 为 ``None``。``message_count`` 仅在 summary 含
    statistics 时可得，否则为 ``None``（未知，不是 0）。
    """

    channel_id: int
    topic: str
    message_encoding: str
    schema_id: int
    schema_name: str | None
    schema_encoding: str | None
    message_count: int | None
    metadata_entry_count: int


@dataclass(frozen=True, slots=True)
class McapMetadata:
    """一次只读探测的完整产物。

    ``has_summary=False`` 时，channel/schema/消息计数均为 ``None``（未知），
    ``channels``/``schemas`` 为空元组——调用方必须以 ``has_summary`` 判断，
    不能把空元组当成“文件里没有 channel”。

    另一种情况是 summary 只重复了 statistics、没有重复 channel/schema 记录：
    此时 ``has_summary=True`` 且计数来自 statistics，但 ``channels``/``schemas``
    为空元组表示“明细未收录”，不是“数量为 0”。

    ``message_start_time_ns`` / ``message_end_time_ns`` 仅在 summary 含
    statistics 且 ``message_count > 0`` 时给出；时间单位是纳秒，语义由写入方
    定义（通常是 Unix epoch 或机器人启动时刻），本模块不做时区换算。
    """

    profile: str
    library: str
    has_summary: bool
    channel_count: int | None
    schema_count: int | None
    message_count: int | None
    message_start_time_ns: int | None
    message_end_time_ns: int | None
    channels: tuple[McapChannelSummary, ...]
    schemas: tuple[McapSchemaSummary, ...]

    def as_metadata(self) -> dict[str, object]:
        """有界、可 JSON 序列化的摘要；不含路径与消息体。

        调用方可把它嵌进 ``RawAsset.raw_metadata``（如 ``{"mcap": ...}``），
        与既有导入来源留痕并存，无需新增数据库列。
        """
        return {
            "profile": self.profile,
            "library": self.library,
            "has_summary": self.has_summary,
            "channel_count": self.channel_count,
            "schema_count": self.schema_count,
            "message_count": self.message_count,
            "message_start_time_ns": self.message_start_time_ns,
            "message_end_time_ns": self.message_end_time_ns,
            "channels": [
                {
                    "channel_id": channel.channel_id,
                    "topic": channel.topic,
                    "message_encoding": channel.message_encoding,
                    "schema_name": channel.schema_name,
                    "schema_encoding": channel.schema_encoding,
                    "message_count": channel.message_count,
                }
                for channel in self.channels
            ],
            "schemas": [
                {
                    "schema_id": schema.schema_id,
                    "name": schema.name,
                    "encoding": schema.encoding,
                    "data_bytes": schema.data_bytes,
                }
                for schema in self.schemas
            ],
        }


__all__ = ["McapChannelSummary", "McapMetadata", "McapSchemaSummary"]
