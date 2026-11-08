"""TrainingDelivery 领域实体。

交付单引用一个已批准 revision、一个 FROZEN DatasetVersion 与一个 SUCCEEDED
ExportJob。创建后引用与产物摘要不可原地修改；驳回后通过新交付/返工记录修正，
旧交付保留。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.datasets.enums import ExportFormat
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.training_enums import TrainingDeliveryStatus
from app.domain.datasets.training_errors import (
    TrainingDeliveryAcceptanceForbiddenError,
    TrainingDeliveryAlreadyAcceptedError,
)
from app.domain.datasets.training_state_machines import (
    allowed_delivery_actions,
    delivery_target,
)
from app.domain.datasets.validation import (
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


def _split_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise DomainValidationError("split_counts 必须是对象")
    if set(value) != {"train", "validation", "test"}:
        raise DomainValidationError("split_counts 必须且只能包含 train/validation/test")
    return {
        name: non_negative_int(value[name], f"split_counts.{name}")
        for name in ("train", "validation", "test")
    }


@dataclass(frozen=True, slots=True)
class TrainingDelivery:
    training_delivery_id: UUID
    project_id: UUID
    training_request_id: UUID
    training_request_revision_id: UUID
    dataset_version_id: UUID
    export_job_id: UUID
    delivery_note_no: str
    recipient_id: UUID
    artifact_manifest_sha256: str | None
    aggregate_sha256: str | None
    item_count: int
    split_counts: dict[str, int]
    status: TrainingDeliveryStatus | str = TrainingDeliveryStatus.DRAFT
    recipient_team: str | None = None
    artifact_manifest_key: str | None = None
    format: str = ExportFormat.LE_ROBOT.value
    format_version: str = "ddp.lerobot/1.0.0"
    quality_report_ref: str | None = None
    delivered_at: datetime | None = None
    accepted_by: UUID | None = None
    accepted_at: datetime | None = None
    acceptance_opinion: str | None = None
    rejection_reason: str | None = None
    rework_ref: UUID | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "training_delivery_id",
            "project_id",
            "training_request_id",
            "training_request_revision_id",
            "dataset_version_id",
            "export_job_id",
            "recipient_id",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "delivery_note_no",
            text_value(self.delivery_note_no, "delivery_note_no", max_length=64),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, TrainingDeliveryStatus, "status")
        )
        object.__setattr__(
            self,
            "recipient_team",
            optional_text(self.recipient_team, "recipient_team", max_length=200),
        )
        object.__setattr__(
            self,
            "artifact_manifest_key",
            optional_text(self.artifact_manifest_key, "artifact_manifest_key", max_length=1024),
        )
        object.__setattr__(
            self,
            "artifact_manifest_sha256",
            optional_sha256(self.artifact_manifest_sha256, "artifact_manifest_sha256"),
        )
        object.__setattr__(
            self,
            "aggregate_sha256",
            optional_sha256(self.aggregate_sha256, "aggregate_sha256"),
        )
        object.__setattr__(
            self,
            "quality_report_ref",
            optional_sha256(self.quality_report_ref, "quality_report_ref"),
        )
        object.__setattr__(self, "item_count", non_negative_int(self.item_count, "item_count"))
        counts = _split_counts(self.split_counts)
        object.__setattr__(self, "split_counts", counts)
        if self.item_count != sum(counts.values()):
            raise DomainValidationError("item_count 必须等于三个划分计数之和")
        if self.format != ExportFormat.LE_ROBOT.value:
            raise DomainValidationError("训练交付仅支持 LE_ROBOT")
        if self.format_version != "ddp.lerobot/1.0.0":
            raise DomainValidationError("训练交付仅支持 ddp.lerobot/1.0.0")
        for name in ("delivered_at", "accepted_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("accepted_by", "rework_ref", "created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "acceptance_opinion",
            optional_text(self.acceptance_opinion, "acceptance_opinion", max_length=2000),
        )
        object.__setattr__(
            self,
            "rejection_reason",
            optional_text(self.rejection_reason, "rejection_reason", max_length=2000),
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        self._assert_invariants()

    def _assert_invariants(self) -> None:
        status = TrainingDeliveryStatus(self.status)
        if status is not TrainingDeliveryStatus.DRAFT:
            if (
                self.artifact_manifest_sha256 is None
                or self.aggregate_sha256 is None
                or self.item_count < 0
            ):
                raise DomainValidationError("非 DRAFT 交付必须记录 Manifest 与汇总 Hash")
        if status in {
            TrainingDeliveryStatus.DELIVERED,
            TrainingDeliveryStatus.ACCEPTED,
            TrainingDeliveryStatus.REJECTED,
            TrainingDeliveryStatus.REWORK,
        }:
            if self.delivered_at is None:
                raise DomainValidationError("已交付记录必须包含 delivered_at")
        if status is TrainingDeliveryStatus.ACCEPTED:
            if (
                self.accepted_by is None
                or self.accepted_at is None
                or self.acceptance_opinion is None
            ):
                raise DomainValidationError("ACCEPTED 交付必须记录验收人、时间与意见")
        if status is TrainingDeliveryStatus.REJECTED and self.rejection_reason is None:
            raise DomainValidationError("REJECTED 交付必须记录驳回原因")

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_delivery_actions(TrainingDeliveryStatus(self.status))

    def ready(self, *, now: datetime, actor_id: UUID) -> TrainingDelivery:
        target = delivery_target(TrainingDeliveryStatus(self.status), "ready")
        return replace(
            self,
            status=target,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def deliver(self, *, now: datetime, actor_id: UUID) -> TrainingDelivery:
        target = delivery_target(TrainingDeliveryStatus(self.status), "deliver")
        return replace(
            self,
            status=target,
            delivered_at=now,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def accept(
        self,
        *,
        now: datetime,
        actor_id: UUID,
        opinion: str,
    ) -> TrainingDelivery:
        if TrainingDeliveryStatus(self.status) is TrainingDeliveryStatus.ACCEPTED:
            raise TrainingDeliveryAlreadyAcceptedError("交付已验收，不能重复修改")
        if self.created_by is not None and actor_id == self.created_by:
            raise TrainingDeliveryAcceptanceForbiddenError("交付创建人不得验收自己创建的交付")
        target = delivery_target(TrainingDeliveryStatus(self.status), "accept")
        return replace(
            self,
            status=target,
            accepted_by=actor_id,
            accepted_at=now,
            acceptance_opinion=text_value(opinion, "acceptance_opinion", max_length=2000),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def reject(
        self,
        *,
        now: datetime,
        actor_id: UUID,
        reason: str,
    ) -> TrainingDelivery:
        target = delivery_target(TrainingDeliveryStatus(self.status), "reject")
        return replace(
            self,
            status=target,
            rejection_reason=text_value(reason, "rejection_reason", max_length=2000),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def rework(
        self,
        *,
        now: datetime,
        actor_id: UUID,
        reason: str,
        rework_ref: UUID | None,
    ) -> TrainingDelivery:
        target = delivery_target(TrainingDeliveryStatus(self.status), "rework")
        return replace(
            self,
            status=target,
            rework_ref=rework_ref,
            rejection_reason=(
                self.rejection_reason
                if self.rejection_reason is not None
                else text_value(reason, "rejection_reason", max_length=2000)
            ),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def resume_ready(self, *, now: datetime, actor_id: UUID) -> TrainingDelivery:
        target = delivery_target(TrainingDeliveryStatus(self.status), "resume-ready")
        return replace(
            self,
            status=target,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def cancel(
        self,
        *,
        now: datetime,
        actor_id: UUID,
        reason: str,
    ) -> TrainingDelivery:
        target = delivery_target(TrainingDeliveryStatus(self.status), "cancel")
        return replace(
            self,
            status=target,
            rejection_reason=text_value(reason, "cancel_reason", max_length=2000),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def assert_reference_unchanged(self, candidate: TrainingDelivery) -> bool:
        """幂等重放时确认引用与产物摘要完全一致。"""
        return (
            self.training_request_id == candidate.training_request_id
            and self.training_request_revision_id == candidate.training_request_revision_id
            and self.dataset_version_id == candidate.dataset_version_id
            and self.export_job_id == candidate.export_job_id
            and self.delivery_note_no == candidate.delivery_note_no
            and self.recipient_id == candidate.recipient_id
            and self.artifact_manifest_sha256 == candidate.artifact_manifest_sha256
            and self.aggregate_sha256 == candidate.aggregate_sha256
            and self.item_count == candidate.item_count
            and self.split_counts == candidate.split_counts
        )


def new_training_delivery(
    *,
    training_delivery_id: UUID,
    project_id: UUID,
    training_request_id: UUID,
    training_request_revision_id: UUID,
    dataset_version_id: UUID,
    export_job_id: UUID,
    delivery_note_no: str,
    recipient_id: UUID,
    recipient_team: str | None,
    artifact_manifest_key: str,
    artifact_manifest_sha256: str,
    aggregate_sha256: str,
    item_count: int,
    split_counts: dict[str, int],
    quality_report_ref: str | None,
    actor_id: UUID,
    now: datetime,
) -> TrainingDelivery:
    return TrainingDelivery(
        training_delivery_id=training_delivery_id,
        project_id=project_id,
        training_request_id=training_request_id,
        training_request_revision_id=training_request_revision_id,
        dataset_version_id=dataset_version_id,
        export_job_id=export_job_id,
        delivery_note_no=delivery_note_no,
        recipient_id=recipient_id,
        recipient_team=recipient_team,
        artifact_manifest_key=text_value(
            artifact_manifest_key, "artifact_manifest_key", max_length=1024
        ),
        artifact_manifest_sha256=ensure_sha(artifact_manifest_sha256, "artifact_manifest_sha256"),
        aggregate_sha256=ensure_sha(aggregate_sha256, "aggregate_sha256"),
        item_count=item_count,
        split_counts=split_counts,
        quality_report_ref=quality_report_ref,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


__all__ = ["TrainingDelivery", "new_training_delivery"]
