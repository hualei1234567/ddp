"""Canonical Schema v1：版本化的只读数据契约（DDP-V21-N3-001）。

实体：``Episode`` / ``Frame`` / ``VideoStream`` / ``SensorStream`` / ``Action`` /
``Observation``。这是**契约**而不是持久化模型：没有业务表、没有 ORM、没有 IO。

三条硬规则（ADR-0035）：

1. 每个实体都带 ``schema_version = ddp.canonical/1.0.0``；读写只接受精确版本。
2. 缺失字段必须显式：值为空（None/空串/空元组/空映射）时，必须同时出现在
   ``missing_fields`` 中，否则构造失败。禁止用 0、空动作或默认值冒充数据。
3. ``*_ns`` 时间戳内部存 int 纳秒，JSON 输出十进制字符串；不得只留一个
   无法解释来源的时间戳。

字段命名与 `docs/data-standards/Canonical草案.md` 对齐；Episode 保留现有
``CanonicalEpisode`` 的字段名，以便 Adapter 声明与历史导入可解释。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from ddp_data.canonical._schema import (
    CanonicalFieldError,
    CanonicalSchemaError,
    CanonicalSchemaVersionError,
    FieldSpec,
    check_schema_version,
    compute_missing_fields,
    entity_from_dict,
    entity_to_dict,
    validate_entity,
)

SCHEMA_VERSION = "ddp.canonical/1.1.0"
#: 1.0.0 仍可**读取**（历史产物不因版本升级而变得不可解析）；
#: 新产物一律写 1.1.0。1.1 在 1.0 基础上**只增加可选字段**，无删除、无收紧。
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION, "ddp.canonical/1.0.0"})
#: 1.1 新增字段（1.0 产物不含，读取 1.0 时不得要求这些字段）。
SCHEMA_1_1_ONLY_FIELDS = frozenset(
    {
        # SensorStream
        "dtype",
        "start_timestamp_ns",
        "end_timestamp_ns",
        "missing_count",
        "invalid_count",
        "parser_version",
        "sensor_id",
        "device_id",
        # Observation / Action（共用同名的新增字段）
        "timestamp_raw_ns",
        "timestamp_normalized_ns",
        "validity",
        "source_index",
        "source_offset",
        "semantics_version",
        "transform_provenance",
    }
)


# 草案 §4.1 的坐标约定是闭合集合；出现未知值时显式失败，而不是当成合法值。
FRAME_CONVENTIONS = frozenset({"FLU", "RDF", "OPTICAL", "ENU", "NED"})

# Episode 级：现有 Adapter 声明的 ``media_assets`` 在 v1 中由视频流引用承接。
# 该映射是唯一的字段改名，登记在此供 Adapter 声明一致性检查使用。
LEGACY_EPISODE_FIELD_MAPPING: Mapping[str, str] = {"media_assets": "video_stream_ids"}

FRAME_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("frame_index", required=True, kind="int", minimum=0),
    FieldSpec("sequence", kind="int", minimum=0),
    FieldSpec("timestamp_ns", kind="int"),
    FieldSpec("source_time_ns", kind="int"),
    FieldSpec("source_time_domain"),
    FieldSpec("clock_id"),
    FieldSpec("time_base_ref"),
    FieldSpec("uncertainty_ns", kind="int", minimum=0),
    FieldSpec("observation_ids", kind="str_tuple"),
    FieldSpec("is_dropped", kind="bool"),
    FieldSpec("drop_reason"),
)

VIDEO_STREAM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("stream_id", required=True),
    FieldSpec("camera_name"),
    FieldSpec("camera_name_raw"),
    FieldSpec("modality"),
    FieldSpec("media_format"),
    FieldSpec("storage_reference"),
    FieldSpec("excerpt_start_ns", kind="int", minimum=0),
    FieldSpec("duration_ns", kind="int", minimum=0),
    FieldSpec("fps", kind="number", minimum=0),
    FieldSpec("frame_count", kind="int", minimum=0),
    FieldSpec("width", kind="int", minimum=1),
    FieldSpec("height", kind="int", minimum=1),
    FieldSpec("size_bytes", kind="int", minimum=0),
    FieldSpec("checksum"),
    FieldSpec("encoding"),
    FieldSpec("dropped_count", kind="int", minimum=0),
)

SENSOR_STREAM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("stream_id", required=True),
    FieldSpec("sensor_name"),
    FieldSpec("sensor_type"),
    FieldSpec("modality"),
    FieldSpec("sample_rate_hz", kind="number", minimum=0),
    FieldSpec("channel_names", kind="str_tuple"),
    FieldSpec("unit"),
    FieldSpec("quantity_kind"),
    FieldSpec("frame_id"),
    FieldSpec("frame_convention"),
    FieldSpec("duration_ns", kind="int", minimum=0),
    FieldSpec("sample_count", kind="int", minimum=0),
    FieldSpec("storage_reference"),
    FieldSpec("checksum"),
    FieldSpec("calibration_id"),
    FieldSpec("calibration_version", kind="int", minimum=1),
    # --- 1.1 新增（全部可选；1.0 产物不含这些字段）---
    # dtype：下游反序列化必须知道数值类型，否则只能猜。
    FieldSpec("dtype"),
    # 绝对起止时间：1.0 只有 duration_ns，无法定位到时间轴上的具体位置。
    FieldSpec("start_timestamp_ns", kind="int", minimum=0),
    FieldSpec("end_timestamp_ns", kind="int", minimum=0),
    # 缺失/无效样本数：蓝图 §7 要求量化数据完整度，而不是只说"有缺失"。
    FieldSpec("missing_count", kind="int", minimum=0),
    FieldSpec("invalid_count", kind="int", minimum=0),
    # parser_version：与 schema_version 正交——同一 schema 版本可由不同解析器产出。
    FieldSpec("parser_version"),
    # UUID 关联：1.0 只有 sensor_name（字符串），无法与设备表外键对齐。
    FieldSpec("sensor_id"),
    FieldSpec("device_id"),
)

ACTION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("action_id", required=True),
    FieldSpec("episode_key", required=True),
    FieldSpec("action_type", required=True),
    FieldSpec("start_time_ns", kind="int"),
    FieldSpec("end_time_ns", kind="int"),
    FieldSpec("values", kind="number_tuple"),
    FieldSpec("unit"),
    FieldSpec("frame_id"),
    FieldSpec("joint_names", kind="str_tuple"),
    FieldSpec("source"),
    FieldSpec("confidence", kind="number", minimum=0),
    FieldSpec("action_schema_ref"),
    FieldSpec("frame_index", kind="int", minimum=0),
    # --- 1.1 新增（全部可选，对齐 ADR-0049 §3.2）---
    FieldSpec("timestamp_raw_ns", kind="int"),
    FieldSpec("timestamp_normalized_ns", kind="int"),
    FieldSpec("dtype"),
    FieldSpec("validity"),
    FieldSpec("parser_version"),
    FieldSpec("source_index", kind="int", minimum=0),
    FieldSpec("source_offset", kind="int", minimum=0),
    # semantics_version：动作语义版本。**与 action_schema_ref 不同**：
    # 后者指向 Schema 定义，前者表示"这批动作按哪一版语义解释"
    # （例如末端 delta 的坐标系约定变了）。蓝图 §5 明确要求。
    FieldSpec("semantics_version"),
)

OBSERVATION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("observation_id", required=True),
    FieldSpec("episode_key", required=True),
    FieldSpec("observation_type", required=True),
    FieldSpec("stream_id"),
    FieldSpec("sensor_id"),
    FieldSpec("frame_index", kind="int", minimum=0),
    FieldSpec("timestamp_ns", kind="int"),
    FieldSpec("source_time_ns", kind="int"),
    FieldSpec("sequence", kind="int", minimum=0),
    FieldSpec("frame_id"),
    FieldSpec("calibration_id"),
    FieldSpec("calibration_version", kind="int", minimum=1),
    FieldSpec("unit"),
    FieldSpec("quantity_kind"),
    FieldSpec("shape", kind="int_tuple", minimum=0),
    FieldSpec("dtype"),
    FieldSpec("encoding"),
    FieldSpec("payload_ref"),
    FieldSpec("checksum"),
    FieldSpec("valid", kind="bool"),
    FieldSpec("invalid_reason"),
    FieldSpec("quality_flags", kind="str_tuple"),
    # --- 1.1 新增（全部可选，对齐 ADR-0049 §3.2）---
    FieldSpec("timestamp_raw_ns", kind="int"),
    FieldSpec("timestamp_normalized_ns", kind="int"),
    FieldSpec("validity"),
    # parser_version：与 schema_version **正交**——同一 schema 版本可由不同
    # 解析器产出。缺它就无法回答"这条观测是哪个解析器版本读出来的"，
    # 而排查解析 bug 时这是第一个要问的问题（蓝图 §8 要求版本化）。
    FieldSpec("parser_version"),
    # 源定位：从这条观测能否回到**源文件的具体位置**。高维 payload 走 payload_ref，
    # 但排查时仍需要"第几帧/第几个字节"，否则只能整份重读。
    FieldSpec("source_index", kind="int", minimum=0),
    FieldSpec("source_offset", kind="int", minimum=0),
    FieldSpec("transform_provenance"),
)

EPISODE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("episode_key", required=True),
    FieldSpec("task_name", required=True),
    FieldSpec("instruction", required=True),
    FieldSpec("environment"),
    FieldSpec("setting"),
    FieldSpec("episode_seconds", kind="number", minimum=0),
    FieldSpec("source_fps", kind="number", minimum=0),
    FieldSpec("has_wrist", kind="bool"),
    FieldSpec("raw", kind="mapping"),
    FieldSpec("video_stream_ids", required=True, kind="str_tuple"),
    FieldSpec("sensor_stream_ids", kind="str_tuple"),
    FieldSpec("action_ids", kind="str_tuple"),
    FieldSpec("observation_ids", kind="str_tuple"),
    FieldSpec("frame_count", kind="int", minimum=0),
    FieldSpec("episode_id"),
    FieldSpec("project_id"),
    FieldSpec("start_time_ns", kind="int"),
    FieldSpec("end_time_ns", kind="int"),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Frame:
    """帧级时间锚点；帧数据本体不内联，只存引用与时间信息。"""

    frame_index: int
    sequence: int | None = None
    timestamp_ns: int | None = None
    source_time_ns: int | None = None
    source_time_domain: str | None = None
    clock_id: str | None = None
    time_base_ref: str | None = None
    uncertainty_ns: int | None = None
    observation_ids: tuple[str, ...] | None = None
    is_dropped: bool | None = None
    drop_reason: str | None = None
    schema_version: str = SCHEMA_VERSION
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_entity(self, FRAME_FIELDS, SUPPORTED_SCHEMA_VERSIONS)
        if self.timestamp_ns is None and self.source_time_ns is None:
            raise CanonicalFieldError("Frame 必须至少有 timestamp_ns 或 source_time_ns 之一")
        if self.is_dropped is True and self.drop_reason is None:
            raise CanonicalFieldError("is_dropped=True 时必须给出 drop_reason")
        if self.is_dropped is False and self.drop_reason is not None:
            raise CanonicalFieldError("is_dropped=False 时不应出现 drop_reason")

    def to_dict(self) -> dict[str, object]:
        return entity_to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return entity_from_dict(cls, data, FRAME_FIELDS, SUPPORTED_SCHEMA_VERSIONS)


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoStream:
    """视频/相机流的一条声明；媒体本体在对象存储。"""

    stream_id: str
    camera_name: str | None = None
    camera_name_raw: str | None = None
    modality: str | None = None
    media_format: str | None = None
    storage_reference: str | None = None
    excerpt_start_ns: int | None = None
    duration_ns: int | None = None
    fps: float | None = None
    frame_count: int | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    encoding: str | None = None
    dropped_count: int | None = None
    schema_version: str = SCHEMA_VERSION
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_entity(self, VIDEO_STREAM_FIELDS, SUPPORTED_SCHEMA_VERSIONS)

    def to_dict(self) -> dict[str, object]:
        return entity_to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return entity_from_dict(cls, data, VIDEO_STREAM_FIELDS, SUPPORTED_SCHEMA_VERSIONS)


@dataclass(frozen=True, slots=True, kw_only=True)
class SensorStream:
    """非视频传感器流（IMU / 关节 / 力/力矩等）的声明。"""

    stream_id: str
    sensor_name: str | None = None
    sensor_type: str | None = None
    modality: str | None = None
    sample_rate_hz: float | None = None
    channel_names: tuple[str, ...] | None = None
    unit: str | None = None
    quantity_kind: str | None = None
    frame_id: str | None = None
    frame_convention: str | None = None
    duration_ns: int | None = None
    sample_count: int | None = None
    storage_reference: str | None = None
    checksum: str | None = None
    calibration_id: str | None = None
    calibration_version: int | None = None
    # --- 1.1 新增（全部可选，1.0 产物不含）---
    dtype: str | None = None
    start_timestamp_ns: int | None = None
    end_timestamp_ns: int | None = None
    missing_count: int | None = None
    invalid_count: int | None = None
    parser_version: str | None = None
    sensor_id: str | None = None
    device_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # 按产物自身的 schema 版本选字段表：1.0 产物不该被要求声明 1.1 才有的字段。
        validate_entity(
            self,
            _fields_for_version("SensorStream", SENSOR_STREAM_FIELDS, self.schema_version),
            SUPPORTED_SCHEMA_VERSIONS,
        )
        if self.frame_convention is not None and self.frame_convention not in FRAME_CONVENTIONS:
            raise CanonicalFieldError(
                f"SensorStream.frame_convention 非法：{self.frame_convention!r}；"
                f"允许 {sorted(FRAME_CONVENTIONS)}"
            )

    def to_dict(self) -> dict[str, object]:
        return entity_to_dict(
            self, _fields_for_version("SensorStream", SENSOR_STREAM_FIELDS, self.schema_version)
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        version = str(data.get("schema_version", "")) if isinstance(data, Mapping) else ""
        return entity_from_dict(
            cls,
            data,
            _fields_for_version("SensorStream", SENSOR_STREAM_FIELDS, version),
            SUPPORTED_SCHEMA_VERSIONS,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Action:
    """动作序列/动作指令的声明；数值本体可放对象存储。"""

    action_id: str
    episode_key: str
    action_type: str
    start_time_ns: int | None = None
    end_time_ns: int | None = None
    values: tuple[float, ...] | None = None
    unit: str | None = None
    frame_id: str | None = None
    joint_names: tuple[str, ...] | None = None
    source: str | None = None
    confidence: float | None = None
    action_schema_ref: str | None = None
    frame_index: int | None = None
    # --- 1.1 新增（全部可选，1.0 产物不含）---
    timestamp_raw_ns: int | None = None
    timestamp_normalized_ns: int | None = None
    dtype: str | None = None
    validity: str | None = None
    parser_version: str | None = None
    source_index: int | None = None
    source_offset: int | None = None
    semantics_version: str | None = None
    schema_version: str = SCHEMA_VERSION
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_entity(
            self,
            _fields_for_version("Action", ACTION_FIELDS, self.schema_version),
            SUPPORTED_SCHEMA_VERSIONS,
        )
        if self.start_time_ns is None and self.end_time_ns is None:
            raise CanonicalFieldError("Action 必须至少有 start_time_ns 或 end_time_ns 之一")
        if (
            self.start_time_ns is not None
            and self.end_time_ns is not None
            and self.end_time_ns < self.start_time_ns
        ):
            raise CanonicalFieldError("Action.end_time_ns 不能早于 start_time_ns")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise CanonicalFieldError("Action.confidence 必须在 [0, 1] 内")

    def to_dict(self) -> dict[str, object]:
        return entity_to_dict(
            self, _fields_for_version("Action", ACTION_FIELDS, self.schema_version)
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        version = str(data.get("schema_version", "")) if isinstance(data, Mapping) else ""
        return entity_from_dict(
            cls,
            data,
            _fields_for_version("Action", ACTION_FIELDS, version),
            SUPPORTED_SCHEMA_VERSIONS,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation:
    """一次传感器观测的声明；payload 由引用指向，不内联大数据。"""

    observation_id: str
    episode_key: str
    observation_type: str
    stream_id: str | None = None
    sensor_id: str | None = None
    frame_index: int | None = None
    timestamp_ns: int | None = None
    source_time_ns: int | None = None
    sequence: int | None = None
    frame_id: str | None = None
    calibration_id: str | None = None
    calibration_version: int | None = None
    unit: str | None = None
    quantity_kind: str | None = None
    shape: tuple[int, ...] | None = None
    dtype: str | None = None
    encoding: str | None = None
    payload_ref: str | None = None
    checksum: str | None = None
    valid: bool | None = None
    invalid_reason: str | None = None
    quality_flags: tuple[str, ...] | None = None
    # --- 1.1 新增（全部可选，1.0 产物不含）---
    timestamp_raw_ns: int | None = None
    timestamp_normalized_ns: int | None = None
    validity: str | None = None
    parser_version: str | None = None
    source_index: int | None = None
    source_offset: int | None = None
    transform_provenance: str | None = None
    schema_version: str = SCHEMA_VERSION
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_entity(
            self,
            _fields_for_version("Observation", OBSERVATION_FIELDS, self.schema_version),
            SUPPORTED_SCHEMA_VERSIONS,
        )
        if self.timestamp_ns is None and self.source_time_ns is None:
            raise CanonicalFieldError("Observation 必须至少有 timestamp_ns 或 source_time_ns 之一")
        if self.valid is False and self.invalid_reason is None:
            raise CanonicalFieldError("valid=False 时必须给出 invalid_reason")

    def to_dict(self) -> dict[str, object]:
        return entity_to_dict(
            self, _fields_for_version("Observation", OBSERVATION_FIELDS, self.schema_version)
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        version = str(data.get("schema_version", "")) if isinstance(data, Mapping) else ""
        return entity_from_dict(
            cls,
            data,
            _fields_for_version("Observation", OBSERVATION_FIELDS, version),
            SUPPORTED_SCHEMA_VERSIONS,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Episode:
    """Episode 级 Canonical：保留既有字段，并用 ID 引用下层实体。

    ``video_stream_ids`` 承接现有 ``CanonicalEpisode.media_assets``；
    ``sensor_stream_ids`` / ``action_ids`` / ``observation_ids`` 为引用，不内联。
    ``episode_id`` / ``project_id`` 由服务端在持久化时分配，投影阶段保持缺失。
    """

    episode_key: str
    task_name: str
    instruction: str
    video_stream_ids: tuple[str, ...]
    environment: str | None = None
    setting: str | None = None
    episode_seconds: float | None = None
    source_fps: float | None = None
    has_wrist: bool | None = None
    raw: Mapping[str, object] | None = None
    sensor_stream_ids: tuple[str, ...] | None = None
    action_ids: tuple[str, ...] | None = None
    observation_ids: tuple[str, ...] | None = None
    frame_count: int | None = None
    episode_id: str | None = None
    project_id: str | None = None
    start_time_ns: int | None = None
    end_time_ns: int | None = None
    schema_version: str = SCHEMA_VERSION
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_entity(self, EPISODE_FIELDS, SUPPORTED_SCHEMA_VERSIONS)
        if (
            self.start_time_ns is not None
            and self.end_time_ns is not None
            and self.end_time_ns < self.start_time_ns
        ):
            raise CanonicalFieldError("Episode.end_time_ns 不能早于 start_time_ns")

    def to_dict(self) -> dict[str, object]:
        return entity_to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return entity_from_dict(cls, data, EPISODE_FIELDS, SUPPORTED_SCHEMA_VERSIONS)


def declared_missing_fields(
    specs: tuple[FieldSpec, ...], values: Mapping[str, object]
) -> tuple[str, ...]:
    """按字段声明推导 ``missing_fields``；供投影函数显式登记缺口。"""
    return compute_missing_fields(specs, values)


def ensure_supported_version(value: object) -> None:
    """供 Adapter / 调用方在入口处显式校验版本。"""
    check_schema_version(value, SUPPORTED_SCHEMA_VERSIONS)


__all__ = [
    "ACTION_FIELDS",
    "EPISODE_FIELDS",
    "FRAME_CONVENTIONS",
    "FRAME_FIELDS",
    "LEGACY_EPISODE_FIELD_MAPPING",
    "OBSERVATION_FIELDS",
    "SCHEMA_VERSION",
    "SENSOR_STREAM_FIELDS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "VIDEO_STREAM_FIELDS",
    "Action",
    "CanonicalFieldError",
    "CanonicalSchemaError",
    "CanonicalSchemaVersionError",
    "Episode",
    "FieldSpec",
    "Frame",
    "Observation",
    "SensorStream",
    "VideoStream",
    "declared_missing_fields",
    "ensure_supported_version",
]


def _fields_for_version(
    entity_cls_name: str, specs: tuple[FieldSpec, ...], version: str
) -> tuple[FieldSpec, ...]:
    """按产物的 schema 版本与实体类型裁剪字段表。

    1.0 产物**不含** 1.1 新字段（不是"有但为空"），因此读 1.0 时不该要求它们
    登记 ``missing_fields``——否则历史产物会全部解析失败（那不是更严格，
    是破坏向后兼容，见 ADR-0049）。

    **注意跨实体同名但引入版本不同的字段**：``dtype`` 与 ``sensor_id`` 在
    ``Observation`` 1.0 中已存在，但在 ``SensorStream`` / ``Action`` 中是 1.1
    新增。若用一个全局集合统一剥离，会把 ``Observation`` 1.0 的既有字段误判为
    "未定义字段"。因此必须按实体各自的 1.1 新增集裁剪。
    """
    if version == "ddp.canonical/1.0.0":
        added = _ADDED_IN_1_1_BY_ENTITY.get(entity_cls_name, frozenset())
        return tuple(spec for spec in specs if spec.name not in added)
    return specs


_ADDED_IN_1_1_BY_ENTITY: Mapping[str, frozenset[str]] = {
    "SensorStream": frozenset(
        {
            "dtype",
            "start_timestamp_ns",
            "end_timestamp_ns",
            "missing_count",
            "invalid_count",
            "parser_version",
            "sensor_id",
            "device_id",
        }
    ),
    "Action": frozenset(
        {
            "timestamp_raw_ns",
            "timestamp_normalized_ns",
            "dtype",
            "validity",
            "parser_version",
            "source_index",
            "source_offset",
            "semantics_version",
        }
    ),
    "Observation": frozenset(
        {
            "timestamp_raw_ns",
            "timestamp_normalized_ns",
            "validity",
            "parser_version",
            "source_index",
            "source_offset",
            "transform_provenance",
        }
    ),
}

#: 1.0 字段表：读取历史产物时用。
SENSOR_STREAM_FIELDS_1_0: tuple[FieldSpec, ...] = _fields_for_version(
    "SensorStream", SENSOR_STREAM_FIELDS, "ddp.canonical/1.0.0"
)
OBSERVATION_FIELDS_1_0: tuple[FieldSpec, ...] = _fields_for_version(
    "Observation", OBSERVATION_FIELDS, "ddp.canonical/1.0.0"
)
ACTION_FIELDS_1_0: tuple[FieldSpec, ...] = _fields_for_version(
    "Action", ACTION_FIELDS, "ddp.canonical/1.0.0"
)
