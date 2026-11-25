"""Canonical v1 的 JSON Schema 导出（V31-017）。

字段声明（``*_FIELDS``）是唯一权威；本模块把它机械翻译成 JSON Schema
（draft 2020-12），供 ``packages/contracts/schemas/canonical-v1/`` 的提交产物与
跨语言消费者使用。测试锁定"提交产物 == 生成结果"，两边一旦漂移立刻失败。

为什么生成而不是手写：6 个实体、近百个字段，手写必然与 ``FieldSpec`` 分叉。
生成器只做**机械**映射（kind → JSON 类型、``*_ns`` → 十进制字符串、可选 →
可空），语义仍只存在于 ``v1.py``。

不表达的内容：

- 跨字段约束（Frame/Action/Observation 至少一个时间戳、``end >= start``、
  ``is_dropped`` 与 ``drop_reason`` 一致）留在 ``v1.py`` 的 ``__post_init__``。
  JSON Schema 只能表达单字段约束，硬塞 ``if/then`` 会让 schema 不可读，
  且与领域校验形成两份真相。
- 不生成外部 ``$ref``：每个实体自包含，便于单独校验与跨语言消费。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from ddp_data.canonical.v1 import (
    ACTION_FIELDS,
    EPISODE_FIELDS,
    FRAME_FIELDS,
    OBSERVATION_FIELDS,
    SENSOR_STREAM_FIELDS,
    SUPPORTED_SCHEMA_VERSIONS,
    VIDEO_STREAM_FIELDS,
    FieldSpec,
)

# 纳秒字段在 ``to_dict`` 中输出十进制字符串（避免 JSON 客户端 2^53 精度丢失），
# 因此 ``kind="int"`` 的 ``*_ns`` 字段在 schema 里是字符串而非整数。
_NS_PATTERN = "^-?(0|[1-9][0-9]*)$"

# kind → JSON Schema 类型片段。``mapping`` 只声明为对象，不约束内部结构：
# ``raw`` 等字段的键由来源决定，收紧会拒绝合法数据。
_JSON_TYPES: Mapping[str, dict[str, object]] = {
    "text": {"type": "string"},
    "int": {"type": "integer"},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "str_tuple": {"type": "array", "items": {"type": "string"}},
    "int_tuple": {"type": "array", "items": {"type": "integer"}},
    "number_tuple": {"type": "array", "items": {"type": "number"}},
    "mapping": {"type": "object"},
}

_NUMERIC_KINDS = frozenset({"int", "number"})


@dataclass(frozen=True, slots=True)
class EntitySchema:
    """一个 Canonical 实体的 schema 元信息与字段声明。"""

    name: str
    slug: str
    title: str
    description: str
    fields: tuple[FieldSpec, ...]

    @property
    def filename(self) -> str:
        return f"{self.slug}.v1.schema.json"

    @property
    def schema_id(self) -> str:
        return f"https://ddp.local/schemas/canonical/{self.filename}"


ENTITIES: tuple[EntitySchema, ...] = (
    EntitySchema(
        name="Frame",
        slug="frame",
        title="DDP Canonical Frame v1",
        description=(
            "帧级时间锚点；帧数据本体不内联，只存引用与时间信息。"
            "至少需要 timestamp_ns 或 source_time_ns 之一（跨字段约束由领域层强制）。"
        ),
        fields=FRAME_FIELDS,
    ),
    EntitySchema(
        name="VideoStream",
        slug="video-stream",
        title="DDP Canonical VideoStream v1",
        description="视频/相机流的一条声明；媒体本体在对象存储。",
        fields=VIDEO_STREAM_FIELDS,
    ),
    EntitySchema(
        name="SensorStream",
        slug="sensor-stream",
        title="DDP Canonical SensorStream v1",
        description="非视频传感器流（IMU / 关节 / 力/力矩等）的声明。",
        fields=SENSOR_STREAM_FIELDS,
    ),
    EntitySchema(
        name="Action",
        slug="action",
        title="DDP Canonical Action v1",
        description=(
            "动作序列/动作指令的声明；数值本体可放对象存储。"
            "至少需要 start_time_ns 或 end_time_ns 之一，且 end 不早于 start（领域层强制）。"
        ),
        fields=ACTION_FIELDS,
    ),
    EntitySchema(
        name="Observation",
        slug="observation",
        title="DDP Canonical Observation v1",
        description=(
            "一次传感器观测的声明；payload 由引用指向，不内联大数据。"
            "至少需要 timestamp_ns 或 source_time_ns 之一（领域层强制）。"
        ),
        fields=OBSERVATION_FIELDS,
    ),
    EntitySchema(
        name="Episode",
        slug="episode",
        title="DDP Canonical Episode v1",
        description=(
            "Episode 级 Canonical：保留既有字段，并用 ID 引用下层实体。"
            "video_stream_ids 承接既有 media_assets；episode_id / project_id 由服务端分配。"
        ),
        fields=EPISODE_FIELDS,
    ),
)


def _nullable(schema: Mapping[str, object]) -> dict[str, object]:
    """可选字段 → 可空：``type`` 追加 ``"null"``。"""
    result = dict(schema)
    kind = result["type"]
    if isinstance(kind, str):
        result["type"] = [kind, "null"]
    else:  # pragma: no cover - 当前所有 kind 都是单字符串
        result["type"] = [*kind, "null"]
    return result


def _field_schema(spec: FieldSpec) -> dict[str, object]:
    if spec.name.endswith("_ns"):
        # 纳秒字段是十进制字符串；``minimum`` 是领域层的整数下界，对字符串
        # 无意义，不能泄漏进 schema（否则 ``uncertainty_ns`` 会带一个永远
        # 不生效的 ``minimum``）。
        base: dict[str, object] = {"type": "string", "pattern": _NS_PATTERN}
    else:
        base = dict(_JSON_TYPES[spec.kind])
        if spec.minimum is not None and spec.kind in _NUMERIC_KINDS:
            base["minimum"] = spec.minimum
    return base if spec.required else _nullable(base)


def _missing_fields_schema(entity: EntitySchema) -> dict[str, object]:
    """``missing_fields`` 只能引用可选字段名；枚举把拼写错误挡在校验期。"""
    optional = sorted(spec.name for spec in entity.fields if not spec.required)
    return {
        "type": "array",
        "items": {"type": "string", "enum": optional},
        "uniqueItems": True,
    }


def build_entity_schema(entity: EntitySchema) -> dict[str, object]:
    """把一个实体声明翻译成自包含的 JSON Schema。"""
    properties: dict[str, object] = {
        # 用 enum 而非 const：1.0 产物仍须通过校验（ADR-0049 要求向后兼容）。
        # 只写 const=当前版本会让所有历史产物校验失败——那不是"更严格"，
        # 是**破坏了可读性**。
        "schema_version": {
            "type": "string",
            "enum": sorted(SUPPORTED_SCHEMA_VERSIONS),
        },
    }
    required: list[str] = ["schema_version"]
    for spec in entity.fields:
        properties[spec.name] = _field_schema(spec)
        if spec.required:
            required.append(spec.name)
    properties["missing_fields"] = _missing_fields_schema(entity)
    required.append("missing_fields")
    # 1.0 兼容：1.0 产物允许省略 1.1 新增的可选字段（这些字段在 properties 中已定义，
    # 且均为可选，不在 required 列表中，因此 JSON Schema 天然允许省略或置 null）。
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": entity.schema_id,
        "title": entity.title,
        "description": entity.description,
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def canonical_json_schemas() -> dict[str, dict[str, object]]:
    """返回 ``{文件名: schema}``，文件名即提交到契约包时的名字。"""
    return {entity.filename: build_entity_schema(entity) for entity in ENTITIES}


def render_json_schema(schema: Mapping[str, object]) -> str:
    """稳定的 JSON 文本：缩进 2、保留插入序、末尾换行。"""
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "ENTITIES",
    "EntitySchema",
    "build_entity_schema",
    "canonical_json_schemas",
    "render_json_schema",
]
