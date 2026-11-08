"""Episode 选择门禁与排除原因统计。

选择只读取数据库快照，不修改 Episode/Annotation；每个候选必须得到
SELECTED 或带稳定排除码的 EXCLUDED 结论，不允许静默丢弃。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

from app.domain.datasets.canonical import validate_sha256
from app.domain.datasets.enums import (
    DatasetUsagePolicy,
    ExclusionCode,
    Split,
)
from app.domain.datasets.errors import DomainValidationError

_NAMESPACE = UUID("6f8d4f1e-0f1a-4f0e-9e9f-2f6d1b7c1a31")
_ALLOWED_FILTER_KEYS = frozenset(
    {
        "episode_ids",
        "scene_labels",
        "robot_ids",
        "device_ids",
        "quality_statuses",
        "code_prefix",
        "captured_from",
        "captured_to",
        "allow_warn",
        "warn_confirmation_reason",
        "allow_degraded_time_sync",
        "degraded_confirmation_reason",
        "usage_policy_waiver_reason",
    }
)
_USABLE_ASSET_STATUSES = frozenset({"VALID", "ARCHIVED"})
_EPISODE_STATUSES = frozenset({"ACCEPTED", "COMPLETED"})
_GOOD_QUALITY = frozenset({"PASS"})
_APPROVED_SUBMISSION_STATUSES = frozenset({"APPROVED", "RESOLVED_APPROVED"})


class Decision(StrEnum):
    SELECTED = "SELECTED"
    EXCLUDED = "EXCLUDED"
    DUPLICATED = "DUPLICATED"


@dataclass(frozen=True, slots=True)
class SelectionFilters:
    episode_ids: tuple[UUID, ...] = ()
    scene_labels: tuple[str, ...] = ()
    robot_ids: tuple[UUID, ...] = ()
    device_ids: tuple[UUID, ...] = ()
    quality_statuses: tuple[str, ...] = ()
    code_prefix: str | None = None
    captured_from: datetime | None = None
    captured_to: datetime | None = None
    allow_warn: bool = False
    warn_confirmation_reason: str | None = None
    allow_degraded_time_sync: bool = False
    degraded_confirmation_reason: str | None = None
    usage_policy_waiver_reason: str | None = None
    raw: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, object] | None) -> SelectionFilters:
        raw = dict(value or {})
        unknown = set(raw) - _ALLOWED_FILTER_KEYS
        if unknown:
            raise DomainValidationError("selection_filters 包含未知字段")
        allow_warn = bool(raw.get("allow_warn", False))
        warn_reason = _optional_text(
            raw.get("warn_confirmation_reason"), "warn_confirmation_reason"
        )
        if allow_warn and warn_reason is None:
            raise DomainValidationError("允许 WARN Episode 必须提供确认原因")
        allow_degraded = bool(raw.get("allow_degraded_time_sync", False))
        degraded_reason = _optional_text(
            raw.get("degraded_confirmation_reason"),
            "degraded_confirmation_reason",
        )
        if allow_degraded and degraded_reason is None:
            raise DomainValidationError("允许 DEGRADED 时间同步必须提供确认原因")
        captured_from = _optional_datetime(raw.get("captured_from"), "captured_from")
        captured_to = _optional_datetime(raw.get("captured_to"), "captured_to")
        if captured_from is not None and captured_to is not None and captured_to < captured_from:
            raise DomainValidationError("captured_to 不得早于 captured_from")
        return cls(
            episode_ids=_uuid_tuple(raw.get("episode_ids"), "episode_ids"),
            scene_labels=_str_tuple(raw.get("scene_labels"), "scene_labels", 100),
            robot_ids=_uuid_tuple(raw.get("robot_ids"), "robot_ids"),
            device_ids=_uuid_tuple(raw.get("device_ids"), "device_ids"),
            quality_statuses=_str_tuple(raw.get("quality_statuses"), "quality_statuses", 32),
            code_prefix=_optional_text(raw.get("code_prefix"), "code_prefix", max_length=64),
            captured_from=captured_from,
            captured_to=captured_to,
            allow_warn=allow_warn,
            warn_confirmation_reason=warn_reason,
            allow_degraded_time_sync=allow_degraded,
            degraded_confirmation_reason=degraded_reason,
            usage_policy_waiver_reason=_optional_text(
                raw.get("usage_policy_waiver_reason"),
                "usage_policy_waiver_reason",
                max_length=2000,
            ),
            raw=raw,
        )

    def matches(self, candidate: EpisodeCandidate) -> bool:
        if self.episode_ids and candidate.episode_id not in self.episode_ids:
            return False
        if self.robot_ids and candidate.robot_id not in self.robot_ids:
            return False
        if self.device_ids and not set(self.device_ids) <= set(candidate.device_ids):
            return False
        if self.quality_statuses and candidate.quality_status not in self.quality_statuses:
            return False
        if self.code_prefix and not candidate.code.startswith(self.code_prefix):
            return False
        if self.scene_labels and not set(self.scene_labels) & set(candidate.scene_labels):
            return False
        captured = candidate.captured_at
        if self.captured_from is not None and (captured is None or captured < self.captured_from):
            return False
        if self.captured_to is not None and (captured is None or captured > self.captured_to):
            return False
        return True


@dataclass(frozen=True, slots=True)
class EpisodeCandidate:
    episode_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    code: str
    status: str
    quality_status: str
    annotation_status: str
    manifest_sha256: str | None
    scene_labels: tuple[str, ...]
    robot_id: UUID | None
    device_ids: tuple[UUID, ...]
    batch_id: UUID | None
    task_id: UUID | None
    start_time_ns: int
    end_time_ns: int
    duration_ns: int
    captured_at: datetime | None
    updated_at: datetime | None
    time_sync_status: str | None
    calibration_valid: bool
    calibration_issue: str | None
    annotation_submission_id: UUID | None
    annotation_version: int | None
    annotation_record_digest: str | None
    annotation_submission_status: str | None
    schema_id: UUID | None
    schema_version: int | None
    schema_sha256: str | None
    raw_asset_ids: tuple[UUID, ...]
    raw_asset_hashes: tuple[str, ...]
    raw_asset_statuses: tuple[str, ...]
    total_bytes: int = 0
    scene_group_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.end_time_ns <= self.start_time_ns:
            raise DomainValidationError("Episode 时间区间非法")
        if self.duration_ns != self.end_time_ns - self.start_time_ns:
            raise DomainValidationError("Episode duration_ns 与时间区间不一致")
        if self.scene_group_id is None and self.scene_labels:
            key = "|".join(sorted(self.scene_labels))
            object.__setattr__(
                self,
                "scene_group_id",
                uuid5(_NAMESPACE, f"scene:{key}"),
            )


@dataclass(frozen=True, slots=True)
class ExcludedCandidate:
    episode_id: UUID
    code: ExclusionCode | str
    reason: str
    detail: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "episode_id": str(self.episode_id),
            "decision": "EXCLUDED",
            "split": None,
            "reason": f"{_value(self.code)}:{self.reason}",
        }


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    candidates: tuple[EpisodeCandidate, ...]
    selected: tuple[EpisodeCandidate, ...]
    excluded: tuple[ExcludedCandidate, ...]
    warnings: tuple[str, ...]
    exclusion_summary: tuple[dict[str, object], ...]

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def selected_count(self) -> int:
        return len(self.selected)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)


def evaluate_selection(
    candidates: tuple[EpisodeCandidate, ...],
    *,
    filters: SelectionFilters,
    usage_policy: DatasetUsagePolicy | str,
) -> SelectionOutcome:
    usage = (
        usage_policy
        if isinstance(usage_policy, DatasetUsagePolicy)
        else DatasetUsagePolicy(usage_policy)
    )
    matched = tuple(candidate for candidate in candidates if filters.matches(candidate))
    selected: list[EpisodeCandidate] = []
    excluded: list[ExcludedCandidate] = []
    warnings: list[str] = []
    if usage is not DatasetUsagePolicy.TRAINING_ALLOWED:
        if filters.usage_policy_waiver_reason is None:
            code = (
                ExclusionCode.USAGE_POLICY_DENIED
                if usage is DatasetUsagePolicy.TRAINING_DENIED
                else ExclusionCode.USAGE_POLICY_UNKNOWN
            )
            for candidate in matched:
                excluded.append(
                    ExcludedCandidate(candidate.episode_id, code, f"数据集用途为 {usage.value}")
                )
            return _outcome(matched, selected, excluded, warnings)
        warnings.append("USAGE_POLICY_WAIVED")

    for candidate in matched:
        exclusion = _check_candidate(candidate, filters=filters, warnings=warnings)
        if exclusion is None:
            selected.append(candidate)
        else:
            excluded.append(exclusion)
    return _outcome(matched, selected, excluded, warnings)


def _check_candidate(
    candidate: EpisodeCandidate,
    *,
    filters: SelectionFilters,
    warnings: list[str],
) -> ExcludedCandidate | None:
    if candidate.status not in _EPISODE_STATUSES:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.EPISODE_STATUS_INELIGIBLE,
            f"Episode 状态 {candidate.status} 不可用于训练",
        )
    if candidate.quality_status not in _GOOD_QUALITY:
        if candidate.quality_status != "WARN":
            return ExcludedCandidate(
                candidate.episode_id,
                ExclusionCode.QUALITY_NOT_PASSED,
                f"质量状态 {candidate.quality_status}",
            )
        if not filters.allow_warn or filters.warn_confirmation_reason is None:
            return ExcludedCandidate(
                candidate.episode_id,
                ExclusionCode.QUALITY_WARN_UNCONFIRMED,
                "WARN 未获得确认",
            )
        warnings.append(f"QUALITY_WARN_CONFIRMED:{candidate.episode_id}")
    if (
        candidate.annotation_status != "APPROVED"
        or candidate.annotation_submission_id is None
        or candidate.annotation_version is None
        or candidate.annotation_record_digest is None
        or candidate.annotation_submission_status not in _APPROVED_SUBMISSION_STATUSES
        or candidate.schema_id is None
        or candidate.schema_version is None
        or candidate.schema_sha256 is None
    ):
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.ANNOTATION_NOT_APPROVED,
            "缺少已批准标注或 Schema 快照",
        )
    if candidate.manifest_sha256 is None:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.MANIFEST_MISSING,
            "缺少 Manifest Hash",
        )
    try:
        validate_sha256(candidate.manifest_sha256, "manifest_sha256")
        validate_sha256(candidate.annotation_record_digest, "annotation_record_digest")
        validate_sha256(candidate.schema_sha256, "schema_sha256")
    except ValueError:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.MANIFEST_HASH_INVALID,
            "Manifest/标注/Schema Hash 非法",
        )
    if not candidate.raw_asset_ids:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.RAW_ASSET_MISSING,
            "Episode 没有 RawAsset",
        )
    if len(candidate.raw_asset_ids) != len(candidate.raw_asset_hashes):
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.RAW_ASSET_HASH_INVALID,
            "RawAsset Hash 数量不一致",
        )
    try:
        for digest in candidate.raw_asset_hashes:
            validate_sha256(digest, "raw_asset_sha256")
    except ValueError:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.RAW_ASSET_HASH_INVALID,
            "RawAsset Hash 非法",
        )
    if any(status not in _USABLE_ASSET_STATUSES for status in candidate.raw_asset_statuses):
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.RAW_ASSET_NOT_USABLE,
            "RawAsset 处于隔离、墓碑或失效状态",
        )
    if candidate.time_sync_status in {"UNVERIFIED", "FAILED", None, ""}:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.TIME_SYNC_UNKNOWN,
            f"时间同步状态 {candidate.time_sync_status or 'UNKNOWN'}",
        )
    if candidate.time_sync_status == "DEGRADED":
        if not filters.allow_degraded_time_sync or filters.degraded_confirmation_reason is None:
            return ExcludedCandidate(
                candidate.episode_id,
                ExclusionCode.TIME_SYNC_DEGRADED_UNCONFIRMED,
                "DEGRADED 时间同步未获得确认",
            )
        warnings.append(f"TIME_SYNC_DEGRADED_CONFIRMED:{candidate.episode_id}")
    elif candidate.time_sync_status != "VERIFIED":
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.TIME_SYNC_UNKNOWN,
            f"未知时间同步状态 {candidate.time_sync_status}",
        )
    if not candidate.calibration_valid:
        return ExcludedCandidate(
            candidate.episode_id,
            ExclusionCode.CALIBRATION_INVALID,
            candidate.calibration_issue or "标定快照无效",
        )
    return None


def _outcome(
    candidates: tuple[EpisodeCandidate, ...],
    selected: list[EpisodeCandidate],
    excluded: list[ExcludedCandidate],
    warnings: list[str],
) -> SelectionOutcome:
    summary = _summary(excluded)
    return SelectionOutcome(
        candidates=candidates,
        selected=tuple(selected),
        excluded=tuple(excluded),
        warnings=tuple(dict.fromkeys(warnings)),
        exclusion_summary=summary,
    )


def _summary(excluded: list[ExcludedCandidate]) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[UUID]] = {}
    for item in excluded:
        grouped.setdefault(_value(item.code), []).append(item.episode_id)
    return tuple(
        {
            "code": code,
            "count": len(ids),
            "sample_episode_ids": [str(item) for item in ids[:5]],
        }
        for code, ids in sorted(grouped.items())
    )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError(f"{field_name} 必须是 UUID 列表")
    result: list[UUID] = []
    for item in value:
        parsed: UUID
        if isinstance(item, UUID):
            parsed = item
        elif isinstance(item, str):
            try:
                parsed = UUID(item)
            except ValueError as exc:
                raise DomainValidationError(f"{field_name} 必须是 UUID 列表") from exc
        else:
            raise DomainValidationError(f"{field_name} 必须是 UUID 列表")
        if parsed not in result:
            result.append(parsed)
    return tuple(result)


def _str_tuple(value: object, field_name: str, max_length: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError(f"{field_name} 必须是字符串列表")
    result: list[str] = []
    for item in value:
        text = _optional_text(item, field_name, max_length=max_length)
        if text is None:
            raise DomainValidationError(f"{field_name} 必须是字符串列表")
        if text not in result:
            result.append(text)
    return tuple(result)


def _optional_text(value: object, field_name: str, *, max_length: int = 2000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = value.strip()
    if not text:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 超过最大长度 {max_length}")
    return text


def _optional_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 必须是 ISO 8601 时间") from exc
    else:
        raise DomainValidationError(f"{field_name} 必须是 ISO 8601 时间")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区")
    return parsed


__all__ = [
    "Decision",
    "EpisodeCandidate",
    "ExcludedCandidate",
    "SelectionFilters",
    "SelectionOutcome",
    "Split",
    "evaluate_selection",
]
