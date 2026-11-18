"""数据集核心实体：Dataset、DatasetVersion、DatasetItem 与 SelectionPreview。

所有 Hash 由服务端计算；构造时若提供 Hash 会复算校验，不一致直接拒绝。
实体不依赖 Web/ORM/队列/云 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.datasets.canonical import plain, sha256_value
from app.domain.datasets.enums import (
    DatasetPurpose,
    DatasetStatus,
    DatasetUsagePolicy,
    DatasetVersionStatus,
    LeakageStatus,
    SampleRole,
    Split,
    ValidityStatus,
)
from app.domain.datasets.errors import DomainValidationError, InvalidStateTransitionError
from app.domain.datasets.policies import PolicyRef
from app.domain.datasets.state_machines import (
    allowed_validity_actions,
    validity_target,
)
from app.domain.datasets.validation import (
    aware_datetime,
    coerce_enum,
    non_negative_int,
    optional_aware_datetime,
    optional_sha256,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    text_value,
)
from app.domain.datasets.validation import (
    sha256_value as ensure_sha,
)


@dataclass(frozen=True, slots=True)
class Dataset:
    dataset_id: UUID
    project_id: UUID
    dataset_lineage_id: UUID
    code: str
    name: str
    purpose: DatasetPurpose | str
    owner_id: UUID
    usage_policy: DatasetUsagePolicy | str
    status: DatasetStatus | str = DatasetStatus.DRAFT
    description: str | None = None
    current_version_id: UUID | None = None
    tags: tuple[str, ...] = ()
    retention_policy: dict[str, object] | None = None
    default_dedup_policy_id: UUID | None = None
    default_split_policy_id: UUID | None = None
    default_leakage_policy_id: UUID | None = None
    frozen_version_count: int = 0
    archived_at: datetime | None = None
    archived_by: UUID | None = None
    archive_reason: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("dataset_id", "project_id", "dataset_lineage_id", "owner_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "code", text_value(self.code, "code", max_length=64))
        object.__setattr__(self, "name", text_value(self.name, "name", max_length=200))
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, "description", max_length=2000),
        )
        object.__setattr__(self, "purpose", coerce_enum(self.purpose, DatasetPurpose, "purpose"))
        object.__setattr__(self, "status", coerce_enum(self.status, DatasetStatus, "status"))
        object.__setattr__(
            self,
            "usage_policy",
            coerce_enum(self.usage_policy, DatasetUsagePolicy, "usage_policy"),
        )
        object.__setattr__(
            self,
            "current_version_id",
            optional_uuid(self.current_version_id, "current_version_id"),
        )
        tags = tuple(text_value(item, "tags", max_length=64) for item in self.tags)
        object.__setattr__(self, "tags", tuple(dict.fromkeys(tags)))
        object.__setattr__(self, "retention_policy", dict(self.retention_policy or {}) or None)
        for name in (
            "default_dedup_policy_id",
            "default_split_policy_id",
            "default_leakage_policy_id",
        ):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "frozen_version_count",
            non_negative_int(self.frozen_version_count, "frozen_version_count"),
        )
        object.__setattr__(
            self, "archived_at", optional_aware_datetime(self.archived_at, "archived_at")
        )
        object.__setattr__(self, "archived_by", optional_uuid(self.archived_by, "archived_by"))
        object.__setattr__(
            self,
            "archive_reason",
            optional_text(self.archive_reason, "archive_reason", max_length=2000),
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))

    @property
    def is_archived(self) -> bool:
        return self.status is DatasetStatus.ARCHIVED


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    dataset_version_id: UUID
    project_id: UUID
    dataset_id: UUID
    dataset_lineage_id: UUID
    dataset_version: int
    selection_rule_version: str
    selection_filters: dict[str, object]
    selection_snapshot_at: datetime
    dedup_policy_ref: PolicyRef
    split_policy_ref: PolicyRef
    leakage_policy_ref: PolicyRef
    quality_policy_ref: PolicyRef
    status: DatasetVersionStatus | str = DatasetVersionStatus.DRAFT
    validity_status: ValidityStatus | str = ValidityStatus.VALID
    candidate_count: int = 0
    selected_count: int = 0
    excluded_count: int = 0
    duplicated_count: int = 0
    item_count: int = 0
    train_count: int = 0
    validation_count: int = 0
    test_count: int = 0
    total_duration_ns: int | None = None
    total_bytes: int | None = None
    episode_version_digest: str | None = None
    annotation_schema_versions: tuple[dict[str, object], ...] = ()
    annotation_submission_refs_digest: str | None = None
    manifest_key: str | None = None
    manifest_sha256: str | None = None
    quality_report_id: UUID | None = None
    quality_report_sha256: str | None = None
    leakage_report_id: UUID | None = None
    leakage_report_sha256: str | None = None
    leakage_status: LeakageStatus | str | None = None
    source_snapshot_at: datetime | None = None
    build_attempt: int = 0
    freeze_approval_id: UUID | None = None
    frozen_by: UUID | None = None
    frozen_at: datetime | None = None
    supersedes_version_id: UUID | None = None
    superseded_by_version_id: UUID | None = None
    archived_at: datetime | None = None
    archive_reason: str | None = None
    blocking_issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    exclusion_summary: dict[str, object] = field(default_factory=dict)
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "dataset_version_id",
            "project_id",
            "dataset_id",
            "dataset_lineage_id",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "dataset_version",
            positive_int(self.dataset_version, "dataset_version"),
        )
        object.__setattr__(
            self,
            "selection_rule_version",
            text_value(self.selection_rule_version, "selection_rule_version", max_length=64),
        )
        object.__setattr__(self, "selection_filters", dict(self.selection_filters))
        object.__setattr__(
            self,
            "selection_snapshot_at",
            aware_datetime(self.selection_snapshot_at, "selection_snapshot_at"),
        )
        for name in (
            "dedup_policy_ref",
            "split_policy_ref",
            "leakage_policy_ref",
            "quality_policy_ref",
        ):
            if not isinstance(getattr(self, name), PolicyRef):
                raise DomainValidationError(f"{name} 必须是 PolicyRef")
        object.__setattr__(self, "status", coerce_enum(self.status, DatasetVersionStatus, "status"))
        object.__setattr__(
            self,
            "validity_status",
            coerce_enum(self.validity_status, ValidityStatus, "validity_status"),
        )
        for name in (
            "candidate_count",
            "selected_count",
            "excluded_count",
            "duplicated_count",
            "item_count",
            "train_count",
            "validation_count",
            "test_count",
            "build_attempt",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        for name in ("total_duration_ns", "total_bytes"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, non_negative_int(value, name))
        for name in (
            "episode_version_digest",
            "annotation_submission_refs_digest",
            "manifest_sha256",
            "quality_report_sha256",
            "leakage_report_sha256",
        ):
            object.__setattr__(self, name, optional_sha256(getattr(self, name), name))
        object.__setattr__(
            self,
            "annotation_schema_versions",
            tuple(dict(item) for item in self.annotation_schema_versions),
        )
        object.__setattr__(
            self,
            "manifest_key",
            optional_text(self.manifest_key, "manifest_key", max_length=1024),
        )
        for name in (
            "quality_report_id",
            "leakage_report_id",
            "freeze_approval_id",
            "frozen_by",
            "supersedes_version_id",
            "superseded_by_version_id",
        ):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        if self.leakage_status is not None:
            object.__setattr__(
                self,
                "leakage_status",
                coerce_enum(self.leakage_status, LeakageStatus, "leakage_status"),
            )
        for name in (
            "source_snapshot_at",
            "frozen_at",
            "archived_at",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "archive_reason",
            optional_text(self.archive_reason, "archive_reason", max_length=2000),
        )
        object.__setattr__(self, "blocking_issues", tuple(self.blocking_issues))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "exclusion_summary", dict(self.exclusion_summary))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        for name in ("created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        self._assert_invariants()

    @property
    def allowed_validity_actions(self) -> frozenset[str]:
        """有效性轴的**对外**动作。

        ``system-mark-stale`` / ``system-mark-unknown`` 由后台巡检在检测到上游
        变化时发起，不是用户按钮；人工可点的只有「标记失效」与「重新确认有效」。
        """
        return frozenset(
            action
            for action in allowed_validity_actions(self.validity_status)
            if not action.startswith("system-")
        )

    def apply_validity_action(
        self,
        action: str,
        *,
        reason: str | None,
        now: datetime,
        actor_id: UUID,
    ) -> DatasetVersion:
        """有效性状态迁移（``STALE → INVALIDATED`` / ``STALE → VALID``）。

        与生命周期状态分离：失效不改变版本内容与生命周期状态，只影响下游
        （导出/训练）能否继续引用。契约要求每次迁移都留下原因——包括系统巡检
        发起的 ``system-mark-stale``——这里统一强制。
        """
        if action not in allowed_validity_actions(self.validity_status):
            # 非法迁移是 409（state_transition_invalid），不是参数错误。
            raise InvalidStateTransitionError(
                "数据集版本有效性", self.validity_status.value, action
            )
        cleaned = (reason or "").strip()
        if not cleaned:
            raise DomainValidationError("有效性迁移必须填写原因")
        if len(cleaned) > 2000:
            raise DomainValidationError("原因长度不得超过 2000")
        target = validity_target(self.validity_status, action)
        return replace(
            self,
            validity_status=target,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def _assert_invariants(self) -> None:
        if self.item_count != self.train_count + self.validation_count + self.test_count:
            raise DomainValidationError("item_count 必须等于三个划分计数之和")
        if self.status is DatasetVersionStatus.FROZEN:
            required = (
                self.manifest_sha256,
                self.quality_report_sha256,
                self.leakage_report_sha256,
                self.frozen_at,
                self.frozen_by,
                self.freeze_approval_id,
            )
            if any(value is None for value in required):
                raise DomainValidationError(
                    "FROZEN 版本必须记录 Manifest/报告 Hash、审批与冻结时间"
                )
        if self.status in {DatasetVersionStatus.READY, DatasetVersionStatus.FROZEN}:
            if self.manifest_sha256 is None or self.quality_report_sha256 is None:
                raise DomainValidationError("READY/FROZEN 版本必须记录 Manifest 与质量报告 Hash")
            if self.train_count < 1 or self.validation_count < 1:
                raise DomainValidationError("训练与验证划分不得为空")

    def is_content_immutable(self) -> bool:
        return self.status in {
            DatasetVersionStatus.FROZEN,
            DatasetVersionStatus.SUPERSEDED,
            DatasetVersionStatus.ARCHIVED,
            DatasetVersionStatus.CANCELLED,
        }

    def assert_content_change_allowed(self, changes: dict[str, object]) -> None:
        if not self.is_content_immutable():
            return
        forbidden = set(changes) - {
            "status",
            "validity_status",
            "superseded_by_version_id",
            "archived_at",
            "archive_reason",
            "updated_at",
            "updated_by",
            "row_version",
        }
        if forbidden:
            raise DomainValidationError("冻结或终态版本的内容不可原地修改")


@dataclass(frozen=True, slots=True)
class DatasetItem:
    dataset_item_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    episode_id: UUID
    episode_version: int
    episode_lineage_id: UUID
    manifest_sha256: str
    raw_asset_ids: tuple[UUID, ...]
    annotation_submission_id: UUID
    annotation_version: int
    annotation_record_digest: str
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    split: Split | str
    sample_role: SampleRole | str = SampleRole.NORMAL
    weight: float = 1.0
    selected_reason: str = "ELIGIBLE"
    stratum: str | None = None
    dedup_group_id: UUID | None = None
    similarity_group_id: UUID | None = None
    scene_group_id: UUID | None = None
    time_range: dict[str, object] | None = None
    item_sha256: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "dataset_item_id",
            "project_id",
            "dataset_version_id",
            "episode_id",
            "episode_lineage_id",
            "annotation_submission_id",
            "schema_id",
        ):
            required_uuid(getattr(self, name), name)
        for name in ("episode_version", "annotation_version", "schema_version"):
            object.__setattr__(self, name, positive_int(getattr(self, name), name))
        object.__setattr__(
            self,
            "manifest_sha256",
            ensure_sha(self.manifest_sha256, "manifest_sha256"),
        )
        assets = tuple(self.raw_asset_ids)
        if not assets:
            raise DomainValidationError("raw_asset_ids 不得为空")
        for asset in assets:
            required_uuid(asset, "raw_asset_ids")
        object.__setattr__(self, "raw_asset_ids", tuple(dict.fromkeys(assets)))
        object.__setattr__(
            self,
            "annotation_record_digest",
            ensure_sha(self.annotation_record_digest, "annotation_record_digest"),
        )
        object.__setattr__(self, "schema_sha256", ensure_sha(self.schema_sha256, "schema_sha256"))
        object.__setattr__(self, "split", coerce_enum(self.split, Split, "split"))
        object.__setattr__(
            self,
            "sample_role",
            coerce_enum(self.sample_role, SampleRole, "sample_role"),
        )
        if not isinstance(self.weight, int | float) or isinstance(self.weight, bool):
            raise DomainValidationError("weight 必须是数字")
        if not 0 < float(self.weight) <= 1000:
            raise DomainValidationError("weight 必须大于 0 且不超过 1000")
        object.__setattr__(self, "weight", float(self.weight))
        object.__setattr__(
            self,
            "selected_reason",
            text_value(self.selected_reason, "selected_reason", max_length=500),
        )
        object.__setattr__(
            self,
            "stratum",
            optional_text(self.stratum, "stratum", max_length=200),
        )
        for name in ("dedup_group_id", "similarity_group_id", "scene_group_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(self, "time_range", dict(self.time_range or {}) or None)
        object.__setattr__(
            self, "created_at", optional_aware_datetime(self.created_at, "created_at")
        )
        digest = sha256_value(self._payload())
        if self.item_sha256 is not None and ensure_sha(self.item_sha256, "item_sha256") != digest:
            raise DomainValidationError("item_sha256 与条目内容不一致")
        object.__setattr__(self, "item_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "dataset_item_id": str(self.dataset_item_id),
            "project_id": str(self.project_id),
            "dataset_version_id": str(self.dataset_version_id),
            "episode_id": str(self.episode_id),
            "episode_version": self.episode_version,
            "episode_lineage_id": str(self.episode_lineage_id),
            "manifest_sha256": self.manifest_sha256,
            "raw_asset_ids": [str(item) for item in sorted(self.raw_asset_ids)],
            "annotation_submission_id": str(self.annotation_submission_id),
            "annotation_version": self.annotation_version,
            "annotation_record_digest": self.annotation_record_digest,
            "schema_id": str(self.schema_id),
            "schema_version": self.schema_version,
            "schema_sha256": self.schema_sha256,
            "split": self.split.value,
            "sample_role": self.sample_role.value,
            "weight": self.weight,
            "selected_reason": self.selected_reason,
            "stratum": self.stratum,
            "dedup_group_id": str(self.dedup_group_id) if self.dedup_group_id else None,
            "similarity_group_id": (
                str(self.similarity_group_id) if self.similarity_group_id else None
            ),
            "scene_group_id": str(self.scene_group_id) if self.scene_group_id else None,
            "time_range": plain(self.time_range),
        }

    def to_payload(self) -> dict[str, object]:
        payload = self._payload()
        payload["item_sha256"] = self.item_sha256
        # ``created_at`` 不参与 item_hash（时间不影响内容摘要），但契约
        # ``dataset-item.v1.schema.json`` 把它列为 required，Manifest 内联
        # items 必须给客户端该字段，否则响应模型会拒收。
        payload["created_at"] = (
            self.created_at.astimezone(UTC).isoformat() if self.created_at is not None else None
        )
        return payload


@dataclass(frozen=True, slots=True)
class SelectionPreview:
    project_id: UUID
    dataset_id: UUID
    candidate_count: int
    selected_count: int
    excluded_count: int
    duplicated_count: int
    split_counts: dict[str, int]
    split_preview_status: str
    exclusion_summary: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]
    blocking_issues: tuple[str, ...]
    source_snapshot_at: datetime
    preview_sha256: str | None = None
    dataset_version_id: UUID | None = None
    items_sample: tuple[dict[str, object], ...] = ()
    preview_id: UUID | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "dataset_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "dataset_version_id",
            optional_uuid(self.dataset_version_id, "dataset_version_id"),
        )
        object.__setattr__(self, "preview_id", optional_uuid(self.preview_id, "preview_id"))
        for name in (
            "candidate_count",
            "selected_count",
            "excluded_count",
            "duplicated_count",
        ):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        object.__setattr__(self, "split_counts", dict(self.split_counts))
        object.__setattr__(
            self,
            "split_preview_status",
            text_value(self.split_preview_status, "split_preview_status", max_length=32),
        )
        object.__setattr__(
            self,
            "exclusion_summary",
            tuple(dict(item) for item in self.exclusion_summary),
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "blocking_issues", tuple(self.blocking_issues))
        object.__setattr__(
            self,
            "source_snapshot_at",
            aware_datetime(self.source_snapshot_at, "source_snapshot_at"),
        )
        object.__setattr__(
            self,
            "items_sample",
            tuple(dict(item) for item in self.items_sample),
        )
        object.__setattr__(
            self, "created_at", optional_aware_datetime(self.created_at, "created_at")
        )
        digest = sha256_value(self._payload())
        if (
            self.preview_sha256 is not None
            and ensure_sha(
                self.preview_sha256,
                "preview_sha256",
            )
            != digest
        ):
            raise DomainValidationError("preview_sha256 与预览内容不一致")
        object.__setattr__(self, "preview_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "project_id": str(self.project_id),
            "dataset_id": str(self.dataset_id),
            "dataset_version_id": (
                str(self.dataset_version_id) if self.dataset_version_id else None
            ),
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "excluded_count": self.excluded_count,
            "duplicated_count": self.duplicated_count,
            "split_counts": self.split_counts,
            "split_preview_status": self.split_preview_status,
            "exclusion_summary": list(self.exclusion_summary),
            "warnings": list(self.warnings),
            "blocking_issues": list(self.blocking_issues),
            "source_snapshot_at": self.source_snapshot_at.isoformat(),
            "items_sample": list(self.items_sample),
        }


def new_dataset(
    *,
    dataset_id: UUID,
    project_id: UUID,
    dataset_lineage_id: UUID,
    code: str,
    name: str,
    purpose: DatasetPurpose | str,
    owner_id: UUID,
    usage_policy: DatasetUsagePolicy | str,
    actor_id: UUID,
    now: datetime,
    description: str | None = None,
    tags: tuple[str, ...] = (),
    retention_policy: dict[str, object] | None = None,
    default_dedup_policy_id: UUID | None = None,
    default_split_policy_id: UUID | None = None,
    default_leakage_policy_id: UUID | None = None,
) -> Dataset:
    return Dataset(
        dataset_id=dataset_id,
        project_id=project_id,
        dataset_lineage_id=dataset_lineage_id,
        code=code,
        name=name,
        purpose=purpose,
        owner_id=owner_id,
        usage_policy=usage_policy,
        description=description,
        tags=tags,
        retention_policy=retention_policy,
        default_dedup_policy_id=default_dedup_policy_id,
        default_split_policy_id=default_split_policy_id,
        default_leakage_policy_id=default_leakage_policy_id,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


def update_dataset(
    dataset: Dataset,
    changes: dict[str, object],
    *,
    actor_id: UUID,
    now: datetime,
) -> Dataset:
    allowed = {
        "name",
        "description",
        "purpose",
        "tags",
        "usage_policy",
        "retention_policy",
        "default_dedup_policy_id",
        "default_split_policy_id",
        "default_leakage_policy_id",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise DomainValidationError("请求包含不允许修改的字段")
    if dataset.status is DatasetStatus.ARCHIVED:
        raise DomainValidationError("已归档数据集不可编辑")
    return replace(
        dataset,
        **changes,
        updated_at=now,
        updated_by=actor_id,
        row_version=dataset.row_version + 1,
    )


__all__ = [
    "Dataset",
    "DatasetItem",
    "DatasetVersion",
    "SelectionPreview",
    "new_dataset",
    "update_dataset",
]
