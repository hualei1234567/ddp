"""标注应用层命令 DTO。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewAnnotationSchema:
    code: str
    name: str
    annotation_levels: tuple[str, ...]
    labels: tuple[Mapping[str, object], ...]
    description: str | None = None
    compatibility: str = "BACKWARD_COMPATIBLE"
    schema_lineage_id: UUID | None = None
    schema_version: int = 1
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewAnnotationSampleSet:
    code: str
    filters: Mapping[str, object] = field(default_factory=dict)
    name: str | None = None
    strategy: str = "FILTER"
    selection_rule_version: str = "stage3-sample-selection/1"
    strata: tuple[Mapping[str, object], ...] = ()
    dedup_key: str = "EPISODE_LINEAGE"
    data_source: str = "UNKNOWN"
    warn_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class NewAnnotationTask:
    code: str
    name: str
    sample_set_id: UUID
    schema_id: UUID
    target_item_count: int
    target_episode_count: int
    description: str | None = None
    schema_version: int | None = None
    due_at: datetime | None = None
    priority: str = "P1"
    assignment_mode: str = "MANUAL"
    iaa_policy: Mapping[str, object] = field(default_factory=dict)
    quality_policy: Mapping[str, object] = field(default_factory=dict)
    # 技能路由配置（V52-009）：JSON 标签映射序列或 None；空 = 不路由。
    # 校验在领域聚合根 AnnotationTask 统一完成。
    required_skills: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class NewAnnotationBatch:
    planned_item_count: int
    code: str | None = None
    sequence_no: int | None = None
    name: str | None = None
    stratum: str | None = None
    item_refs: tuple[UUID, ...] = ()
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewAnnotationAssignment:
    assignee_id: UUID
    assignment_role: str = "ANNOTATOR"
    planned_item_count: int = 0
    planned_minutes: int = 0
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewAnnotationSubmission:
    assignment_id: UUID
    records: tuple[Mapping[str, object], ...]
    data_source: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NewAiSuggestions:
    """一批 AI 预标注建议。

    ``suggestions`` 保持通用映射：每个元素由应用层统一转成
    :class:`app.domain.annotation.ai_suggestion.AiSuggestion`，校验规则不在这
    一层重复。``generated_at`` 缺省由用例补当前时刻（模型侧时间可选：
    批量回放历史模型输出时常拿不到原始时间）。
    """

    suggestions: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class NewAnnotationReview:
    submission_id: UUID
    reviewer_id: UUID


@dataclass(frozen=True, slots=True)
class NewAnnotationArbitration:
    submission_id: UUID
    arbiter_ids: tuple[UUID, ...]
    quorum: int
    trigger: str
    review_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewGoldSet:
    code: str
    schema_id: UUID
    items: tuple[Mapping[str, object], ...]
    name: str | None = None
    exposure_policy: str = "HIDDEN"
    max_exposure_per_annotator: int = 1
    rotation_policy: str = "NONE"
    usage_policy: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityCandidateSpec:
    submission_id: UUID
    stratum: str | None = None
    risk_score: float = 0.0


@dataclass(frozen=True, slots=True)
class NewQualitySample:
    task_id: UUID
    submission_id: UUID
    annotator_id: UUID
    sampling_policy_version: str
    sample_type: str
    sample_reason: str
    item_refs: tuple[UUID, ...] = ()
    stratum: str | None = None
    due_at: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
