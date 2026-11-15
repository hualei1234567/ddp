"""标注样本集与 Episode 引用值对象。

样本集只引用 Episode（数据库复合外键指向 ``episodes``），不得绕开 Episode
直接引用 RawAsset。快照 Hash 由筛选条件、去重策略与 Episode 引用清单计算；
进入 SNAPSHOTTED 后实体与数据库触发器都不允许静默变更。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationSampleSetAction,
    AnnotationSampleSetStatus,
    DataSource,
    DedupKey,
    SampleRole,
    SampleStrategy,
    SampleTimeDomain,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    SampleSetCancelledError,
    SampleSetEmptyError,
    SampleSetNotSnapshottedError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    sample_set_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    REASON_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    canonical_sha256,
    check_ns_range,
    coerce_enum,
    frozen_mapping,
    frozen_sequence,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    validate_sha256,
)

SAMPLE_ROLE_VALUES = frozenset(role.value for role in SampleRole)
SAMPLE_TIME_DOMAIN_VALUES = frozenset(domain.value for domain in SampleTimeDomain)


def _now() -> datetime:
    return datetime.now(UTC)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SampleEpisodeRef:
    project_id: UUID
    sample_set_id: UUID
    episode_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    sample_role: SampleRole | str = SampleRole.NORMAL
    selected_reason: str = ""
    stratum: str | None = None
    start_time_ns: int | None = None
    end_time_ns: int | None = None
    time_domain: SampleTimeDomain | str = SampleTimeDomain.UTC
    ref_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "sample_set_id", "episode_id", "episode_lineage_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "episode_version",
            positive_int(self.episode_version, "episode_version"),
        )
        object.__setattr__(
            self,
            "sample_role",
            coerce_enum(self.sample_role, SampleRole, "sample_role"),
        )
        object.__setattr__(
            self,
            "selected_reason",
            normalized_text(self.selected_reason, "selected_reason", max_length=500),
        )
        object.__setattr__(
            self,
            "stratum",
            optional_text(self.stratum, "stratum", max_length=100),
        )
        check_ns_range(self.start_time_ns, self.end_time_ns, "time_range")
        object.__setattr__(
            self,
            "time_domain",
            coerce_enum(self.time_domain, SampleTimeDomain, "time_domain"),
        )
        object.__setattr__(self, "ref_id", optional_uuid(self.ref_id, "ref_id"))

    @property
    def dedup_fingerprint(self) -> tuple[UUID, int]:
        return (self.episode_lineage_id, self.episode_version)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "episode_id": str(self.episode_id),
            "episode_lineage_id": str(self.episode_lineage_id),
            "episode_version": self.episode_version,
            "sample_role": self.sample_role.value,
            "selected_reason": self.selected_reason,
            "stratum": self.stratum,
            "time_domain": self.time_domain.value,
        }
        if self.start_time_ns is not None:
            payload["start_time_ns"] = self.start_time_ns
            payload["end_time_ns"] = self.end_time_ns
        return payload


def sample_set_snapshot_payload(sample_set: AnnotationSampleSet) -> dict[str, object]:
    """样本集快照 Hash 的稳定载荷。"""
    refs = sorted(
        sample_set.episode_refs,
        key=lambda item: (
            str(item.episode_lineage_id),
            item.episode_version,
            str(item.episode_id),
        ),
    )
    return {
        "selection_rule_version": sample_set.selection_rule_version,
        "strategy": sample_set.strategy.value,
        "filters": _plain(sample_set.filters),
        "strata": [_plain(item) for item in sample_set.strata],
        "dedup_key": sample_set.dedup_key.value,
        "data_source": sample_set.data_source.value,
        "candidate_count": sample_set.candidate_count,
        "selected_count": sample_set.selected_count,
        "excluded_count": sample_set.excluded_count,
        "deduplicated_count": sample_set.deduplicated_count,
        "episode_refs": [item.to_payload() for item in refs],
    }


def compute_sample_set_sha256(sample_set: AnnotationSampleSet) -> str:
    return canonical_sha256(sample_set_snapshot_payload(sample_set))


@dataclass(frozen=True, slots=True)
class AnnotationSampleSet:
    sample_set_id: UUID
    project_id: UUID
    code: str
    status: AnnotationSampleSetStatus | str = AnnotationSampleSetStatus.DRAFT
    name: str | None = None
    selection_rule_version: str = "stage3-sample-selection/1"
    strategy: SampleStrategy | str = SampleStrategy.FILTER
    filters: Mapping[str, object] = field(default_factory=dict)
    strata: tuple[Mapping[str, object], ...] = ()
    episode_refs: tuple[SampleEpisodeRef, ...] = ()
    dedup_key: DedupKey | str = DedupKey.EPISODE_LINEAGE
    candidate_count: int = 0
    selected_count: int = 0
    excluded_count: int = 0
    deduplicated_count: int = 0
    snapshot_sha256: str | None = None
    snapshot_at: datetime | None = None
    cancelled_reason: str | None = None
    data_source: DataSource | str = DataSource.UNKNOWN
    exclusion_summary: Mapping[str, object] = field(default_factory=dict)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        required_uuid(self.sample_set_id, "sample_set_id")
        required_uuid(self.project_id, "project_id")
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            optional_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationSampleSetStatus, "status"),
        )
        object.__setattr__(
            self,
            "selection_rule_version",
            normalized_text(
                self.selection_rule_version,
                "selection_rule_version",
                max_length=VERSION_TEXT_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "strategy",
            coerce_enum(self.strategy, SampleStrategy, "strategy"),
        )
        object.__setattr__(self, "filters", frozen_mapping(self.filters, "filters"))
        object.__setattr__(self, "strata", frozen_sequence(self.strata, "strata"))
        object.__setattr__(
            self,
            "dedup_key",
            coerce_enum(self.dedup_key, DedupKey, "dedup_key"),
        )
        object.__setattr__(
            self,
            "data_source",
            coerce_enum(self.data_source, DataSource, "data_source"),
        )
        object.__setattr__(
            self,
            "exclusion_summary",
            frozen_mapping(self.exclusion_summary, "exclusion_summary"),
        )
        refs: list[SampleEpisodeRef] = []
        seen_episodes: set[UUID] = set()
        for item in self.episode_refs:
            if not isinstance(item, SampleEpisodeRef):
                raise DomainValidationError("episode_refs 只能包含 SampleEpisodeRef")
            if item.project_id != self.project_id:
                raise DomainValidationError("样本引用必须与样本集同项目")
            if item.sample_set_id != self.sample_set_id:
                raise DomainValidationError("样本引用必须指向当前样本集")
            if item.episode_id in seen_episodes:
                raise DomainValidationError("同一 Episode 在样本集中只能出现一次")
            seen_episodes.add(item.episode_id)
            refs.append(item)
        object.__setattr__(self, "episode_refs", tuple(refs))
        for name in (
            "candidate_count",
            "selected_count",
            "excluded_count",
            "deduplicated_count",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        if self.selected_count != len(refs):
            raise DomainValidationError("selected_count 必须等于 episode_refs 数量")
        if self.candidate_count < self.selected_count:
            raise DomainValidationError("candidate_count 不得小于 selected_count")
        object.__setattr__(
            self,
            "snapshot_at",
            optional_aware_datetime(self.snapshot_at, "snapshot_at"),
        )
        object.__setattr__(
            self,
            "cancelled_reason",
            optional_text(
                self.cancelled_reason,
                "cancelled_reason",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        if self.status in {
            AnnotationSampleSetStatus.SNAPSHOTTED,
            AnnotationSampleSetStatus.CONSUMED,
        }:
            if self.snapshot_sha256 is None or self.snapshot_at is None:
                raise DomainValidationError("快照样本集必须记录 snapshot_sha256 与 snapshot_at")
        if self.status is AnnotationSampleSetStatus.PREVIEW and self.snapshot_sha256 is not None:
            raise DomainValidationError("PREVIEW 样本集不得提前写入快照 Hash")
        if self.snapshot_sha256 is not None:
            provided = validate_sha256(self.snapshot_sha256, "snapshot_sha256")
            if provided != compute_sample_set_sha256(self):
                raise DomainValidationError("snapshot_sha256 与样本集快照内容不一致")

    @property
    def is_snapshotted(self) -> bool:
        return self.status in {
            AnnotationSampleSetStatus.SNAPSHOTTED,
            AnnotationSampleSetStatus.CONSUMED,
        }

    def assert_usable_for_task(self) -> None:
        """样本集能否被任务引用（任务创建/审批/就绪/执行的前置条件）。

        契约把这条前置条件拆成两个码，客户端据此区分是"得先快照"还是
        "这份样本集已经作废、要换一份"：
        - CANCELLED → ``annotation_sample_set_cancelled``
        - 其余非快照状态 → ``annotation_sample_set_not_snapshotted``
        CONSUMED 允许：已被批准的任务继续执行是合法路径，只是不能再被新任务引用。
        """
        if self.status is AnnotationSampleSetStatus.CANCELLED:
            raise SampleSetCancelledError(
                "任务引用的样本集已取消",
                details={
                    "sample_set_id": str(self.sample_set_id),
                    "status": self.status.value,
                },
            )
        if not self.is_snapshotted:
            raise SampleSetNotSnapshottedError(
                "任务必须引用已快照且未消费的样本集",
                details={
                    "sample_set_id": str(self.sample_set_id),
                    "status": self.status.value,
                },
            )

    def preview(
        self,
        *,
        episode_refs: tuple[SampleEpisodeRef, ...],
        candidate_count: int,
        excluded_count: int,
        deduplicated_count: int,
        exclusion_summary: Mapping[str, object] | None = None,
        now: datetime | None = None,
    ) -> AnnotationSampleSet:
        if self.status not in {
            AnnotationSampleSetStatus.DRAFT,
            AnnotationSampleSetStatus.PREVIEW,
        }:
            sample_set_target(self.status, AnnotationSampleSetAction.PREVIEW.value)
        occurred_at = now or _now()
        return replace(
            self,
            status=AnnotationSampleSetStatus.PREVIEW,
            episode_refs=episode_refs,
            selected_count=len(episode_refs),
            candidate_count=candidate_count,
            excluded_count=excluded_count,
            deduplicated_count=deduplicated_count,
            exclusion_summary=exclusion_summary or {},
            updated_at=occurred_at,
        )

    def snapshot(self, *, now: datetime | None = None) -> AnnotationSampleSet:
        sample_set_target(self.status, AnnotationSampleSetAction.SNAPSHOT.value)
        if not self.episode_refs:
            raise SampleSetEmptyError("样本集为空，不能快照")
        occurred_at = now or _now()
        # 先算 Hash 再构造 SNAPSHOTTED，避免中间对象缺少 snapshot_sha256 触发校验。
        snapshot_sha256 = canonical_sha256(sample_set_snapshot_payload(self))
        return replace(
            self,
            status=AnnotationSampleSetStatus.SNAPSHOTTED,
            snapshot_at=occurred_at,
            updated_at=occurred_at,
            snapshot_sha256=snapshot_sha256,
        )

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationSampleSet:
        action_name = validate_action_name(action, family="sample_set")
        target = sample_set_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("sample_set", action_name) and cleaned_reason is None:
            raise DomainValidationError("该动作必须填写原因")
        changes: dict[str, object] = {
            "status": target,
            "updated_at": occurred_at,
        }
        if target is AnnotationSampleSetStatus.CANCELLED:
            changes["cancelled_reason"] = cleaned_reason
        return replace(self, **changes)


__all__ = [
    "AnnotationSampleSet",
    "SampleEpisodeRef",
    "compute_sample_set_sha256",
    "sample_set_snapshot_payload",
]
