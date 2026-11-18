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
from app.application.datasets.version_facts import VersionFactDirectory
from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import (
    DatasetStatus,
    DatasetVersionStatus,
    ExportJobStatus,
)
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
from app.domain.datasets.requirement_match import (
    MatchPolicy,
    MatchReport,
    VersionFacts,
    evaluate_requirement_match,
)
from app.domain.datasets.training_deliveries import TrainingDelivery, new_training_delivery
from app.domain.datasets.training_enums import RevisionStatus, TrainingRequestStatus
from app.domain.datasets.training_errors import (
    TrainingDeliveryMismatchError,
    TrainingDeliveryNotReadyError,
    TrainingDeliveryRequirementUnmetError,
    TrainingRequestNotApprovedError,
)
from app.domain.datasets.training_state_machines import (
    allowed_delivery_actions,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE = "TRAINING_DELIVERY"

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合与 ``apply_action`` 的显式白名单一致。
TRAINING_DELIVERY_ACTION_EVENTS: dict[str, str] = {
    "ready": "training.delivery.ready",
    "deliver": "training.delivery.deliver",
    "accept": "training.delivery.accept",
    "reject": "training.delivery.reject",
    "rework": "training.delivery.rework",
    "resume-ready": "training.delivery.resume-ready",
    "cancel": "training.delivery.cancel",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _enum_text(value: object) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


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
        fact_directory: VersionFactDirectory,
        match_policy: MatchPolicy,
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
        self._facts = fact_directory
        self._match_policy = match_policy

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
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
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
            raise InvalidStateTransitionError("训练需求", request.status.value, "start-delivery")
        match_report = self._evaluate_match(scope, version, job, revision.snapshot)
        self._assert_match_allows_delivery(match_report)
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
            requirement_match=match_report.to_payload(),
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._deliveries.add(scope, delivery)
        if request.status in {
            TrainingRequestStatus.APPROVED,
            TrainingRequestStatus.REVISION_REQUIRED,
        }:
            updated_request = request.start_delivery(now=now, actor_id=scope.actor_id)
            self._requests.save(scope, updated_request, expected_version=request.row_version)
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
                "training_request_revision_id": str(created.training_request_revision_id),
                "dataset_version_id": str(created.dataset_version_id),
                "export_job_id": str(created.export_job_id),
                "delivery_note_no": created.delivery_note_no,
                "requirement_match_status": match_report.status.value,
                "requirement_match_blocked": match_report.blocked,
                "requirement_match_failed_dimensions": [
                    item.dimension.value for item in match_report.failed
                ],
                "requirement_match_unverifiable_dimensions": [
                    item.dimension.value for item in match_report.unverifiable
                ],
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
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        delivery = self.get(scope, delivery_id)
        if delivery.row_version != expected_version:
            raise VersionConflictError("训练交付")
        if action_name not in allowed_delivery_actions(delivery.status):
            raise InvalidStateTransitionError("训练交付", delivery.status.value, action_name)
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
            self._assert_recorded_match_still_valid(scope, delivery, version, job)
            updated = delivery.ready(now=now, actor_id=scope.actor_id)
        elif action_name == "deliver":
            updated = delivery.deliver(now=now, actor_id=scope.actor_id)
            request = request.mark_delivered(now=now, actor_id=scope.actor_id)
        elif action_name == "accept":
            if not opinion or not opinion.strip():
                raise DomainValidationError("验收必须填写验收意见")
            updated = delivery.accept(now=now, actor_id=scope.actor_id, opinion=opinion)
            request = request.accept_delivery(now=now, actor_id=scope.actor_id)
        elif action_name == "reject":
            if not reason or not reason.strip():
                raise DomainValidationError("驳回交付必须填写原因")
            updated = delivery.reject(now=now, actor_id=scope.actor_id, reason=reason)
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
            event_type=TRAINING_DELIVERY_ACTION_EVENTS[action_name],
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
    def _evaluate_match(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        job: object,
        snapshot: dict[str, object],
    ) -> MatchReport:
        """按需求快照与版本事实生成核对报告（确定性、无副作用）。"""
        projection = self._facts.project(scope, version.dataset_version_id)
        facts = VersionFacts(
            selected_count=version.selected_count,
            total_duration_ns=version.total_duration_ns,
            split_counts={
                "train": version.train_count,
                "validation": version.validation_count,
                "test": version.test_count,
            },
            scene_labels=projection.scene_labels,
            annotation_labels=projection.annotation_labels,
            annotation_schema_versions=frozenset(
                (str(item.get("schema_id")), int(item.get("schema_version", 0)))
                for item in version.annotation_schema_versions
                if isinstance(item, dict) and isinstance(item.get("schema_version"), int)
            ),
            # 版本/产物尚未声明数据模态：显式传 None（UNVERIFIABLE），
            # 不构造一个"看起来有值"的空集合。
            quality_metrics=self._quality_metrics(scope, version),
            format=_enum_text(getattr(job, "format", None)),
            format_version=getattr(job, "format_version", None),
        )
        return evaluate_requirement_match(snapshot, facts, self._match_policy)

    def _quality_metrics(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
    ) -> dict[str, object] | None:
        """质量事实不可得时返回 ``None``，让阈值维度判 UNVERIFIABLE。

        当前版本上没有质量报告仓储可注入，因此只用版本自身已持久化的汇总
        字段拼一份最小事实；拿不到的键保持缺省，由领域层逐键判 UNVERIFIABLE。
        """
        metrics: dict[str, object] = {"selected_count": version.selected_count}
        if version.total_duration_ns is not None:
            metrics["total_duration_ns"] = version.total_duration_ns
        return metrics

    def _assert_match_allows_delivery(self, report: MatchReport) -> None:
        """按配置决定阻断还是仅告警；阻断时抛带逐维度详情的领域异常。"""
        if not report.blocked:
            return
        raise TrainingDeliveryRequirementUnmetError(
            "数据集版本未满足已批准需求",
            details={
                "status": report.status.value,
                "on_failed": report.policy.on_failed.value,
                "on_unverifiable": report.policy.on_unverifiable.value,
                "blocking_dimensions": [
                    {
                        "dimension": item.dimension.value,
                        "status": item.status.value,
                        "expected": item.to_payload()["expected"],
                        "actual": item.to_payload()["actual"],
                        "detail": item.detail,
                    }
                    for item in report.blocking_dimensions
                ],
            },
        )

    def _assert_recorded_match_still_valid(
        self,
        scope: ProjectScope,
        delivery: TrainingDelivery,
        version: DatasetVersion,
        job: object,
    ) -> None:
        """``ready`` 时按当前事实复核一次，并确认与创建时落库的报告一致。

        版本或导出产物可能在创建交付后被归档/替换；用创建时冻结的快照重算，
        若结论变化说明事实已漂移，直接拒绝而不是沿用旧结论。
        """
        recorded = delivery.requirement_match
        if recorded is None:
            # 该交付建立于核对能力上线之前：没有留证就无法断言"通过"。
            raise TrainingDeliveryRequirementUnmetError(
                "该交付没有需求核对记录，不能进入 READY",
                details={"delivery_id": str(delivery.training_delivery_id), "recorded": False},
            )
        revision = self._revisions.get(scope, delivery.training_request_revision_id)
        if revision is None:
            raise ResourceNotFoundError("需求 revision", delivery.training_request_revision_id)
        current = self._evaluate_match(scope, version, job, revision.snapshot)
        if current.to_payload() != recorded:
            raise TrainingDeliveryRequirementUnmetError(
                "需求核对结论与创建交付时不一致，版本事实已漂移",
                details={
                    "recorded_status": recorded.get("status"),
                    "current_status": current.status.value,
                },
            )
        self._assert_match_allows_delivery(current)

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
        blocked = version.validity_block_reason
        if blocked is not None:
            raise TrainingDeliveryNotReadyError(
                f"{blocked}，不能交付",
                details={"validity_status": version.validity_status.value},
            )
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
