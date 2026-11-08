"""TrainingDelivery 用例：创建、就绪校验、交付、验收、驳回、返工与取消。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.export_ports import ExportJobRepository, ExportObjectStorage
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    DatasetRepository,
    DatasetVersionRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasets.training_commands import NewTrainingDelivery
from app.application.datasets.training_ports import (
    TrainingDeliveryRepository,
    TrainingRequestRepository,
    TrainingRequestRevisionRepository,
)
from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import DatasetStatus, DatasetVersionStatus, ExportJobStatus, ValidityStatus
from app.domain.datasets.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ExportChecksumMismatchError,
    ExportIncompleteError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasets.models import DatasetVersion
from app.domain.datasets.training_deliveries import TrainingDelivery, new_training_delivery
from app.domain.datasets.training_enums import RevisionStatus, TrainingRequestStatus
from app.domain.datasets.training_errors import (
    TrainingDeliveryMismatchError,
    TrainingDeliveryNotReadyError,
    TrainingRequestNotApprovedError,
)
from app.domain.datasets.training_state_machines import (
    allowed_delivery_actions,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE = "TRAINING_DELIVERY"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrainingDeliveryUseCases:
    def __init__(
        self,
        *,
        requests: TrainingRequestRepository,
        revisions: TrainingRequestRevisionRepository,
        deliveries: TrainingDeliveryRepository,
        datasets: DatasetRepository,
        versions: DatasetVersionRepository,
        exports: ExportJobRepository,
        storage: ExportObjectStorage,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        chunk_size_bytes: int,
    ) -> None:
        self._requests = requests
        self._revisions = revisions
        self._deliveries = deliveries
        self._datasets = datasets
        self._versions = versions
        self._exports = exports
        self._storage = storage
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        self._chunk_size = chunk_size_bytes

    def get(self, scope: ProjectScope, delivery_id: UUID) -> TrainingDelivery:
        delivery = self._deliveries.get(scope, delivery_id)
        if delivery is None:
            raise ResourceNotFoundError("训练交付", delivery_id)
        return delivery

    def list(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingDelivery]:
        return self._deliveries.list(
            scope,
            request_id=request_id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def create(
        self,
        scope: ProjectScope,
        command: NewTrainingDelivery,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> TrainingDelivery:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=now
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        request = self._requests.get(scope, command.training_request_id)
        if request is None:
            raise ResourceNotFoundError("训练需求", command.training_request_id)
        revision = self._revisions.get_for_request(
            scope,
            request_id=command.training_request_id,
            revision_id=command.training_request_revision_id,
        )
        if revision is None:
            raise TrainingDeliveryMismatchError("交付引用的需求 revision 不属于该需求")
        if revision.status is not RevisionStatus.APPROVED:
            raise TrainingRequestNotApprovedError("只能为已批准的 revision 创建交付")
        if self._deliveries.get_by_note_no(scope, command.delivery_note_no) is not None:
            raise DuplicateResourceError("项目内交付单号重复")
        version = self._versions.get(scope, command.dataset_version_id)
        if version is None:
            raise ResourceNotFoundError("数据集版本", command.dataset_version_id)
        job = self._exports.get(scope, command.export_job_id)
        if job is None:
            raise ResourceNotFoundError("导出任务", command.export_job_id)
        manifest = self._verify_export(scope, version, job)
        item_count = version.item_count
        split_counts = {
            "train": version.train_count,
            "validation": version.validation_count,
            "test": version.test_count,
        }
        if manifest.get("artifact_count") != job.artifact_count:
            raise TrainingDeliveryMismatchError("产物计数与导出任务不一致")
        if request.status not in {
            TrainingRequestStatus.APPROVED,
            TrainingRequestStatus.REVISION_REQUIRED,
            TrainingRequestStatus.IN_DELIVERY,
        }:
            raise InvalidStateTransitionError(
                "训练需求", request.status.value, "start-delivery"
            )
        delivery = new_training_delivery(
            training_delivery_id=uuid4(),
            project_id=scope.project_id,
            training_request_id=command.training_request_id,
            training_request_revision_id=command.training_request_revision_id,
            dataset_version_id=command.dataset_version_id,
            export_job_id=command.export_job_id,
            delivery_note_no=command.delivery_note_no,
            recipient_id=command.recipient_id,
            recipient_team=command.recipient_team,
            artifact_manifest_key=job.artifact_manifest_key or "",
            artifact_manifest_sha256=job.artifact_manifest_sha256 or "",
            aggregate_sha256=job.aggregate_sha256 or "",
            item_count=item_count,
            split_counts=split_counts,
            quality_report_ref=version.quality_report_sha256,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._deliveries.add(scope, delivery)
        if request.status in {
            TrainingRequestStatus.APPROVED,
            TrainingRequestStatus.REVISION_REQUIRED,
        }:
            updated_request = request.start_delivery(now=now, actor_id=scope.actor_id)
            self._requests.save(
                scope, updated_request, expected_version=request.row_version
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=created.training_delivery_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "training_request_revision_id": str(
                    created.training_request_revision_id
                ),
                "dataset_version_id": str(created.dataset_version_id),
                "export_job_id": str(created.export_job_id),
                "delivery_note_no": created.delivery_note_no,
            },
            event_type="training.delivery.created",
            event_payload={
                "training_delivery_id": str(created.training_delivery_id),
                "training_request_id": str(created.training_request_id),
                "dataset_version_id": str(created.dataset_version_id),
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.training_delivery_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        delivery_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        opinion: str | None = None,
        rework_ref: UUID | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> TrainingDelivery:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="delivery")
        if action_name not in {
            "ready",
            "deliver",
            "accept",
            "reject",
            "rework",
            "resume-ready",
            "cancel",
        }:
            raise DomainValidationError("交付动作非法")
        body = {
            "action": action_name,
            "expected_version": expected_version,
            "reason": reason,
            "opinion": opinion,
            "rework_ref": str(rework_ref) if rework_ref else None,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=now
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        delivery = self.get(scope, delivery_id)
        if delivery.row_version != expected_version:
            raise VersionConflictError("训练交付")
        if action_name not in allowed_delivery_actions(delivery.status):
            raise InvalidStateTransitionError(
                "训练交付", delivery.status.value, action_name
            )
        request = self._requests.get(scope, delivery.training_request_id)
        if request is None:
            raise ResourceNotFoundError("训练需求", delivery.training_request_id)

        if action_name == "ready":
            version = self._versions.get(scope, delivery.dataset_version_id)
            if version is None:
                raise ResourceNotFoundError("数据集版本", delivery.dataset_version_id)
            job = self._exports.get(scope, delivery.export_job_id)
            if job is None:
                raise ResourceNotFoundError("导出任务", delivery.export_job_id)
            self._verify_export(scope, version, job)
            updated = delivery.ready(now=now, actor_id=scope.actor_id)
        elif action_name == "deliver":
            updated = delivery.deliver(now=now, actor_id=scope.actor_id)
            request = request.mark_delivered(now=now, actor_id=scope.actor_id)
        elif action_name == "accept":
            if not opinion or not opinion.strip():
                raise DomainValidationError("验收必须填写验收意见")
            updated = delivery.accept(
                now=now, actor_id=scope.actor_id, opinion=opinion
            )
            request = request.accept_delivery(now=now, actor_id=scope.actor_id)
        elif action_name == "reject":
            if not reason or not reason.strip():
                raise DomainValidationError("驳回交付必须填写原因")
            updated = delivery.reject(
                now=now, actor_id=scope.actor_id, reason=reason
            )
            request = request.require_revision(now=now, actor_id=scope.actor_id)
        elif action_name == "rework":
            if not reason or not reason.strip():
                raise DomainValidationError("返工必须填写原因")
            if rework_ref is not None:
                target = self._deliveries.get(scope, rework_ref)
                if (
                    target is None
                    or target.training_request_id != delivery.training_request_id
                    or target.training_delivery_id == delivery.training_delivery_id
                ):
                    raise TrainingDeliveryMismatchError("返工引用必须指向同一需求的新交付")
            updated = delivery.rework(
                now=now,
                actor_id=scope.actor_id,
                reason=reason,
                rework_ref=rework_ref,
            )
        elif action_name == "resume-ready":
            updated = delivery.resume_ready(now=now, actor_id=scope.actor_id)
        else:
            if not reason or not reason.strip():
                raise DomainValidationError("取消交付必须填写原因")
            updated = delivery.cancel(now=now, actor_id=scope.actor_id, reason=reason)

        saved = self._deliveries.save(scope, updated, expected_version=expected_version)
        if action_name in {"deliver", "accept", "reject"}:
            self._requests.save(scope, request, expected_version=request.row_version - 1)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.training_delivery_id,
            action=action_name,
            from_status=delivery.status.value,
            to_status=saved.status.value,
            reason=reason or opinion,
            request_id=request_id,
            details={"rework_ref": str(rework_ref) if rework_ref else None},
            event_type=f"training.delivery.{action_name}",
            event_payload={
                "training_delivery_id": str(saved.training_delivery_id),
                "training_request_id": str(saved.training_request_id),
                "from_status": delivery.status.value,
                "to_status": saved.status.value,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.training_delivery_id),
            now=now,
        )
        self._uow.commit()
        return saved

    # ---- 校验 ----
    def _verify_export(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        job: object,
    ) -> dict[str, object]:
        dataset = self._datasets.get(scope, version.dataset_id)
        if dataset is None:
            raise ResourceNotFoundError("数据集", version.dataset_id)
        if dataset.status is DatasetStatus.ARCHIVED:
            raise TrainingDeliveryNotReadyError("已归档数据集不能交付")
        if version.status is not DatasetVersionStatus.FROZEN:
            raise TrainingDeliveryNotReadyError("只能交付 FROZEN 数据集版本")
        if version.validity_status is not ValidityStatus.VALID:
            raise TrainingDeliveryNotReadyError("数据集版本已失效，不能交付")
        if getattr(job, "dataset_version_id", None) != version.dataset_version_id:
            raise TrainingDeliveryMismatchError("导出任务与数据集版本不匹配")
        if getattr(job, "status", None) is not ExportJobStatus.SUCCEEDED:
            raise TrainingDeliveryNotReadyError("只有 SUCCEEDED 导出任务可以交付")
        manifest_key = getattr(job, "artifact_manifest_key", None)
        expected_sha = getattr(job, "artifact_manifest_sha256", None)
        aggregate_sha = getattr(job, "aggregate_sha256", None)
        if not manifest_key or not expected_sha or not aggregate_sha:
            raise ExportIncompleteError("导出任务缺少产物清单或汇总 Hash")
        data = b"".join(self._storage.stream_object(manifest_key, chunk_size=self._chunk_size))
        if sha256_text(data.decode("utf-8")) != expected_sha:
            raise ExportChecksumMismatchError("产物清单 Hash 校验失败")
        try:
            manifest = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportChecksumMismatchError("产物清单内容非法") from exc
        if not isinstance(manifest, dict):
            raise ExportChecksumMismatchError("产物清单结构非法")
        if manifest.get("aggregate_sha256") != aggregate_sha:
            raise ExportChecksumMismatchError("产物汇总 Hash 与导出任务不一致")
        return manifest

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> TrainingDelivery:
        if not response_ref:
            raise ResourceNotFoundError("训练交付", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("训练交付", response_ref) from exc


__all__ = ["TrainingDeliveryUseCases"]
