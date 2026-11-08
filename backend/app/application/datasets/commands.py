"""数据集应用层命令 DTO。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewDataset:
    code: str
    name: str
    purpose: str
    owner_id: UUID
    usage_policy: str
    description: str | None = None
    tags: tuple[str, ...] = ()
    retention_policy: Mapping[str, object] | None = None
    default_dedup_policy_id: UUID | None = None
    default_split_policy_id: UUID | None = None
    default_leakage_policy_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewDedupPolicy:
    exact_keys: tuple[str, ...]
    near_duplicate_enabled: bool
    near_duplicate_enforcement: str
    retain_rule: str
    name: str | None = None
    similarity_method: str | None = None
    similarity_threshold: float | None = None
    similarity_min_cluster_size: int | None = None
    grouping_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewSplitPolicy:
    strategy: str
    seed: int
    group_by: tuple[str, ...]
    empty_split_policy: str
    min_item_count: int
    min_duration_ns: int
    rounding_rule: str
    name: str | None = None
    train_ratio: float = 0.8
    validation_ratio: float = 0.1
    test_ratio: float = 0.1
    train_count: int | None = None
    validation_count: int | None = None
    test_count: int | None = None
    stratify_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewLeakagePolicy:
    hard_rules: tuple[str, ...]
    soft_rules: tuple[str, ...]
    waiver_allowed_rules: tuple[str, ...]
    name: str | None = None
    similarity_grouping_enabled: bool = False
    scene_grouping_enabled: bool = True
    batch_grouping_enabled: bool = False
    temporal_overlap_enabled: bool = True


@dataclass(frozen=True, slots=True)
class NewQualityPolicy:
    min_selected_count: int = 1
    allow_warn_quality: bool = False
    require_leakage_pass: bool = False
    name: str | None = None


@dataclass(frozen=True, slots=True)
class NewDatasetVersion:
    selection_rule_version: str
    selection_filters: Mapping[str, object]
    dedup_policy_id: UUID
    dedup_policy_version: int
    split_policy_id: UUID
    split_policy_version: int
    leakage_policy_id: UUID
    leakage_policy_version: int
    quality_policy_id: UUID
    quality_policy_version: int
    supersedes_version_id: UUID | None = None
    build_now: bool = False


@dataclass(frozen=True, slots=True)
class PreviewSelection:
    selection_filters: Mapping[str, object]
    dedup_policy_id: UUID
    dedup_policy_version: int
    split_policy_id: UUID
    split_policy_version: int
    leakage_policy_id: UUID
    leakage_policy_version: int
    quality_policy_id: UUID | None = None
    quality_policy_version: int | None = None
    sample_limit: int = 50


@dataclass(frozen=True, slots=True)
class ActionCommand:
    reason: str | None = None
    comment: str | None = None
    evidence_refs: tuple[str, ...] = ()
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LeakageWaiverCommand:
    rule: str
    reason: str
    impact: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewExportJob:
    dataset_version_id: UUID
    format: str
    format_version: str
    exporter_id: str
    exporter_version: str
    mapping_version: str
    config: Mapping[str, object]
    timeout_seconds: int | None = None
    max_attempts: int | None = None


@dataclass(frozen=True, slots=True)
class ExportJobActionCommand:
    action: str
    expected_version: int
    reason: str | None = None
