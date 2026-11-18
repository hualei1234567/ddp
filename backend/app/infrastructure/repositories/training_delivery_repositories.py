"""TrainingDelivery / ExternalTrainingReference / 回调防重放 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.application.datasets.training_ports import CallbackRecord
from app.domain.datasets.training_deliveries import TrainingDelivery
from app.domain.datasets.training_enums import (
    ExecutionMode,
    ExternalTrainingStatus,
    TrainingDeliveryStatus,
)
from app.domain.datasets.training_references import ExternalTrainingReference
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.training import (
    ExternalTrainingReferenceModel,
    TrainingAdapterCallbackModel,
    TrainingDeliveryModel,
)
from app.infrastructure.repositories._dataset_sql import (
    as_utc,
    bounded_limit,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


def _now() -> datetime:
    return datetime.now(UTC)


class SqlTrainingDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, delivery: TrainingDelivery) -> TrainingDelivery:
        scope.ensure(delivery.project_id)
        ensure_initial_version(delivery.row_version)
        now = _now()
        row = TrainingDeliveryModel(
            id=delivery.training_delivery_id,
            project_id=delivery.project_id,
            training_request_id=delivery.training_request_id,
            training_request_revision_id=delivery.training_request_revision_id,
            dataset_version_id=delivery.dataset_version_id,
            export_job_id=delivery.export_job_id,
            delivery_note_no=delivery.delivery_note_no,
            recipient_id=delivery.recipient_id,
            recipient_team=delivery.recipient_team,
            artifact_manifest_key=delivery.artifact_manifest_key,
            artifact_manifest_sha256=delivery.artifact_manifest_sha256,
            aggregate_sha256=delivery.aggregate_sha256,
            item_count=delivery.item_count,
            split_counts=dict(delivery.split_counts),
            format=delivery.format,
            format_version=delivery.format_version,
            quality_report_ref=delivery.quality_report_ref,
            requirement_match=(
                dict(delivery.requirement_match) if delivery.requirement_match is not None else None
            ),
            status=enum_value(delivery.status),
            delivered_at=delivery.delivered_at,
            accepted_by=delivery.accepted_by,
            accepted_at=delivery.accepted_at,
            acceptance_opinion=delivery.acceptance_opinion,
            rejection_reason=delivery.rejection_reason,
            rework_ref=delivery.rework_ref,
            version=1,
            created_at=delivery.created_at or now,
            updated_at=delivery.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "项目内交付单号重复")
        return replace(delivery, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(self, scope: ProjectScope, delivery_id: UUID) -> TrainingDelivery | None:
        row = self._session.scalar(
            select(TrainingDeliveryModel).where(
                TrainingDeliveryModel.id == delivery_id,
                TrainingDeliveryModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_note_no(self, scope: ProjectScope, note_no: str) -> TrainingDelivery | None:
        row = self._session.scalar(
            select(TrainingDeliveryModel).where(
                TrainingDeliveryModel.project_id == scope.project_id,
                TrainingDeliveryModel.delivery_note_no == note_no,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingDelivery]:
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(TrainingDeliveryModel, scope)
        if request_id is not None:
            statement = statement.where(TrainingDeliveryModel.training_request_id == request_id)
        if status is not None:
            statement = statement.where(TrainingDeliveryModel.status == status)
        if after_id is not None:
            statement = statement.where(TrainingDeliveryModel.id > after_id)
        statement = statement.order_by(TrainingDeliveryModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        delivery: TrainingDelivery,
        *,
        expected_version: int,
    ) -> TrainingDelivery:
        scope.ensure(delivery.project_id)
        ensure_expected_version(delivery.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(TrainingDeliveryModel)
            .where(
                TrainingDeliveryModel.id == delivery.training_delivery_id,
                TrainingDeliveryModel.project_id == scope.project_id,
                TrainingDeliveryModel.version == expected_version,
            )
            .values(
                status=enum_value(delivery.status),
                delivered_at=delivery.delivered_at,
                accepted_by=delivery.accepted_by,
                accepted_at=delivery.accepted_at,
                acceptance_opinion=delivery.acceptance_opinion,
                rejection_reason=delivery.rejection_reason,
                rework_ref=delivery.rework_ref,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                TrainingDeliveryModel,
                scope,
                delivery.training_delivery_id,
                "训练交付",
                expected_version,
            )
        return replace(
            delivery,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: TrainingDeliveryModel) -> TrainingDelivery:
        return TrainingDelivery(
            training_delivery_id=row.id,
            project_id=row.project_id,
            training_request_id=row.training_request_id,
            training_request_revision_id=row.training_request_revision_id,
            dataset_version_id=row.dataset_version_id,
            export_job_id=row.export_job_id,
            delivery_note_no=row.delivery_note_no,
            recipient_id=row.recipient_id,
            recipient_team=row.recipient_team,
            artifact_manifest_key=row.artifact_manifest_key,
            artifact_manifest_sha256=row.artifact_manifest_sha256,
            aggregate_sha256=row.aggregate_sha256,
            item_count=row.item_count,
            split_counts=dict(row.split_counts or {}),
            format=row.format,
            format_version=row.format_version,
            quality_report_ref=row.quality_report_ref,
            requirement_match=(
                dict(row.requirement_match) if row.requirement_match is not None else None
            ),
            status=TrainingDeliveryStatus(row.status),
            delivered_at=as_utc(row.delivered_at),
            accepted_by=row.accepted_by,
            accepted_at=as_utc(row.accepted_at),
            acceptance_opinion=row.acceptance_opinion,
            rejection_reason=row.rejection_reason,
            rework_ref=row.rework_ref,
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlExternalReferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        reference: ExternalTrainingReference,
    ) -> ExternalTrainingReference:
        scope.ensure(reference.project_id)
        ensure_initial_version(reference.row_version)
        now = _now()
        row = ExternalTrainingReferenceModel(
            id=reference.external_ref_id,
            project_id=reference.project_id,
            adapter_id=reference.adapter_id,
            adapter_version=reference.adapter_version,
            training_delivery_id=reference.training_delivery_id,
            training_request_revision_id=reference.training_request_revision_id,
            dataset_version_id=reference.dataset_version_id,
            external_run_id=reference.external_run_id,
            external_run_url=reference.external_run_url,
            external_status=enum_value(reference.external_status),
            execution_mode=enum_value(reference.execution_mode),
            status_stale=reference.status_stale,
            submitted_at=reference.submitted_at,
            last_synced_at=reference.last_synced_at,
            model_ref=dict(reference.model_ref) if reference.model_ref else None,
            evaluation_ref=(dict(reference.evaluation_ref) if reference.evaluation_ref else None),
            artifacts=dict(reference.artifacts) if reference.artifacts else None,
            config_summary=(
                dict(reference.config_summary) if reference.config_summary is not None else None
            ),
            code_version=reference.code_version,
            model_version=reference.model_version,
            correlation_id=reference.correlation_id,
            idempotency_key=reference.idempotency_key,
            error_code=reference.error_code,
            error_summary=reference.error_summary,
            version=1,
            created_at=reference.created_at or now,
            updated_at=reference.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "外部训练引用已存在")
        return replace(reference, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(
        self,
        scope: ProjectScope,
        reference_id: UUID,
    ) -> ExternalTrainingReference | None:
        row = self._session.scalar(
            select(ExternalTrainingReferenceModel).where(
                ExternalTrainingReferenceModel.id == reference_id,
                ExternalTrainingReferenceModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_delivery(
        self,
        scope: ProjectScope,
        *,
        delivery_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ExternalTrainingReference]:
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(ExternalTrainingReferenceModel, scope).where(
            ExternalTrainingReferenceModel.training_delivery_id == delivery_id
        )
        if after_id is not None:
            statement = statement.where(ExternalTrainingReferenceModel.id > after_id)
        statement = statement.order_by(ExternalTrainingReferenceModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def get_by_idempotency_key(
        self,
        scope: ProjectScope,
        *,
        adapter_id: str,
        idempotency_key: str,
    ) -> ExternalTrainingReference | None:
        row = self._session.scalar(
            select(ExternalTrainingReferenceModel).where(
                ExternalTrainingReferenceModel.project_id == scope.project_id,
                ExternalTrainingReferenceModel.adapter_id == adapter_id,
                ExternalTrainingReferenceModel.idempotency_key == idempotency_key,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_run(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
    ) -> ExternalTrainingReference | None:
        row = self._session.scalar(
            select(ExternalTrainingReferenceModel).where(
                ExternalTrainingReferenceModel.adapter_id == adapter_id,
                ExternalTrainingReferenceModel.external_run_id == external_run_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def save(
        self,
        scope: ProjectScope,
        reference: ExternalTrainingReference,
        *,
        expected_version: int,
    ) -> ExternalTrainingReference:
        scope.ensure(reference.project_id)
        ensure_expected_version(reference.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(ExternalTrainingReferenceModel)
            .where(
                ExternalTrainingReferenceModel.id == reference.external_ref_id,
                ExternalTrainingReferenceModel.project_id == scope.project_id,
                ExternalTrainingReferenceModel.version == expected_version,
            )
            .values(
                external_status=enum_value(reference.external_status),
                status_stale=reference.status_stale,
                external_run_url=reference.external_run_url,
                last_synced_at=reference.last_synced_at,
                model_ref=dict(reference.model_ref) if reference.model_ref else None,
                evaluation_ref=(
                    dict(reference.evaluation_ref) if reference.evaluation_ref else None
                ),
                artifacts=dict(reference.artifacts) if reference.artifacts else None,
                error_code=reference.error_code,
                error_summary=reference.error_summary,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                ExternalTrainingReferenceModel,
                scope,
                reference.external_ref_id,
                "外部训练引用",
                expected_version,
            )
        return replace(
            reference,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def latest_execution_modes(
        self,
        scope: ProjectScope,
        *,
        delivery_ids: tuple[UUID, ...],
    ) -> dict[UUID, ExecutionMode]:
        if not delivery_ids:
            return {}
        # 按 created_at + id 倒序，每个交付取第一条即"最近一次引用"。引用数量
        # 以人工提交为界，量级很小；不为它引入窗口函数，保持 SQL 可读。
        statement = (
            scoped_select(ExternalTrainingReferenceModel, scope)
            .where(ExternalTrainingReferenceModel.training_delivery_id.in_(delivery_ids))
            .order_by(
                ExternalTrainingReferenceModel.training_delivery_id,
                ExternalTrainingReferenceModel.created_at.desc(),
                ExternalTrainingReferenceModel.id.desc(),
            )
        )
        modes: dict[UUID, ExecutionMode] = {}
        for row in self._session.scalars(statement):
            modes.setdefault(row.training_delivery_id, ExecutionMode(row.execution_mode))
        return modes

    def _from_row(self, row: ExternalTrainingReferenceModel) -> ExternalTrainingReference:
        return ExternalTrainingReference(
            external_ref_id=row.id,
            project_id=row.project_id,
            adapter_id=row.adapter_id,
            adapter_version=row.adapter_version,
            training_delivery_id=row.training_delivery_id,
            training_request_revision_id=row.training_request_revision_id,
            dataset_version_id=row.dataset_version_id,
            external_run_id=row.external_run_id,
            external_run_url=row.external_run_url,
            external_status=ExternalTrainingStatus(row.external_status),
            execution_mode=ExecutionMode(row.execution_mode),
            status_stale=row.status_stale,
            submitted_at=as_utc(row.submitted_at) or _now(),
            last_synced_at=as_utc(row.last_synced_at) or _now(),
            model_ref=dict(row.model_ref) if row.model_ref else None,
            evaluation_ref=dict(row.evaluation_ref) if row.evaluation_ref else None,
            artifacts=dict(row.artifacts) if row.artifacts else None,
            config_summary=dict(row.config_summary) if row.config_summary is not None else None,
            code_version=row.code_version,
            model_version=row.model_version,
            correlation_id=row.correlation_id,
            idempotency_key=row.idempotency_key,
            error_code=row.error_code,
            error_summary=row.error_summary,
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlTrainingCallbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_event(self, adapter_id: str, event_id: str) -> CallbackRecord | None:
        row = self._session.scalar(
            select(TrainingAdapterCallbackModel).where(
                TrainingAdapterCallbackModel.adapter_id == adapter_id,
                TrainingAdapterCallbackModel.event_id == event_id,
            )
        )
        return self._to_record(row) if row is not None else None

    def get_by_nonce(self, adapter_id: str, nonce: str) -> CallbackRecord | None:
        row = self._session.scalar(
            select(TrainingAdapterCallbackModel).where(
                TrainingAdapterCallbackModel.adapter_id == adapter_id,
                TrainingAdapterCallbackModel.nonce == nonce,
            )
        )
        return self._to_record(row) if row is not None else None

    def add(self, record: CallbackRecord) -> CallbackRecord:
        self._session.add(
            TrainingAdapterCallbackModel(
                id=record.callback_id,
                adapter_id=record.adapter_id,
                event_id=record.event_id,
                nonce=record.nonce,
                payload_sha256=record.payload_sha256,
                received_at=record.received_at,
            )
        )
        flush_or_duplicate(self._session, "回调事件或 nonce 重复")
        return record

    def prune_before(self, *, received_before: datetime) -> int:
        result = self._session.execute(
            delete(TrainingAdapterCallbackModel).where(
                TrainingAdapterCallbackModel.received_at < received_before
            )
        )
        return int(result.rowcount or 0)

    def _to_record(self, row: TrainingAdapterCallbackModel) -> CallbackRecord:
        return CallbackRecord(
            callback_id=row.id,
            adapter_id=row.adapter_id,
            event_id=row.event_id,
            nonce=row.nonce,
            payload_sha256=row.payload_sha256,
            received_at=as_utc(row.received_at) or _now(),
        )
