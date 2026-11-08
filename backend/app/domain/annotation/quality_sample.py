"""质检抽检聚合与抽样策略。

抽样只接受随机/分层/风险三类策略，并以稳定 Hash 排序保证可复现；
抽检复核人不得是被抽检提交的标注人，结论与策略版本均留痕。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    QualitySampleAction,
    QualitySampleOutcome,
    QualitySampleStatus,
    QualitySampleType,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    QualitySampleSelfReviewForbiddenError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    quality_sample_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    REASON_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    canonical_json,
    coerce_enum,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    uuid_tuple,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class QualityCandidate:
    """抽检候选：来自真实提交，而非黄金集。"""

    submission_id: UUID
    annotator_id: UUID
    stratum: str | None = None
    risk_score: float = 0.0

    def __post_init__(self) -> None:
        required_uuid(self.submission_id, "candidate.submission_id")
        required_uuid(self.annotator_id, "candidate.annotator_id")
        object.__setattr__(
            self,
            "stratum",
            optional_text(self.stratum, "candidate.stratum", max_length=100),
        )
        if (
            not isinstance(self.risk_score, int | float)
            or isinstance(self.risk_score, bool)
            or not 0 <= float(self.risk_score) <= 1
        ):
            raise DomainValidationError("candidate.risk_score 必须在 0 到 1 之间")
        object.__setattr__(self, "risk_score", float(self.risk_score))


def _stable_order(candidate: QualityCandidate, seed: str) -> str:
    payload = {"seed": seed, "submission_id": str(candidate.submission_id)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def select_quality_candidates(
    candidates: list[QualityCandidate] | tuple[QualityCandidate, ...],
    *,
    sample_type: QualitySampleType | str,
    sample_size: int,
    stratum: str | None = None,
    risk_threshold: float | None = None,
    seed: str = "stage3-quality-sample/1",
) -> tuple[QualityCandidate, ...]:
    """按策略确定性选择抽检候选；候选不足显式失败，不静默降级。"""
    kind = coerce_enum(sample_type, QualitySampleType, "sample_type")
    size = positive_int(sample_size, "sample_size")
    if kind not in {
        QualitySampleType.RANDOM,
        QualitySampleType.STRATIFIED,
        QualitySampleType.RISK_BASED,
    }:
        raise DomainValidationError("抽检策略只支持 RANDOM/STRATIFIED/RISK_BASED")
    pool = list(candidates)
    if kind is QualitySampleType.STRATIFIED:
        cleaned = optional_text(stratum, "stratum", max_length=100)
        if cleaned is None:
            raise DomainValidationError("分层抽样必须指定 stratum")
        pool = [item for item in pool if item.stratum == cleaned]
    if kind is QualitySampleType.RISK_BASED:
        threshold = 0.0 if risk_threshold is None else float(risk_threshold)
        if not 0 <= threshold <= 1:
            raise DomainValidationError("risk_threshold 必须在 0 到 1 之间")
        pool = [item for item in pool if item.risk_score >= threshold]
        pool.sort(key=lambda item: (-item.risk_score, _stable_order(item, seed)))
    elif kind is QualitySampleType.STRATIFIED:
        pool.sort(key=lambda item: (item.stratum or "", _stable_order(item, seed)))
    else:
        pool.sort(key=lambda item: _stable_order(item, seed))
    if len(pool) < size:
        raise DomainValidationError("抽检候选不足，不能生成请求的样本量")
    return tuple(pool[:size])


@dataclass(frozen=True, slots=True)
class QualitySample:
    quality_sample_id: UUID
    project_id: UUID
    task_id: UUID
    submission_id: UUID
    annotator_id: UUID
    sampling_policy_version: str
    sample_type: QualitySampleType | str
    sample_reason: str
    status: QualitySampleStatus | str = QualitySampleStatus.PLANNED
    stratum: str | None = None
    item_refs: tuple[UUID, ...] = ()
    reviewer_id: UUID | None = None
    review_id: UUID | None = None
    arbitration_id: UUID | None = None
    due_at: datetime | None = None
    outcome: QualitySampleOutcome | str | None = None
    reason: str | None = None
    closed_at: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "quality_sample_id",
            "project_id",
            "task_id",
            "submission_id",
            "annotator_id",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "sampling_policy_version",
            normalized_text(
                self.sampling_policy_version,
                "sampling_policy_version",
                max_length=VERSION_TEXT_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "sample_type",
            coerce_enum(self.sample_type, QualitySampleType, "sample_type"),
        )
        object.__setattr__(
            self,
            "sample_reason",
            normalized_text(self.sample_reason, "sample_reason", max_length=500),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, QualitySampleStatus, "status"),
        )
        object.__setattr__(
            self,
            "stratum",
            optional_text(self.stratum, "stratum", max_length=100),
        )
        object.__setattr__(self, "item_refs", uuid_tuple(self.item_refs, "item_refs"))
        for name in ("reviewer_id", "review_id", "arbitration_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        if self.outcome is not None:
            object.__setattr__(
                self,
                "outcome",
                coerce_enum(self.outcome, QualitySampleOutcome, "outcome"),
            )
        object.__setattr__(
            self,
            "reason",
            optional_text(self.reason, "reason", max_length=REASON_MAX_LENGTH),
        )
        for name in ("due_at", "closed_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        if not isinstance(self.metadata, Mapping):
            raise DomainValidationError("metadata 必须是对象")
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    @property
    def is_terminal(self) -> bool:
        return self.status in {QualitySampleStatus.CLOSED, QualitySampleStatus.CANCELLED}

    def assert_not_self_review(self) -> None:
        if self.reviewer_id is not None and self.reviewer_id == self.annotator_id:
            raise QualitySampleSelfReviewForbiddenError("抽检复核人不得是标注人")

    def apply_action(
        self,
        action: object,
        *,
        reviewer_id: UUID | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> QualitySample:
        action_name = validate_action_name(action, family="quality_sample")
        target = quality_sample_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("quality_sample", action_name) and cleaned_reason is None:
            raise DomainValidationError("该抽检动作必须填写原因")
        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if action_name == QualitySampleAction.ASSIGN.value:
            chosen = optional_uuid(reviewer_id, "reviewer_id")
            if chosen is None:
                raise DomainValidationError("分配抽检必须指定 reviewer_id")
            if chosen == self.annotator_id:
                raise QualitySampleSelfReviewForbiddenError("抽检复核人不得是标注人")
            changes["reviewer_id"] = chosen
        if action_name == QualitySampleAction.START.value:
            if self.reviewer_id is None:
                raise DomainValidationError("抽检未分配复核人，不能开始")
        if target is QualitySampleStatus.PASSED:
            changes["outcome"] = QualitySampleOutcome.PASS
            changes["reason"] = cleaned_reason
        if target is QualitySampleStatus.FAILED:
            changes["outcome"] = QualitySampleOutcome.FAIL
            changes["reason"] = cleaned_reason
        if target is QualitySampleStatus.ESCALATED:
            changes["outcome"] = QualitySampleOutcome.ESCALATE
            changes["reason"] = cleaned_reason
        if target is QualitySampleStatus.CLOSED:
            changes["closed_at"] = occurred_at
            changes["reason"] = cleaned_reason or self.reason
        if target is QualitySampleStatus.CANCELLED:
            changes["reason"] = cleaned_reason
        return replace(self, **changes)


__all__ = ["QualityCandidate", "QualitySample", "select_quality_candidates"]
