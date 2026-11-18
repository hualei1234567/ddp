"""外部训练引用与 Adapter 回调用例。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    IdempotencyRepository,
    OutboxEvent,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasets.training_commands import NewExternalTrainingReference
from app.application.datasets.training_ports import (
    AdapterRunStatus,
    CallbackRecord,
    CallbackRepository,
    ExternalReferenceRepository,
    TrainingAdapterGateway,
    TrainingDeliveryRepository,
)
from app.domain.datasets.adapter_callback import ensure_capabilities
from app.domain.datasets.canonical import plain, sha256_value
from app.domain.datasets.errors import (
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasets.training_deliveries import TrainingDelivery
from app.domain.datasets.training_enums import (
    AdapterCapability,
    ExecutionMode,
    ExternalTrainingStatus,
    TrainingDeliveryStatus,
)
from app.domain.datasets.training_errors import (
    TrainingAdapterUnavailableError,
    TrainingCallbackReplayedError,
    TrainingExternalRunConflictError,
)
from app.domain.datasets.training_references import (
    ExternalTrainingReference,
    new_external_reference,
    resolve_execution_mode,
)
from app.domain.datasets.training_state_machines import (
    allowed_external_actions,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_EXTERNAL_RESOURCE = "EXTERNAL_TRAINING_REFERENCE"

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合与 ``apply_action`` 的显式白名单一致；``system-*`` 状态由回调落库，不在此产生事件。
EXTERNAL_REFERENCE_ACTION_EVENTS: dict[str, str] = {
    "sync": "training.external_reference.sync",
    "cancel": "training.external_reference.cancel",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExternalTrainingReferenceUseCases:
    def __init__(
        self,
        *,
        deliveries: TrainingDeliveryRepository,
        references: ExternalReferenceRepository,
        gateway: TrainingAdapterGateway,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._deliveries = deliveries
        self._references = references
        self._gateway = gateway
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, reference_id: UUID) -> ExternalTrainingReference:
        reference = self._references.get(scope, reference_id)
        if reference is None:
            raise ResourceNotFoundError("外部训练引用", reference_id)
        return reference

    def list_for_delivery(
        self,
        scope: ProjectScope,
        delivery_id: UUID,
        *,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ExternalTrainingReference]:
        delivery = self._deliveries.get(scope, delivery_id)
        if delivery is None:
            raise ResourceNotFoundError("训练交付", delivery_id)
        return self._references.list_for_delivery(
            scope, delivery_id=delivery_id, after_id=after_id, limit=limit
        )

    def latest_execution_modes(
        self,
        scope: ProjectScope,
        delivery_ids: list[UUID],
    ) -> dict[UUID, ExecutionMode]:
        """交付级执行方式投影：最近一次外部训练引用；无引用时由调用方填 UNKNOWN。"""
        if not delivery_ids:
            return {}
        return self._references.latest_execution_modes(scope, delivery_ids=tuple(delivery_ids))

    def create(
        self,
        scope: ProjectScope,
        delivery_id: UUID,
        command: NewExternalTrainingReference,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ExternalTrainingReference:
        delivery = self._deliveries.get(scope, delivery_id)
        if delivery is None:
            raise ResourceNotFoundError("训练交付", delivery_id)
        if delivery.status not in {
            TrainingDeliveryStatus.DELIVERED,
            TrainingDeliveryStatus.ACCEPTED,
        }:
            raise InvalidStateTransitionError("训练交付", delivery.status.value, "submit-training")
        key = validate_key(idempotency_key or command.correlation_id)
        if key is None:
            raise DomainValidationError("外部训练引用必须提供幂等键或 correlation_id")
        # 执行方式先定死再走幂等：同一幂等键携带不同 execution_mode 不是同一次提交。
        execution_mode = resolve_execution_mode(
            configured=self._gateway.execution_mode(command.adapter_id),
            requested=command.execution_mode,
        )
        existing = self._references.get_by_idempotency_key(
            scope, adapter_id=command.adapter_id, idempotency_key=key
        )
        if existing is not None:
            candidate = self._candidate(scope, delivery, command, key, execution_mode)
            if not existing.matches_idempotent_request(candidate):
                raise TrainingExternalRunConflictError("同一幂等键对应不同的外部训练请求")
            return existing
        capabilities = self._gateway.capabilities(command.adapter_id)
        ensure_capabilities(capabilities, AdapterCapability.SUBMIT, adapter_id=command.adapter_id)
        if command.model_ref is not None:
            ensure_capabilities(
                capabilities,
                AdapterCapability.MODEL_REFERENCE,
                adapter_id=command.adapter_id,
            )
        if command.evaluation_ref is not None:
            ensure_capabilities(
                capabilities,
                AdapterCapability.EVALUATION_REFERENCE,
                adapter_id=command.adapter_id,
            )
        run = self._gateway.submit(
            adapter_id=command.adapter_id,
            adapter_version=command.adapter_version,
            correlation_id=command.correlation_id,
            delivery_id=delivery.training_delivery_id,
            dataset_version_id=delivery.dataset_version_id,
            config=dict(command.config),
        )
        conflict = self._references.get_by_run(
            adapter_id=command.adapter_id,
            external_run_id=run.external_run_id,
        )
        if conflict is not None:
            raise TrainingExternalRunConflictError("外部训练 run 已登记")
        now = _utcnow()
        reference = new_external_reference(
            external_ref_id=uuid4(),
            project_id=scope.project_id,
            adapter_id=command.adapter_id,
            adapter_version=command.adapter_version,
            training_delivery_id=delivery.training_delivery_id,
            training_request_revision_id=delivery.training_request_revision_id,
            dataset_version_id=delivery.dataset_version_id,
            external_run_id=run.external_run_id,
            external_run_url=run.external_run_url,
            correlation_id=command.correlation_id,
            idempotency_key=key,
            execution_mode=execution_mode,
            model_ref=command.model_ref or run.model_ref,
            evaluation_ref=command.evaluation_ref or run.evaluation_ref,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._references.add(scope, reference)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_EXTERNAL_RESOURCE,
            resource_id=created.external_ref_id,
            action="create",
            from_status=None,
            to_status=created.external_status.value,
            reason=None,
            request_id=request_id,
            details={
                "adapter_id": created.adapter_id,
                "adapter_version": created.adapter_version,
                "external_run_id": created.external_run_id,
                "correlation_id": created.correlation_id,
                "execution_mode": created.execution_mode.value,
            },
            event_type="training.external_reference.created",
            event_payload={
                "external_ref_id": str(created.external_ref_id),
                "training_delivery_id": str(created.training_delivery_id),
                "adapter_id": created.adapter_id,
                "external_run_id": created.external_run_id,
                "external_status": created.external_status.value,
                "execution_mode": created.execution_mode.value,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        reference_id: UUID,
        action: str,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ExternalTrainingReference:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="external")
        if action_name not in {"sync", "cancel"}:
            raise DomainValidationError("外部引用仅支持 sync/cancel")
        body = {"action": action_name, "expected_version": expected_version}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        reference = self.get(scope, reference_id)
        if reference.row_version != expected_version:
            raise VersionConflictError("外部训练引用")
        if action_name not in allowed_external_actions(reference.effective_status):
            raise InvalidStateTransitionError(
                "外部训练引用", reference.effective_status.value, action_name
            )
        capabilities = self._gateway.capabilities(reference.adapter_id)
        ensure_capabilities(
            capabilities,
            AdapterCapability.STATUS if action_name == "sync" else AdapterCapability.CANCEL,
            adapter_id=reference.adapter_id,
        )
        try:
            if action_name == "sync":
                result = self._gateway.get_status(
                    adapter_id=reference.adapter_id,
                    external_run_id=reference.external_run_id,
                    correlation_id=reference.correlation_id,
                )
            else:
                result = self._gateway.cancel(
                    adapter_id=reference.adapter_id,
                    external_run_id=reference.external_run_id,
                    correlation_id=reference.correlation_id,
                )
        except TrainingAdapterUnavailableError as exc:
            failed = reference.mark_unavailable(
                now=now,
                actor_id=scope.actor_id,
                error_code=exc.code,
                error_summary=str(exc),
            )
            self._references.save(scope, failed, expected_version=expected_version)
            self._uow.commit()
            raise
        updated = self._apply_run_status(reference, result, now=now, actor_id=scope.actor_id)
        saved = self._references.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_EXTERNAL_RESOURCE,
            resource_id=saved.external_ref_id,
            action=action_name,
            from_status=reference.effective_status.value,
            to_status=saved.effective_status.value,
            reason=None,
            request_id=request_id,
            details={
                "adapter_id": saved.adapter_id,
                "external_run_id": saved.external_run_id,
                "status_stale": saved.status_stale,
            },
            event_type=EXTERNAL_REFERENCE_ACTION_EVENTS[action_name],
            event_payload={
                "external_ref_id": str(saved.external_ref_id),
                "from_status": reference.effective_status.value,
                "to_status": saved.effective_status.value,
                "status_stale": saved.status_stale,
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
            response_ref=str(saved.external_ref_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _apply_run_status(
        self,
        reference: ExternalTrainingReference,
        result: AdapterRunStatus,
        *,
        now: datetime,
        actor_id: UUID,
    ) -> ExternalTrainingReference:
        status = ExternalTrainingStatus(result.status)
        stale = result.status_stale or status in {
            ExternalTrainingStatus.UNKNOWN,
            ExternalTrainingStatus.UNAVAILABLE,
        }
        error_code = result.error_code
        error_summary = result.error_summary
        if status is ExternalTrainingStatus.FAILED and error_code is None:
            error_code = "adapter_reported_failure"
            error_summary = error_summary or "外部训练平台报告失败"
        if status is ExternalTrainingStatus.UNAVAILABLE and error_code is None:
            error_code = "training_adapter_unavailable"
            error_summary = error_summary or "外部训练平台不可达"
        if status is ExternalTrainingStatus.UNKNOWN and error_code is None:
            error_code = "adapter_status_unknown"
            error_summary = error_summary or "外部训练状态未知"
        return reference.apply_status(
            status,
            now=now,
            actor_id=actor_id,
            model_ref=result.model_ref,
            evaluation_ref=result.evaluation_ref,
            error_code=error_code,
            error_summary=error_summary,
            stale=stale,
            run_url=result.external_run_url,
        )

    def _candidate(
        self,
        scope: ProjectScope,
        delivery: TrainingDelivery,
        command: NewExternalTrainingReference,
        key: str,
        execution_mode: ExecutionMode,
    ) -> ExternalTrainingReference:
        now = _utcnow()
        return new_external_reference(
            external_ref_id=uuid4(),
            project_id=scope.project_id,
            adapter_id=command.adapter_id,
            adapter_version=command.adapter_version,
            training_delivery_id=delivery.training_delivery_id,
            training_request_revision_id=delivery.training_request_revision_id,
            dataset_version_id=delivery.dataset_version_id,
            external_run_id="candidate",
            external_run_url=None,
            correlation_id=command.correlation_id,
            idempotency_key=key,
            execution_mode=execution_mode,
            model_ref=command.model_ref,
            evaluation_ref=command.evaluation_ref,
            actor_id=scope.actor_id,
            now=now,
        )

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> ExternalTrainingReference:
        if not response_ref:
            raise ResourceNotFoundError("外部训练引用", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("外部训练引用", response_ref) from exc


def _extract_artifacts(payload: dict[str, object]) -> dict[str, object] | None:
    """从回调体里取产物元数据，供模型版本登记使用。

    约定（local-adapter 已实现）：``payload.payload`` 是适配器自定的详情对象，
    其中的 ``model`` / ``metadata`` 两项描述产物对象（``object_key`` / ``sha256`` /
    ``size_bytes``）。也接受适配器直接把 ``artifacts`` 放在详情里。

    取不到就返回 ``None``——"对方没报产物"和"报了空对象"必须区分，后者会让
    前端误以为有产物可登记。
    """
    detail = payload.get("payload")
    if not isinstance(detail, dict):
        return None
    declared = detail.get("artifacts")
    if isinstance(declared, dict):
        return dict(declared)
    picked = {
        name: dict(value)
        for name in ("model", "metadata", "metrics")
        if isinstance((value := detail.get(name)), dict)
    }
    return picked or None


class AdapterCallbackUseCases:
    """处理 Adapter 回调；验签由 API 层在读取原始 body 后调用。"""

    def __init__(
        self,
        *,
        references: ExternalReferenceRepository,
        callbacks: CallbackRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
        nonce_ttl_seconds: int,
    ) -> None:
        self._references = references
        self._callbacks = callbacks
        self._outbox = outbox
        self._uow = uow
        self._nonce_ttl = nonce_ttl_seconds

    def handle(
        self,
        *,
        adapter_id: str,
        nonce: str,
        event_id: str,
        adapter_version: str,
        external_run_id: str,
        external_status: str,
        model_ref: dict[str, object] | None,
        evaluation_ref: dict[str, object] | None,
        error_code: str | None,
        error_summary: str | None,
        payload: dict[str, object],
        now: datetime | None = None,
    ) -> str:
        now = now or _utcnow()
        payload_sha = sha256_value(plain(payload))
        existing_event = self._callbacks.get_by_event(adapter_id, event_id)
        if existing_event is not None:
            if existing_event.payload_sha256 != payload_sha:
                raise TrainingCallbackReplayedError("同一 event_id 对应不同回调内容")
            return "IDEMPOTENT"
        if self._callbacks.get_by_nonce(adapter_id, nonce) is not None:
            raise TrainingCallbackReplayedError("nonce 在时间窗内已被使用")
        try:
            self._callbacks.add(
                CallbackRecord(
                    callback_id=uuid4(),
                    adapter_id=adapter_id,
                    event_id=event_id,
                    nonce=nonce,
                    payload_sha256=payload_sha,
                    received_at=now,
                )
            )
        except DuplicateResourceError as exc:
            existing_event = self._callbacks.get_by_event(adapter_id, event_id)
            if existing_event is not None and existing_event.payload_sha256 == payload_sha:
                return "IDEMPOTENT"
            raise TrainingCallbackReplayedError("回调重放") from exc

        reference = self._references.get_by_run(
            adapter_id=adapter_id,
            external_run_id=external_run_id,
        )
        if reference is None:
            raise ResourceNotFoundError("外部训练引用", external_run_id)
        if reference.adapter_version != adapter_version:
            raise DomainValidationError("回调 adapter_version 与登记引用不一致")
        try:
            status = ExternalTrainingStatus(external_status)
        except ValueError as exc:
            raise DomainValidationError("external_status 取值非法") from exc
        if reference.effective_status in {
            ExternalTrainingStatus.SUCCEEDED,
            ExternalTrainingStatus.FAILED,
            ExternalTrainingStatus.CANCELLED,
        }:
            if reference.effective_status is status:
                self._callbacks.prune_before(
                    received_before=now - timedelta(seconds=self._nonce_ttl)
                )
                self._uow.commit()
                return "IDEMPOTENT"
            raise TrainingExternalRunConflictError("已终态的外部训练引用不能回退或改写")
        result = AdapterRunStatus(
            status=status,
            status_stale=status
            in {ExternalTrainingStatus.UNKNOWN, ExternalTrainingStatus.UNAVAILABLE},
            external_run_url=reference.external_run_url,
            model_ref=model_ref,
            evaluation_ref=evaluation_ref,
            error_code=error_code
            or ("adapter_reported_failure" if status is ExternalTrainingStatus.FAILED else None),
            error_summary=error_summary
            or ("外部训练平台报告失败" if status is ExternalTrainingStatus.FAILED else None),
        )
        updated = reference.apply_status(
            result.status,
            now=now,
            actor_id=reference.created_by,
            model_ref=result.model_ref,
            evaluation_ref=result.evaluation_ref,
            artifacts=_extract_artifacts(payload),
            error_code=result.error_code,
            error_summary=result.error_summary,
            stale=result.status_stale,
        )
        scope = ProjectScope(reference.project_id, actor_id=reference.created_by)
        self._references.save(
            scope,
            reference=updated,
            expected_version=reference.row_version,
        )
        self._outbox.enqueue(
            scope,
            OutboxEvent(
                event_id=uuid4(),
                project_id=reference.project_id,
                aggregate_type=_EXTERNAL_RESOURCE,
                aggregate_id=reference.external_ref_id,
                event_type="training.external_reference.callback",
                payload={
                    "external_ref_id": str(reference.external_ref_id),
                    "adapter_id": adapter_id,
                    "event_id": event_id,
                    "from_status": reference.effective_status.value,
                    "to_status": updated.effective_status.value,
                },
                occurred_at=now,
            ),
        )
        self._callbacks.prune_before(received_before=now - timedelta(seconds=self._nonce_ttl))
        self._uow.commit()
        return "UPDATED"


__all__ = ["AdapterCallbackUseCases", "ExternalTrainingReferenceUseCases"]
