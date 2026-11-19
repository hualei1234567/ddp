"""标注 Schema 预设模板目录（N6-001）。

预设是**代码级权威**，不落库：每个模板给出创建 Schema 时的起始标签定义，
项目可以在此基础上覆盖字段后，走既有的 ``POST /annotation-schemas`` 提交。
预设不属于任何项目的数据，也不参与运行时标签校验，因此不存在跨项目泄漏面。

模板不得绕过既有 Schema 校验：注册表构造时用 :class:`AnnotationSchema`
对每个模板做一次完整定义校验，标签规则只保留在 ``schema.py`` 一处。

版本语义：

- 每个模板带语义化 ``version``；模板内容变化必须显式升级该值；
- 注册表 ``version`` 是目录内容的**内容寻址摘要**，任何标签定义变化都会变，
  不依赖人记得手改序号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationLabelType,
    AnnotationLevel,
    LabelVisibility,
)
from app.domain.annotation.errors import DomainValidationError, ResourceNotFoundError
from app.domain.annotation.schema import AnnotationSchema, LabelDefinition
from app.domain.annotation.validation import (
    DESCRIPTION_MAX_LENGTH,
    NAME_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    canonical_sha256,
    coerce_enum,
    normalized_text,
)

PRESET_KEY_MAX_LENGTH = 64
PRESET_VERSION = "1.0.0"

# URL 路径安全的模板键：小写字母开头，只允许小写字母、数字与连字符。
_PRESET_KEY_PATTERN = re.compile(r"[a-z][a-z0-9-]*")

# 仅用于调用既有 Schema 校验的占位标识；不会落库，也不会出现在响应里。
_VALIDATION_SCHEMA_ID = UUID(int=1)
_VALIDATION_PROJECT_ID = UUID(int=2)
_VALIDATION_LINEAGE_ID = UUID(int=3)


class SchemaPresetNotFoundError(ResourceNotFoundError):
    """未注册的预设模板；只读目录按 404 处理，不暴露目录内部结构。"""

    def __init__(self, preset_key: str) -> None:
        super().__init__("Schema 预设", preset_key)


@dataclass(frozen=True, slots=True)
class SchemaPreset:
    """一个可复用的 Schema 起始定义。

    字段与 :class:`AnnotationSchema` 的可编辑定义一一对应（层级 + 标签），
    不携带 ``code`` / ``schema_id`` / ``project_id`` 等创建时才确定的权威字段。
    """

    key: str
    name: str
    description: str
    version: str
    annotation_levels: tuple[AnnotationLevel | str, ...]
    labels: tuple[LabelDefinition, ...]

    def __post_init__(self) -> None:
        key = normalized_text(self.key, "preset.key", max_length=PRESET_KEY_MAX_LENGTH)
        if not _PRESET_KEY_PATTERN.fullmatch(key):
            raise DomainValidationError("preset.key 只允许小写字母、数字与连字符")
        object.__setattr__(self, "key", key)
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "preset.name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "description",
            normalized_text(
                self.description,
                "preset.description",
                max_length=DESCRIPTION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "version",
            normalized_text(
                self.version,
                "preset.version",
                max_length=VERSION_TEXT_MAX_LENGTH,
            ),
        )
        levels: list[AnnotationLevel] = []
        for item in self.annotation_levels:
            level = coerce_enum(item, AnnotationLevel, "preset.annotation_levels")
            if level not in levels:
                levels.append(level)
        if not levels:
            raise DomainValidationError("preset.annotation_levels 至少需要一个层级")
        object.__setattr__(self, "annotation_levels", tuple(levels))
        labels: list[LabelDefinition] = []
        for item in self.labels:
            if not isinstance(item, LabelDefinition):
                raise DomainValidationError("preset.labels 只接受 LabelDefinition")
            labels.append(item)
        if not labels:
            raise DomainValidationError("preset.labels 至少需要一个标签定义")
        object.__setattr__(self, "labels", tuple(labels))
        # 复用聚合根的唯一校验实现：标签键唯一、标签层级属于 annotation_levels、
        # 枚举必须有取值、时长上下界等规则不在这里重写一份。
        AnnotationSchema(
            schema_id=_VALIDATION_SCHEMA_ID,
            project_id=_VALIDATION_PROJECT_ID,
            # 占位 code 固定，避免较长模板键因拼接过长而误报。
            code="preset-validation",
            name=self.name,
            schema_lineage_id=_VALIDATION_LINEAGE_ID,
            annotation_levels=tuple(levels),
            labels=tuple(labels),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "preset_key": self.key,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "annotation_levels": [level.value for level in self.annotation_levels],
            "labels": [definition.to_payload() for definition in self.labels],
        }


class SchemaPresetRegistry:
    """预设模板注册表。

    刻意不使用模块级可变单例：注册表有 ``register`` 入口，共享实例会让测试与
    运行期互相污染。默认目录由 :func:`default_registry` 每次构造一份新实例。
    """

    def __init__(self, presets: tuple[SchemaPreset, ...] = ()) -> None:
        self._presets: dict[str, SchemaPreset] = {}
        for preset in presets:
            self.register(preset)

    def register(self, preset: SchemaPreset) -> None:
        if not isinstance(preset, SchemaPreset):
            raise DomainValidationError("只能注册 SchemaPreset")
        if preset.key in self._presets:
            raise DomainValidationError(f"Schema 预设 {preset.key} 重复注册")
        self._presets[preset.key] = preset

    def get(self, preset_key: str) -> SchemaPreset:
        key = normalized_text(
            preset_key,
            "preset_key",
            max_length=PRESET_KEY_MAX_LENGTH,
        )
        preset = self._presets.get(key)
        if preset is None:
            raise SchemaPresetNotFoundError(key)
        return preset

    def list_presets(self) -> tuple[SchemaPreset, ...]:
        """按声明顺序返回，保证目录输出稳定。"""
        return tuple(self._presets.values())

    def __len__(self) -> int:
        return len(self._presets)

    @property
    def version(self) -> str:
        """目录内容寻址摘要；任何模板定义变化都会得到不同版本。"""
        return canonical_sha256(
            {"presets": [preset.to_payload() for preset in self.list_presets()]}
        )


def _label(
    key: str,
    label_type: AnnotationLabelType,
    level: AnnotationLevel,
    *,
    required: bool,
    description: str,
    value_spec: dict[str, object] | None = None,
    visibility: LabelVisibility = LabelVisibility.ANNOTATOR,
) -> LabelDefinition:
    """构造模板标签；显式声明必填性，避免依赖默认值。"""
    return LabelDefinition(
        label_key=key,
        label_type=label_type,
        required=required,
        level=level,
        visibility=visibility,
        description=description,
        value_spec=value_spec or {},
    )


DEFAULT_PRESETS: tuple[SchemaPreset, ...] = (
    SchemaPreset(
        key="action",
        name="动作标注",
        description="按动作片段标注动作类别、起止时间与执行结果，适用于操作类 Episode。",
        version=PRESET_VERSION,
        annotation_levels=(AnnotationLevel.EPISODE, AnnotationLevel.ACTION_SEGMENT),
        labels=(
            _label(
                "action_type",
                AnnotationLabelType.ENUM,
                AnnotationLevel.ACTION_SEGMENT,
                required=True,
                description="动作类别。",
                value_spec={
                    "enum_values": [
                        "PICK",
                        "PLACE",
                        "OPEN",
                        "CLOSE",
                        "PUSH",
                        "PULL",
                        "NAVIGATE",
                        "OTHER",
                    ]
                },
            ),
            _label(
                "action_segment",
                AnnotationLabelType.ACTION_SEGMENT,
                AnnotationLevel.ACTION_SEGMENT,
                required=True,
                description="动作的时间片段（相对 Episode 起点）。",
                value_spec={"min_duration_ns": 1},
            ),
            _label(
                "success",
                AnnotationLabelType.BOOLEAN,
                AnnotationLevel.ACTION_SEGMENT,
                required=True,
                description="动作是否按预期完成。",
            ),
            _label(
                "failure_reason",
                AnnotationLabelType.FREE_TEXT,
                AnnotationLevel.ACTION_SEGMENT,
                required=False,
                description="失败原因；仅复核者可见。",
                visibility=LabelVisibility.REVIEWER_ONLY,
            ),
        ),
    ),
    SchemaPreset(
        key="segment",
        name="技能分段",
        description="按技能维度切分 Episode 时段，适用于长程任务的技能级采样。",
        version=PRESET_VERSION,
        annotation_levels=(AnnotationLevel.EPISODE, AnnotationLevel.TIME_RANGE),
        labels=(
            _label(
                "skill",
                AnnotationLabelType.ENUM,
                AnnotationLevel.EPISODE,
                required=True,
                description="Episode 的主技能。",
                value_spec={
                    "enum_values": [
                        "PICK_AND_PLACE",
                        "LOCOMOTION",
                        "MANIPULATION",
                        "NAVIGATION",
                        "OTHER",
                    ]
                },
            ),
            _label(
                "skill_segment",
                AnnotationLabelType.TIME_RANGE,
                AnnotationLevel.TIME_RANGE,
                required=True,
                description="技能对应的时段。",
                value_spec={"min_duration_ns": 1},
            ),
            _label(
                "segment_label",
                AnnotationLabelType.STRING,
                AnnotationLevel.TIME_RANGE,
                required=False,
                description="可选的细粒度分段标签。",
            ),
            _label(
                "segment_note",
                AnnotationLabelType.FREE_TEXT,
                AnnotationLevel.TIME_RANGE,
                required=False,
                description="分段备注。",
            ),
        ),
    ),
    SchemaPreset(
        key="object",
        name="目标物体",
        description="标注目标物体的类别、跟踪轨迹与遮挡情况，适用于操作对象识别。",
        version=PRESET_VERSION,
        annotation_levels=(
            AnnotationLevel.EPISODE,
            AnnotationLevel.FRAME,
            AnnotationLevel.OBJECT_TRACK,
        ),
        labels=(
            _label(
                "object_class",
                AnnotationLabelType.ENUM,
                AnnotationLevel.OBJECT_TRACK,
                required=True,
                description="目标物体类别。",
                value_spec={
                    "enum_values": ["CUP", "BOTTLE", "BOX", "TOOL", "DOOR_HANDLE", "OTHER"]
                },
            ),
            _label(
                "track_id",
                AnnotationLabelType.STRING,
                AnnotationLevel.OBJECT_TRACK,
                required=True,
                description="同一物体在 Episode 内的稳定跟踪标识。",
            ),
            _label(
                "bbox",
                AnnotationLabelType.OBJECT_TRACK,
                AnnotationLevel.OBJECT_TRACK,
                required=True,
                description="物体在帧序列中的跟踪框与轨迹。",
            ),
            _label(
                "occlusion",
                AnnotationLabelType.ENUM,
                AnnotationLevel.OBJECT_TRACK,
                required=False,
                description="遮挡程度。",
                value_spec={"enum_values": ["NONE", "PARTIAL", "FULL"]},
            ),
        ),
    ),
    SchemaPreset(
        key="attributes",
        name="物体属性",
        description="标注物体的颜色、材质与尺寸等属性，适用于属性级数据筛选。",
        version=PRESET_VERSION,
        annotation_levels=(AnnotationLevel.EPISODE, AnnotationLevel.FRAME),
        labels=(
            _label(
                "color",
                AnnotationLabelType.MULTI_ENUM,
                AnnotationLevel.FRAME,
                required=True,
                description="物体主要颜色，可多选。",
                value_spec={
                    "enum_values": [
                        "RED",
                        "GREEN",
                        "BLUE",
                        "BLACK",
                        "WHITE",
                        "METALLIC",
                        "OTHER",
                    ]
                },
            ),
            _label(
                "material",
                AnnotationLabelType.ENUM,
                AnnotationLevel.FRAME,
                required=False,
                description="物体材质。",
                value_spec={
                    "enum_values": ["PLASTIC", "METAL", "WOOD", "GLASS", "FABRIC", "OTHER"]
                },
            ),
            _label(
                "size",
                AnnotationLabelType.ENUM,
                AnnotationLevel.FRAME,
                required=False,
                description="物体尺寸档位。",
                value_spec={"enum_values": ["SMALL", "MEDIUM", "LARGE"]},
            ),
            _label(
                "attribute_note",
                AnnotationLabelType.FREE_TEXT,
                AnnotationLevel.FRAME,
                required=False,
                description="属性备注。",
            ),
        ),
    ),
    SchemaPreset(
        key="quality",
        name="质量标注",
        description="对 Episode 或片段给出通过/失败结论与缺陷归因，供质检复核使用。",
        version=PRESET_VERSION,
        annotation_levels=(AnnotationLevel.EPISODE, AnnotationLevel.TIME_RANGE),
        labels=(
            _label(
                "quality_verdict",
                AnnotationLabelType.ENUM,
                AnnotationLevel.EPISODE,
                required=True,
                description="质量结论。",
                value_spec={"enum_values": ["PASS", "FAIL", "NEEDS_REVIEW"]},
            ),
            _label(
                "defect_type",
                AnnotationLabelType.MULTI_ENUM,
                AnnotationLevel.EPISODE,
                required=False,
                description="缺陷类型，可多选。",
                value_spec={
                    "enum_values": [
                        "BLUR",
                        "OCCLUSION",
                        "SYNC_DRIFT",
                        "MISSING_FRAME",
                        "SENSOR_NOISE",
                        "INSTRUCTION_MISMATCH",
                        "OTHER",
                    ]
                },
            ),
            _label(
                "defect_segment",
                AnnotationLabelType.TIME_RANGE,
                AnnotationLevel.TIME_RANGE,
                required=False,
                description="缺陷所在时段。",
                value_spec={"min_duration_ns": 1},
            ),
            _label(
                "quality_note",
                AnnotationLabelType.FREE_TEXT,
                AnnotationLevel.EPISODE,
                required=False,
                description="质检备注；仅复核者可见。",
                visibility=LabelVisibility.REVIEWER_ONLY,
            ),
        ),
    ),
    SchemaPreset(
        key="scene",
        name="场景理解",
        description="标注场景类型、场地标签、光照与杂乱度，适用于场景分层采样。",
        version=PRESET_VERSION,
        annotation_levels=(AnnotationLevel.EPISODE,),
        labels=(
            _label(
                "scene_type",
                AnnotationLabelType.ENUM,
                AnnotationLevel.EPISODE,
                required=True,
                description="场景大类。",
                value_spec={"enum_values": ["INDOOR", "OUTDOOR", "MIXED"]},
            ),
            _label(
                "scene_tags",
                AnnotationLabelType.MULTI_ENUM,
                AnnotationLevel.EPISODE,
                required=False,
                description="场地标签，可多选。",
                value_spec={
                    "enum_values": ["KITCHEN", "LIVING_ROOM", "OFFICE", "WAREHOUSE", "LAB", "OTHER"]
                },
            ),
            _label(
                "lighting",
                AnnotationLabelType.ENUM,
                AnnotationLevel.EPISODE,
                required=False,
                description="光照条件。",
                value_spec={"enum_values": ["BRIGHT", "NORMAL", "DIM", "MIXED"]},
            ),
            _label(
                "clutter_level",
                AnnotationLabelType.NUMBER,
                AnnotationLevel.EPISODE,
                required=False,
                description="场景杂乱度（0 最整洁，5 最杂乱）。",
                value_spec={"min_value": 0, "max_value": 5},
            ),
        ),
    ),
    SchemaPreset(
        key="robot-state",
        name="机器人状态",
        description="标注机器人运行模式、电量与故障码，适用于状态相关数据筛选。",
        version=PRESET_VERSION,
        annotation_levels=(AnnotationLevel.EPISODE, AnnotationLevel.TIME_RANGE),
        labels=(
            _label(
                "robot_mode",
                AnnotationLabelType.ENUM,
                AnnotationLevel.EPISODE,
                required=True,
                description="机器人运行模式。",
                value_spec={"enum_values": ["IDLE", "TELEOP", "AUTONOMOUS", "FAULT"]},
            ),
            _label(
                "battery_percent",
                AnnotationLabelType.NUMBER,
                AnnotationLevel.EPISODE,
                required=False,
                description="采集时的电量百分比。",
                value_spec={"min_value": 0, "max_value": 100, "unit": "%"},
            ),
            _label(
                "state_segment",
                AnnotationLabelType.TIME_RANGE,
                AnnotationLevel.TIME_RANGE,
                required=False,
                description="状态持续时段。",
                value_spec={"min_duration_ns": 1},
            ),
            _label(
                "fault_code",
                AnnotationLabelType.STRING,
                AnnotationLevel.EPISODE,
                required=False,
                description="故障码；仅复核者可见。",
                visibility=LabelVisibility.REVIEWER_ONLY,
            ),
        ),
    ),
)


def default_registry() -> SchemaPresetRegistry:
    """默认七类预设目录；每次返回新实例，避免跨调用共享可变状态。"""
    return SchemaPresetRegistry(DEFAULT_PRESETS)


__all__ = [
    "DEFAULT_PRESETS",
    "PRESET_KEY_MAX_LENGTH",
    "PRESET_VERSION",
    "SchemaPreset",
    "SchemaPresetNotFoundError",
    "SchemaPresetRegistry",
    "default_registry",
]
