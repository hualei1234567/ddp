"""数据集策略值对象：去重、划分、泄漏与质量策略。

策略按版本追加：每次创建生成新的 policy_id/version，内容 Hash 由服务端计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.datasets.canonical import sha256_value
from app.domain.datasets.enums import (
    HARD_LEAKAGE_RULES,
    SOFT_LEAKAGE_RULES,
    DedupKey,
    EmptySplitPolicy,
    GroupingKey,
    LeakageRule,
    NearDuplicateEnforcement,
    RetainRule,
    RoundingRule,
    SplitStrategy,
    StratifyKey,
)
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.validation import (
    coerce_enum,
    enum_list,
    non_negative_int,
    optional_aware_datetime,
    optional_enum_list,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    sha256_value as ensure_sha,
    text_value,
)


@dataclass(frozen=True, slots=True)
class PolicyRef:
    policy_id: UUID
    version: int
    policy_sha256: str

    def __post_init__(self) -> None:
        required_uuid(self.policy_id, "policy_id")
        positive_int(self.version, "policy version")
        ensure_sha(self.policy_sha256, "policy_sha256")

    def to_payload(self) -> dict[str, object]:
        return {
            "policy_id": str(self.policy_id),
            "version": self.version,
            "policy_sha256": self.policy_sha256,
        }


@dataclass(frozen=True, slots=True)
class DedupPolicy:
    dedup_policy_id: UUID
    project_id: UUID
    dataset_id: UUID
    version: int
    exact_keys: tuple[DedupKey, ...]
    near_duplicate_enabled: bool = False
    near_duplicate_enforcement: NearDuplicateEnforcement | str = (
        NearDuplicateEnforcement.WARN_ONLY
    )
    retain_rule: RetainRule | str = RetainRule.NEWEST_ACCEPTED
    name: str | None = None
    similarity_method: str | None = None
    similarity_threshold: float | None = None
    similarity_min_cluster_size: int | None = None
    grouping_keys: tuple[str, ...] = ()
    policy_sha256: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("dedup_policy_id", "project_id", "dataset_id"):
            required_uuid(getattr(self, name), name)
        positive_int(self.version, "version")
        object.__setattr__(self, "exact_keys", enum_list(self.exact_keys, DedupKey, "exact_keys"))
        object.__setattr__(
            self,
            "near_duplicate_enforcement",
            coerce_enum(
                self.near_duplicate_enforcement,
                NearDuplicateEnforcement,
                "near_duplicate_enforcement",
            ),
        )
        object.__setattr__(self, "retain_rule", coerce_enum(self.retain_rule, RetainRule, "retain_rule"))
        object.__setattr__(self, "name", optional_text(self.name, "name", max_length=200))
        object.__setattr__(
            self,
            "similarity_method",
            optional_text(self.similarity_method, "similarity_method", max_length=100),
        )
        if self.similarity_threshold is not None and not 0 <= self.similarity_threshold <= 1:
            raise DomainValidationError("similarity_threshold 必须在 0 到 1 之间")
        if self.similarity_min_cluster_size is not None and self.similarity_min_cluster_size < 2:
            raise DomainValidationError("similarity_min_cluster_size 必须大于等于 2")
        object.__setattr__(
            self,
            "grouping_keys",
            tuple(
                text_value(item, "grouping_keys", max_length=100)
                for item in (self.grouping_keys or ())
            ),
        )
        digest = sha256_value(self._payload())
        if self.policy_sha256 is not None and ensure_sha(self.policy_sha256, "policy_sha256") != digest:
            raise DomainValidationError("dedup_policy_sha256 与策略内容不一致")
        object.__setattr__(self, "policy_sha256", digest)
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        object.__setattr__(self, "created_at", optional_aware_datetime(self.created_at, "created_at"))
        object.__setattr__(self, "created_by", optional_uuid(self.created_by, "created_by"))

    def _payload(self) -> dict[str, object]:
        return {
            "dataset_id": str(self.dataset_id),
            "version": self.version,
            "exact_keys": [item.value for item in self.exact_keys],
            "near_duplicate_enabled": self.near_duplicate_enabled,
            "near_duplicate_enforcement": self.near_duplicate_enforcement.value,
            "similarity_method": self.similarity_method,
            "similarity_threshold": self.similarity_threshold,
            "similarity_min_cluster_size": self.similarity_min_cluster_size,
            "grouping_keys": list(self.grouping_keys),
            "retain_rule": self.retain_rule.value,
        }

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(self.dedup_policy_id, self.version, self.policy_sha256 or "")


@dataclass(frozen=True, slots=True)
class SplitPolicy:
    split_policy_id: UUID
    project_id: UUID
    dataset_id: UUID
    version: int
    strategy: SplitStrategy | str
    seed: int
    group_by: tuple[GroupingKey, ...]
    empty_split_policy: EmptySplitPolicy | str = EmptySplitPolicy.FAIL
    min_item_count: int = 1
    min_duration_ns: int = 0
    rounding_rule: RoundingRule | str = RoundingRule.LARGEST_REMAINDER
    name: str | None = None
    train_ratio: float = 0.8
    validation_ratio: float = 0.1
    test_ratio: float = 0.1
    train_count: int | None = None
    validation_count: int | None = None
    test_count: int | None = None
    stratify_by: tuple[StratifyKey, ...] = ()
    policy_sha256: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("split_policy_id", "project_id", "dataset_id"):
            required_uuid(getattr(self, name), name)
        positive_int(self.version, "version")
        object.__setattr__(self, "strategy", coerce_enum(self.strategy, SplitStrategy, "strategy"))
        object.__setattr__(self, "seed", non_negative_int(self.seed, "seed"))
        object.__setattr__(self, "group_by", enum_list(self.group_by, GroupingKey, "group_by"))
        object.__setattr__(
            self,
            "empty_split_policy",
            coerce_enum(self.empty_split_policy, EmptySplitPolicy, "empty_split_policy"),
        )
        object.__setattr__(self, "min_item_count", positive_int(self.min_item_count, "min_item_count"))
        object.__setattr__(
            self,
            "min_duration_ns",
            non_negative_int(self.min_duration_ns, "min_duration_ns"),
        )
        object.__setattr__(
            self,
            "rounding_rule",
            coerce_enum(self.rounding_rule, RoundingRule, "rounding_rule"),
        )
        object.__setattr__(self, "name", optional_text(self.name, "name", max_length=200))
        object.__setattr__(
            self,
            "stratify_by",
            optional_enum_list(self.stratify_by, StratifyKey, "stratify_by"),
        )
        for name in ("train_ratio", "validation_ratio", "test_ratio"):
            value = getattr(self, name)
            if not isinstance(value, int | float) or not 0 <= float(value) <= 1:
                raise DomainValidationError(f"{name} 必须在 0 到 1 之间")
            object.__setattr__(self, name, float(value))
        if self.strategy is SplitStrategy.RATIO:
            total = self.train_ratio + self.validation_ratio + self.test_ratio
            if abs(total - 1.0) > 1e-9:
                raise DomainValidationError("RATIO 划分比例之和必须为 1")
        else:
            for name in ("train_count", "validation_count", "test_count"):
                value = getattr(self, name)
                if (
                    value is None
                    or not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise DomainValidationError("FIXED_COUNT 必须提供非负的三项计数")
            if self.train_count == 0 or self.validation_count == 0:
                raise DomainValidationError("训练与验证划分不得为 0")
        digest = sha256_value(self._payload())
        if self.policy_sha256 is not None and ensure_sha(self.policy_sha256, "policy_sha256") != digest:
            raise DomainValidationError("split_policy_sha256 与策略内容不一致")
        object.__setattr__(self, "policy_sha256", digest)
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        object.__setattr__(self, "created_at", optional_aware_datetime(self.created_at, "created_at"))
        object.__setattr__(self, "created_by", optional_uuid(self.created_by, "created_by"))

    def _payload(self) -> dict[str, object]:
        return {
            "dataset_id": str(self.dataset_id),
            "version": self.version,
            "strategy": self.strategy.value,
            "train_ratio": self.train_ratio,
            "validation_ratio": self.validation_ratio,
            "test_ratio": self.test_ratio,
            "train_count": self.train_count,
            "validation_count": self.validation_count,
            "test_count": self.test_count,
            "seed": self.seed,
            "stratify_by": [item.value for item in self.stratify_by],
            "group_by": [item.value for item in self.group_by],
            "empty_split_policy": self.empty_split_policy.value,
            "min_item_count": self.min_item_count,
            "min_duration_ns": self.min_duration_ns,
            "rounding_rule": self.rounding_rule.value,
        }

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(self.split_policy_id, self.version, self.policy_sha256 or "")


@dataclass(frozen=True, slots=True)
class LeakagePolicy:
    leakage_policy_id: UUID
    project_id: UUID
    dataset_id: UUID
    version: int
    hard_rules: tuple[LeakageRule, ...]
    soft_rules: tuple[LeakageRule, ...]
    waiver_allowed_rules: tuple[LeakageRule, ...]
    name: str | None = None
    similarity_grouping_enabled: bool = False
    scene_grouping_enabled: bool = True
    batch_grouping_enabled: bool = False
    temporal_overlap_enabled: bool = True
    policy_sha256: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("leakage_policy_id", "project_id", "dataset_id"):
            required_uuid(getattr(self, name), name)
        positive_int(self.version, "version")
        object.__setattr__(self, "hard_rules", enum_list(self.hard_rules, LeakageRule, "hard_rules"))
        object.__setattr__(
            self,
            "soft_rules",
            optional_enum_list(self.soft_rules, LeakageRule, "soft_rules"),
        )
        object.__setattr__(
            self,
            "waiver_allowed_rules",
            optional_enum_list(
                self.waiver_allowed_rules,
                LeakageRule,
                "waiver_allowed_rules",
            ),
        )
        for rule in self.hard_rules:
            if rule not in HARD_LEAKAGE_RULES:
                raise DomainValidationError("hard_rules 只能包含硬规则")
        for rule in (*self.soft_rules, *self.waiver_allowed_rules):
            if rule not in SOFT_LEAKAGE_RULES:
                raise DomainValidationError("软规则/豁免列表不得包含硬规则")
        if not set(self.waiver_allowed_rules) <= set(self.soft_rules):
            raise DomainValidationError("waiver_allowed_rules 必须是 soft_rules 的子集")
        object.__setattr__(self, "name", optional_text(self.name, "name", max_length=200))
        digest = sha256_value(self._payload())
        if self.policy_sha256 is not None and ensure_sha(self.policy_sha256, "policy_sha256") != digest:
            raise DomainValidationError("leakage_policy_sha256 与策略内容不一致")
        object.__setattr__(self, "policy_sha256", digest)
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        object.__setattr__(self, "created_at", optional_aware_datetime(self.created_at, "created_at"))
        object.__setattr__(self, "created_by", optional_uuid(self.created_by, "created_by"))

    def _payload(self) -> dict[str, object]:
        return {
            "dataset_id": str(self.dataset_id),
            "version": self.version,
            "hard_rules": [item.value for item in self.hard_rules],
            "soft_rules": [item.value for item in self.soft_rules],
            "waiver_allowed_rules": [item.value for item in self.waiver_allowed_rules],
            "similarity_grouping_enabled": self.similarity_grouping_enabled,
            "scene_grouping_enabled": self.scene_grouping_enabled,
            "batch_grouping_enabled": self.batch_grouping_enabled,
            "temporal_overlap_enabled": self.temporal_overlap_enabled,
        }

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(self.leakage_policy_id, self.version, self.policy_sha256 or "")


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    quality_policy_id: UUID
    project_id: UUID
    dataset_id: UUID
    version: int
    min_selected_count: int = 1
    allow_warn_quality: bool = False
    require_leakage_pass: bool = True
    name: str | None = None
    policy_sha256: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("quality_policy_id", "project_id", "dataset_id"):
            required_uuid(getattr(self, name), name)
        positive_int(self.version, "version")
        object.__setattr__(
            self,
            "min_selected_count",
            positive_int(self.min_selected_count, "min_selected_count"),
        )
        object.__setattr__(self, "name", optional_text(self.name, "name", max_length=200))
        digest = sha256_value(self._payload())
        if self.policy_sha256 is not None and ensure_sha(self.policy_sha256, "policy_sha256") != digest:
            raise DomainValidationError("quality_policy_sha256 与策略内容不一致")
        object.__setattr__(self, "policy_sha256", digest)
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        object.__setattr__(self, "created_at", optional_aware_datetime(self.created_at, "created_at"))
        object.__setattr__(self, "created_by", optional_uuid(self.created_by, "created_by"))

    def _payload(self) -> dict[str, object]:
        return {
            "dataset_id": str(self.dataset_id),
            "version": self.version,
            "min_selected_count": self.min_selected_count,
            "allow_warn_quality": self.allow_warn_quality,
            "require_leakage_pass": self.require_leakage_pass,
        }

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(self.quality_policy_id, self.version, self.policy_sha256 or "")


__all__ = [
    "DedupPolicy",
    "LeakagePolicy",
    "PolicyRef",
    "QualityPolicy",
    "SplitPolicy",
]
