"""标注 Schema 与样本集 HTTP DTO。

请求模型统一 ``extra="forbid"``，不接收 status、project_id、version、
created_by 等服务端权威字段；响应只暴露契约允许的字段。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.annotation.sample_set import AnnotationSampleSet
from app.domain.annotation.schema import AnnotationSchema
from app.domain.annotation.schema_presets import SchemaPreset


class AnnotationSchemaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    annotation_levels: list[str] = Field(min_length=1)
    labels: list[dict[str, object]] = Field(min_length=1)
    compatibility: str = Field(default="BACKWARD_COMPATIBLE", max_length=32)
    schema_lineage_id: UUID | None = None
    schema_version: int = Field(default=1, ge=1)
    supersedes_id: UUID | None = None


class AnnotationSchemaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    annotation_levels: list[str] | None = Field(default=None, min_length=1)
    labels: list[dict[str, object]] | None = Field(default=None, min_length=1)
    compatibility: str | None = Field(default=None, max_length=32)


class AnnotationSchemaActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class SampleSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    strategy: str = Field(default="FILTER", max_length=32)
    selection_rule_version: str = Field(
        default="stage3-sample-selection/1",
        min_length=1,
        max_length=64,
    )
    filters: dict[str, object] = Field(default_factory=dict)
    strata: list[dict[str, object]] = Field(default_factory=list)
    dedup_key: str = Field(default="EPISODE_LINEAGE", max_length=32)
    data_source: str = Field(default="UNKNOWN", max_length=32)
    warn_confirmed: bool = False


class SampleSetPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: dict[str, object] | None = None
    strata: list[dict[str, object]] | None = None
    warn_confirmed: bool = False


class SampleSetActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    filters: dict[str, object] | None = None
    strata: list[dict[str, object]] | None = None
    warn_confirmed: bool = False


class LabelDefinitionOut(BaseModel):
    label_key: str
    label_type: str
    required: bool
    level: str
    visibility: str
    description: str | None = None
    value_spec: dict[str, object]
    quality_rules: list[str]


class AnnotationSchemaOut(BaseModel):
    schema_id: UUID
    project_id: UUID
    code: str
    name: str
    description: str | None
    schema_lineage_id: UUID
    schema_version: int
    status: str
    annotation_levels: list[str]
    labels: list[LabelDefinitionOut]
    compatibility: str
    schema_sha256: str
    supersedes_id: UUID | None
    published_at: datetime | None
    deprecated_at: datetime | None
    revoked_at: datetime | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, schema: AnnotationSchema) -> AnnotationSchemaOut:
        return cls(
            schema_id=schema.schema_id,
            project_id=schema.project_id,
            code=schema.code,
            name=schema.name,
            description=schema.description,
            schema_lineage_id=schema.schema_lineage_id,
            schema_version=schema.schema_version,
            status=_value(schema.status),
            annotation_levels=[_value(item) for item in schema.annotation_levels],
            labels=[LabelDefinitionOut(**item.to_payload()) for item in schema.labels],
            compatibility=_value(schema.compatibility),
            schema_sha256=schema.schema_sha256 or "",
            supersedes_id=schema.supersedes_id,
            published_at=schema.published_at,
            deprecated_at=schema.deprecated_at,
            revoked_at=schema.revoked_at,
            version=schema.version,
            created_at=schema.created_at,
            updated_at=schema.updated_at,
            created_by=schema.created_by,
            updated_by=schema.updated_by,
        )


class SchemaPresetOut(BaseModel):
    """预设模板：只含创建 Schema 所需的可编辑定义，不含项目/ID 等权威字段。"""

    preset_key: str
    name: str
    description: str
    version: str
    annotation_levels: list[str]
    labels: list[LabelDefinitionOut]

    @classmethod
    def from_domain(cls, preset: SchemaPreset) -> SchemaPresetOut:
        return cls(
            preset_key=preset.key,
            name=preset.name,
            description=preset.description,
            version=preset.version,
            annotation_levels=[_value(item) for item in preset.annotation_levels],
            labels=[LabelDefinitionOut(**item.to_payload()) for item in preset.labels],
        )


class SchemaPresetCatalogOut(BaseModel):
    """只读预设目录；``catalog_version`` 是目录内容摘要。"""

    catalog_version: str
    items: list[SchemaPresetOut]


class SampleEpisodeRefOut(BaseModel):
    episode_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    sample_role: str
    selected_reason: str
    stratum: str | None
    time_domain: str
    start_time_ns: int | None = None
    end_time_ns: int | None = None


class AnnotationSampleSetOut(BaseModel):
    sample_set_id: UUID
    project_id: UUID
    code: str
    name: str | None
    status: str
    selection_rule_version: str
    strategy: str
    filters: dict[str, object]
    strata: list[dict[str, object]]
    episode_refs: list[SampleEpisodeRefOut]
    dedup_key: str
    candidate_count: int
    selected_count: int
    excluded_count: int
    deduplicated_count: int
    snapshot_sha256: str | None
    snapshot_at: datetime | None
    cancelled_reason: str | None
    data_source: str
    exclusion_summary: dict[str, object]
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, sample_set: AnnotationSampleSet) -> AnnotationSampleSetOut:
        return cls(
            sample_set_id=sample_set.sample_set_id,
            project_id=sample_set.project_id,
            code=sample_set.code,
            name=sample_set.name,
            status=_value(sample_set.status),
            selection_rule_version=sample_set.selection_rule_version,
            strategy=_value(sample_set.strategy),
            filters=dict(sample_set.filters),
            strata=[dict(item) for item in sample_set.strata],
            episode_refs=[
                SampleEpisodeRefOut(**item.to_payload()) for item in sample_set.episode_refs
            ],
            dedup_key=_value(sample_set.dedup_key),
            candidate_count=sample_set.candidate_count,
            selected_count=sample_set.selected_count,
            excluded_count=sample_set.excluded_count,
            deduplicated_count=sample_set.deduplicated_count,
            snapshot_sha256=sample_set.snapshot_sha256,
            snapshot_at=sample_set.snapshot_at,
            cancelled_reason=sample_set.cancelled_reason,
            data_source=_value(sample_set.data_source),
            exclusion_summary=dict(sample_set.exclusion_summary),
            version=sample_set.version,
            created_at=sample_set.created_at,
            updated_at=sample_set.updated_at,
            created_by=sample_set.created_by,
            updated_by=sample_set.updated_by,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
