"""Schema 预设目录领域单测（N6-001）。

覆盖：八类模板完整性、既有 Schema 校验不被绕过、内容寻址版本、
未知模板语义与注册表防重复。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from app.domain.annotation.enums import AnnotationLabelType, AnnotationLevel
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.schema import AnnotationSchema, LabelDefinition
from app.domain.annotation.schema_presets import (
    DEFAULT_PRESETS,
    PRESET_VERSION,
    SchemaPreset,
    SchemaPresetNotFoundError,
    SchemaPresetRegistry,
    default_registry,
)

EXPECTED_KEYS = {
    "action",
    "segment",
    "object",
    "attributes",
    "quality",
    "scene",
    "semantic-hierarchy",
    "robot-state",
}


def test_default_catalog_has_eight_unique_templates() -> None:
    registry = default_registry()

    assert len(registry) == 8
    presets = registry.list_presets()
    assert {preset.key for preset in presets} == EXPECTED_KEYS
    assert len({preset.key for preset in presets}) == 8

    for preset in presets:
        assert preset.version == PRESET_VERSION
        assert preset.name and preset.description
        assert preset.annotation_levels
        assert preset.labels
        levels = {str(level) for level in preset.annotation_levels}
        for label in preset.labels:
            assert isinstance(label, LabelDefinition)
            assert isinstance(label.required, bool)
            assert label.level.value in levels


def test_every_template_passes_existing_schema_validation() -> None:
    """模板必须以既有聚合根的校验口径通过，不能自带一套更松的规则。"""
    for preset in default_registry().list_presets():
        lineage_id = uuid4()
        schema = AnnotationSchema(
            schema_id=uuid4(),
            project_id=uuid4(),
            code=f"preset-{preset.key}",
            name=preset.name,
            schema_lineage_id=lineage_id,
            annotation_levels=preset.annotation_levels,
            labels=preset.labels,
        )
        assert schema.schema_sha256

        # 与创建 API 的输入形态一致：labels 以 payload（dict）传入。
        same = AnnotationSchema(
            schema_id=uuid4(),
            project_id=uuid4(),
            code=f"preset-{preset.key}",
            name=preset.name,
            schema_lineage_id=lineage_id,
            annotation_levels=preset.annotation_levels,
            labels=tuple(label.to_payload() for label in preset.labels),  # type: ignore[arg-type]
        )
        assert same.schema_sha256 == schema.schema_sha256


def test_unknown_preset_raises_not_found() -> None:
    with pytest.raises(SchemaPresetNotFoundError) as excinfo:
        default_registry().get("not-a-preset")

    assert excinfo.value.code == "resource_not_found"
    # 不做前缀或模糊匹配。
    with pytest.raises(SchemaPresetNotFoundError):
        default_registry().get("act")


def test_duplicate_registration_is_rejected() -> None:
    registry = SchemaPresetRegistry((DEFAULT_PRESETS[0],))
    with pytest.raises(DomainValidationError):
        registry.register(DEFAULT_PRESETS[0])


def test_template_cannot_bypass_schema_rules() -> None:
    action = default_registry().get("action")

    duplicate_labels = (action.labels[0], action.labels[0])
    with pytest.raises(DomainValidationError):
        SchemaPreset(
            key="broken-duplicate",
            name="重复标签",
            description="用于验证模板不绕过标签键唯一规则。",
            version="1.0.0",
            annotation_levels=action.annotation_levels,
            labels=duplicate_labels,
        )

    # V31-047 起类型相关值规范在 LabelDefinition 构造时即校验（失败点从
    # Schema 聚合层前移到定义层）；两层都必须失败才算防住绕过。
    with pytest.raises(DomainValidationError):
        LabelDefinition(
            label_key="action_type",
            label_type=AnnotationLabelType.ENUM,
            required=True,
            level=AnnotationLevel.ACTION_SEGMENT,
            value_spec={},
        )
    with pytest.raises(DomainValidationError):
        SchemaPreset(
            key="broken-enum",
            name="缺少枚举值",
            description="用于验证模板不绕过枚举取值规则。",
            version="1.0.0",
            annotation_levels=action.annotation_levels,
            labels=(
                LabelDefinition(
                    label_key="action_type",
                    label_type=AnnotationLabelType.ENUM,
                    required=True,
                    level=AnnotationLevel.ACTION_SEGMENT,
                    value_spec={},
                ),
            ),
        )

    # V31-047：OUTCOME_SPAN 的非法 outcome 集合同样不得绕过。
    with pytest.raises(DomainValidationError):
        LabelDefinition(
            label_key="outcome_span",
            label_type=AnnotationLabelType.OUTCOME_SPAN,
            required=True,
            level=AnnotationLevel.SEMANTIC_TASK,
            value_spec={"outcome_values": ["SUCCESS", "PERFECT"]},
        )
    with pytest.raises(DomainValidationError):
        SchemaPreset(
            key="broken-outcome",
            name="非法结果枚举",
            description="用于验证模板不绕过 OUTCOME_SPAN 取值规则。",
            version="1.0.0",
            annotation_levels=(AnnotationLevel.SEMANTIC_TASK,),
            labels=(
                LabelDefinition(
                    label_key="outcome_span",
                    label_type=AnnotationLabelType.OUTCOME_SPAN,
                    required=True,
                    level=AnnotationLevel.SEMANTIC_TASK,
                    value_spec={"outcome_values": ["SUCCESS", "PERFECT"]},
                ),
            ),
        )


def test_catalog_version_is_content_addressed() -> None:
    first = default_registry().version
    second = default_registry().version
    assert first == second
    assert first.startswith("sha256:")

    changed = replace(DEFAULT_PRESETS[0], description="模板内容变化后的描述。")
    mutated = SchemaPresetRegistry((changed, *DEFAULT_PRESETS[1:]))
    assert mutated.version != first
