"""标签 Schema 聚合根。

发布后标签定义不可变：实体只允许经具名动作迁移状态；``schema_sha256``
由稳定 JSON 计算并在每次构造时复算校验，任何定义变化都会得到不同 Hash。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationLabelType,
    AnnotationLevel,
    AnnotationSchemaStatus,
    LabelVisibility,
    SchemaCompatibility,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    SchemaImmutableError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    schema_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    CODE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    LABEL_KEY_MAX_LENGTH,
    NAME_MAX_LENGTH,
    REASON_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    canonical_sha256,
    coerce_enum,
    frozen_mapping,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    validate_sha256,
)

_INVALID_VALUE_SPEC_KEYS = frozenset(
    {
        "enum_values",
        "min_value",
        "max_value",
        "unit",
        "min_duration_ns",
        "max_duration_ns",
        "iou_threshold",
        "max_length",
        "json_schema_ref",
    }
)
_ENUM_TYPES = frozenset({AnnotationLabelType.ENUM, AnnotationLabelType.MULTI_ENUM})


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LabelDefinition:
    label_key: str
    label_type: AnnotationLabelType | str
    required: bool
    level: AnnotationLevel | str
    visibility: LabelVisibility | str = LabelVisibility.ANNOTATOR
    description: str | None = None
    value_spec: Mapping[str, object] = field(default_factory=dict)
    quality_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "label_key",
            normalized_text(
                self.label_key,
                "label_key",
                max_length=LABEL_KEY_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "label_type",
            coerce_enum(self.label_type, AnnotationLabelType, "label_type"),
        )
        object.__setattr__(self, "level", coerce_enum(self.level, AnnotationLevel, "level"))
        object.__setattr__(
            self,
            "visibility",
            coerce_enum(self.visibility, LabelVisibility, "visibility"),
        )
        object.__setattr__(
            self,
            "required",
            self.required if isinstance(self.required, bool) else _invalid("required 必须是布尔值"),
        )
        object.__setattr__(
            self,
            "description",
            optional_text(
                self.description,
                "label.description",
                max_length=DESCRIPTION_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "value_spec", _validate_value_spec(self.value_spec))
        rules = self.quality_rules or ()
        if not isinstance(rules, list | tuple):
            raise DomainValidationError("quality_rules 必须是字符串列表")
        cleaned: list[str] = []
        for item in rules:
            text = normalized_text(item, "quality_rules", max_length=200)
            if text not in cleaned:
                cleaned.append(text)
        object.__setattr__(self, "quality_rules", tuple(cleaned))

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "label_key": self.label_key,
            "label_type": self.label_type.value,
            "required": self.required,
            "level": self.level.value,
            "visibility": self.visibility.value,
            "value_spec": _plain(self.value_spec),
            "quality_rules": list(self.quality_rules),
        }
        if self.description is not None:
            payload["description"] = self.description
        return payload


def _invalid(message: str) -> bool:
    raise DomainValidationError(message)


def _validate_value_spec(value: object) -> Mapping[str, object]:
    spec = frozen_mapping(value or {}, "value_spec")
    unknown = set(spec) - _INVALID_VALUE_SPEC_KEYS
    if unknown:
        raise DomainValidationError("value_spec 包含未知字段")
    if "min_value" in spec and spec["min_value"] is not None:
        _require_number(spec["min_value"], "value_spec.min_value")
    if "max_value" in spec and spec["max_value"] is not None:
        _require_number(spec["max_value"], "value_spec.max_value")
    if (
        spec.get("min_value") is not None
        and spec.get("max_value") is not None
        and spec["max_value"] < spec["min_value"]  # type: ignore[operator]
    ):
        raise DomainValidationError("value_spec.max_value 不得小于 min_value")
    if "iou_threshold" in spec and spec["iou_threshold"] is not None:
        threshold = _require_number(spec["iou_threshold"], "value_spec.iou_threshold")
        if not 0 <= threshold <= 1:
            raise DomainValidationError("value_spec.iou_threshold 必须在 0 到 1 之间")
    for name in ("min_duration_ns", "max_duration_ns", "max_length"):
        if name in spec and spec[name] is not None:
            raw = spec[name]
            if isinstance(raw, str) and name != "max_length":
                try:
                    raw = int(raw)
                except ValueError as exc:
                    raise DomainValidationError(f"value_spec.{name} 必须是整数") from exc
            non_negative_int(raw, f"value_spec.{name}")
    enum_values = spec.get("enum_values")
    if enum_values is not None:
        if not isinstance(enum_values, list | tuple):
            raise DomainValidationError("value_spec.enum_values 必须是字符串列表")
        cleaned: list[str] = []
        for item in enum_values:
            text = normalized_text(item, "value_spec.enum_values", max_length=200)
            if text in cleaned:
                raise DomainValidationError("value_spec.enum_values 不得重复")
            cleaned.append(text)
        spec = {**spec, "enum_values": cleaned}
    return frozen_mapping(spec, "value_spec")


def _require_number(value: object, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise DomainValidationError(f"{field_name} 必须是数字")
    return float(value)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def schema_snapshot_payload(
    schema: AnnotationSchema,
) -> dict[str, object]:
    """Schema 快照 Hash 的稳定载荷；不包含状态、时间与乐观锁版本。"""
    return {
        "schema_lineage_id": str(schema.schema_lineage_id),
        "schema_version": schema.schema_version,
        "code": schema.code,
        "name": schema.name,
        "description": schema.description,
        "annotation_levels": sorted(level.value for level in schema.annotation_levels),
        "labels": [
            definition.to_payload()
            for definition in sorted(schema.labels, key=lambda item: item.label_key)
        ],
        "compatibility": schema.compatibility.value,
    }


def compute_schema_sha256(schema: AnnotationSchema) -> str:
    return canonical_sha256(schema_snapshot_payload(schema))


@dataclass(frozen=True, slots=True)
class AnnotationSchema:
    schema_id: UUID
    project_id: UUID
    code: str
    name: str
    schema_lineage_id: UUID
    schema_version: int = 1
    description: str | None = None
    status: AnnotationSchemaStatus | str = AnnotationSchemaStatus.DRAFT
    annotation_levels: tuple[AnnotationLevel | str, ...] = ()
    labels: tuple[LabelDefinition, ...] = ()
    compatibility: SchemaCompatibility | str = SchemaCompatibility.BACKWARD_COMPATIBLE
    schema_sha256: str | None = None
    supersedes_id: UUID | None = None
    published_at: datetime | None = None
    deprecated_at: datetime | None = None
    revoked_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("schema_id", "project_id", "schema_lineage_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "description",
            optional_text(
                self.description,
                "description",
                max_length=DESCRIPTION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationSchemaStatus, "status"),
        )
        object.__setattr__(
            self,
            "compatibility",
            coerce_enum(self.compatibility, SchemaCompatibility, "compatibility"),
        )
        object.__setattr__(
            self,
            "schema_version",
            positive_int(self.schema_version, "schema_version"),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "supersedes_id",
            optional_uuid(self.supersedes_id, "supersedes_id"),
        )
        for name in ("published_at", "deprecated_at", "revoked_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        levels: list[AnnotationLevel] = []
        for item in self.annotation_levels:
            level = coerce_enum(item, AnnotationLevel, "annotation_levels")
            if level not in levels:
                levels.append(level)
        if not levels:
            raise DomainValidationError("annotation_levels 至少需要一个层级")
        object.__setattr__(self, "annotation_levels", tuple(levels))
        if not self.labels:
            raise DomainValidationError("labels 至少需要一个标签定义")
        cleaned_labels: list[LabelDefinition] = []
        seen: set[str] = set()
        for item in self.labels:
            definition = (
                item if isinstance(item, LabelDefinition) else LabelDefinition(**item)  # type: ignore[arg-type]
            )
            if definition.label_key in seen:
                raise DomainValidationError("标签键必须唯一")
            if definition.level not in levels:
                raise DomainValidationError("标签 level 必须属于 annotation_levels")
            _validate_label_type_value(definition)
            seen.add(definition.label_key)
            cleaned_labels.append(definition)
        object.__setattr__(self, "labels", tuple(cleaned_labels))
        digest = compute_schema_sha256(self)
        if self.schema_sha256 is not None:
            provided = validate_sha256(self.schema_sha256, "schema_sha256")
            if provided != digest:
                raise DomainValidationError("schema_sha256 与标签定义不一致")
        object.__setattr__(self, "schema_sha256", digest)

    @property
    def is_published(self) -> bool:
        return self.status is AnnotationSchemaStatus.PUBLISHED

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationSchema:
        action_name = validate_action_name(action, family="schema")
        target = schema_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("schema", action_name) and cleaned_reason is None:
            raise DomainValidationError("该动作必须填写原因")
        changes: dict[str, object] = {
            "status": target,
            "updated_at": occurred_at,
        }
        if target is AnnotationSchemaStatus.PUBLISHED:
            changes["published_at"] = self.published_at or occurred_at
        if target is AnnotationSchemaStatus.DEPRECATED:
            changes["deprecated_at"] = self.deprecated_at or occurred_at
        if target is AnnotationSchemaStatus.REVOKED:
            changes["revoked_at"] = self.revoked_at or occurred_at
        if (
            self.status in {AnnotationSchemaStatus.PUBLISHED, AnnotationSchemaStatus.DEPRECATED}
            and target is not AnnotationSchemaStatus.REVOKED
        ):
            # 状态机已保证只可能是 DEPRECATED；定义字段不在此处改变。
            pass
        return replace(self, **changes)

    def assert_immutable_definition(self, candidate: AnnotationSchema) -> None:
        if self.status in {
            AnnotationSchemaStatus.PUBLISHED,
            AnnotationSchemaStatus.DEPRECATED,
            AnnotationSchemaStatus.REVOKED,
        } and schema_snapshot_payload(self) != schema_snapshot_payload(candidate):
            raise SchemaImmutableError("已发布 Schema 的标签定义不可原地修改")


def _validate_label_type_value(definition: LabelDefinition) -> None:
    if definition.label_type in _ENUM_TYPES:
        values = definition.value_spec.get("enum_values")
        if not values:
            raise DomainValidationError("枚举标签必须提供非空 enum_values")
    if definition.label_type is AnnotationLabelType.NUMBER:
        if definition.value_spec.get("unit") is not None:
            normalized_text(
                definition.value_spec["unit"],
                "value_spec.unit",
                max_length=VERSION_TEXT_MAX_LENGTH,
            )
    if definition.label_type in {
        AnnotationLabelType.TIME_RANGE,
        AnnotationLabelType.ACTION_SEGMENT,
    }:
        min_duration = definition.value_spec.get("min_duration_ns")
        max_duration = definition.value_spec.get("max_duration_ns")
        if (
            min_duration is not None
            and max_duration is not None
            and int(max_duration) < int(min_duration)
        ):
            raise DomainValidationError("max_duration_ns 不得小于 min_duration_ns")
    if definition.label_type is AnnotationLabelType.JSON:
        ref = definition.value_spec.get("json_schema_ref")
        if ref is not None:
            normalized_text(ref, "value_spec.json_schema_ref", max_length=1024)


__all__ = [
    "AnnotationSchema",
    "LabelDefinition",
    "compute_schema_sha256",
    "schema_snapshot_payload",
]
