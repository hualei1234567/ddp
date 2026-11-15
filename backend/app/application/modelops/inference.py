"""推理服务与推理任务用例。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.modelops.audit import record_change
from app.application.modelops.commands import NewInferenceService, NewInferenceTask
from app.application.modelops.idempotency import IdempotencyGuard, validate_key
from app.application.modelops.ports import (
    AuditRepository,
    DatasetGate,
    IdempotencyRepository,
    InferenceServiceRepository,
    InferenceTaskRepository,
    ModelPlatformGateway,
    ModelVersionRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import (
    HealthStatus,
    InferenceEnvironment,
    InferenceServiceStatus,
    InferenceTaskStatus,
    ModelVersionStatus,
)
from app.domain.modelops.errors import (
    DomainValidationError,
    InferenceServiceNotActiveError,
    InferenceTaskInputInvalidError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.modelops.models import (
    InferenceService,
    InferenceTask,
    new_inference_task,
)
from app.domain.modelops.state_machines import target_status, validate_action_name
from app.domain.modelops.validation import coerce_enum, optional_text, text_value

_SERVICE = "INFERENCE_SERVICE"
_TASK = "INFERENCE_TASK"

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合 = ``domain/modelops/state_machines`` 的 inference-service 动作并集。
INFERENCE_SERVICE_ACTION_EVENTS: dict[str, str] = {
    "deploy": "modelops.inference_service.deploy",
    "activate": "modelops.inference_service.activate",
    "system-deploy-fail": "modelops.inference_service.system-deploy-fail",
    "reset": "modelops.inference_service.reset",
    "stop": "modelops.inference_service.stop",
    "retire": "modelops.inference_service.retire",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InferenceServiceUseCases:
    def __init__(
        self,
        *,
        services: InferenceServiceRepository,
        versions: ModelVersionRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._services = services
        self._versions = versions
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, service_id: UUID) -> InferenceService:
        service = self._services.get(scope, service_id)
        if service is None:
            raise ResourceNotFoundError("推理服务", service_id)
        return service

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InferenceService]:
        return self._services.list(scope, status=status, after_id=after_id, limit=limit)

    def create(
        self,
        scope: ProjectScope,
        command: NewInferenceService,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InferenceService:
        version = self._versions.get(scope, command.model_version_id)
        if version is None or version.project_id != scope.project_id:
            raise ResourceNotFoundError("模型版本", command.model_version_id)
        if ModelVersionStatus(version.status) not in {
            ModelVersionStatus.READY,
            ModelVersionStatus.RELEASED,
        }:
            raise DomainValidationError("模型版本必须处于 READY 或 RELEASED")
        endpoint = text_value(command.endpoint_url, "endpoint_url", max_length=1024)
        if not endpoint.startswith(("http://", "https://")):
            raise DomainValidationError("endpoint_url 必须是 http(s) URL")
        key = validate_key(idempotency_key)
        body = {
            "model_version_id": str(command.model_version_id),
            "environment": command.environment,
            "endpoint_url": endpoint,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        service = InferenceService(
            service_id=uuid4(),
            project_id=scope.project_id,
            model_version_id=command.model_version_id,
            environment=coerce_enum(command.environment, InferenceEnvironment, "environment").value,
            endpoint_url=endpoint,
            resource_ref=optional_text(command.resource_ref, "resource_ref", max_length=512),
            config=dict(command.config or {}),
            status=InferenceServiceStatus.PENDING.value,
            health_status=HealthStatus.UNKNOWN.value,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
        )
        created = self._services.add(scope, service)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_SERVICE,
            resource_id=created.service_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"model_version_id": str(created.model_version_id)},
            event_type="modelops.inference_service.created",
            event_payload={"service_id": str(created.service_id)},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.service_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        service_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InferenceService:
        action_name = validate_action_name(action, family="inference-service")
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        service = self.get(scope, service_id)
        if service.row_version != expected_version:
            raise VersionConflictError("推理服务")
        if action_name not in service.allowed_actions:
            raise InvalidStateTransitionError("推理服务", service.status, action_name)
        target = target_status("inference-service", service.status, action_name)
        updates: dict[str, object] = {
            "status": target,
            "updated_at": now,
        }
        if action_name == "activate":
            updates["deployed_at"] = now
            updates["health_status"] = HealthStatus.UNKNOWN.value
        if action_name == "stop":
            updates["stopped_at"] = now
        if action_name == "retire" and not reason:
            raise DomainValidationError("retire 必须提供原因")
        updated = replace(service, **updates)
        saved = self._services.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_SERVICE,
            resource_id=service_id,
            action=action_name,
            from_status=service.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={},
            event_type=INFERENCE_SERVICE_ACTION_EVENTS[action_name],
            event_payload={"service_id": str(service_id)},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.service_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> InferenceService:
        if not response_ref:
            raise ResourceNotFoundError("推理服务", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("推理服务", response_ref) from exc


class InferenceTaskUseCases:
    def __init__(
        self,
        *,
        tasks: InferenceTaskRepository,
        services: InferenceServiceRepository,
        versions: ModelVersionRepository,
        datasets: DatasetGate,
        gateway: ModelPlatformGateway,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._tasks = tasks
        self._services = services
        self._versions = versions
        self._datasets = datasets
        self._gateway = gateway
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, task_id: UUID) -> InferenceTask:
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("推理任务", task_id)
        return task

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        model_version_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InferenceTask]:
        return self._tasks.list(
            scope,
            status=status,
            model_version_id=model_version_id,
            after_id=after_id,
            limit=limit,
        )

    def create(
        self,
        scope: ProjectScope,
        command: NewInferenceTask,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InferenceTask:
        version = self._versions.get(scope, command.model_version_id)
        if version is None or version.project_id != scope.project_id:
            raise ResourceNotFoundError("模型版本", command.model_version_id)
        if ModelVersionStatus(version.status) not in {
            ModelVersionStatus.READY,
            ModelVersionStatus.RELEASED,
        }:
            raise DomainValidationError("模型版本必须处于 READY 或 RELEASED")
        if command.service_id is not None:
            service = self._services.get(scope, command.service_id)
            if service is None:
                raise ResourceNotFoundError("推理服务", command.service_id)
            if service.status != InferenceServiceStatus.ACTIVE.value:
                raise InferenceServiceNotActiveError("推理服务未激活")
            if service.model_version_id != command.model_version_id:
                raise DomainValidationError("推理服务与模型版本不一致")
        if command.dataset_version_id is None and not command.episode_ids:
            raise InferenceTaskInputInvalidError(
                "推理任务必须提供 dataset_version_id 或 episode_ids"
            )
        if (
            command.dataset_version_id is not None
            and self._datasets.version_state(scope.project_id, command.dataset_version_id) is None
        ):
            raise ResourceNotFoundError("数据集版本", command.dataset_version_id)
        key = validate_key(idempotency_key)
        body = {
            "model_version_id": str(command.model_version_id),
            "service_id": str(command.service_id) if command.service_id else None,
            "dataset_version_id": str(command.dataset_version_id)
            if command.dataset_version_id
            else None,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        task = new_inference_task(
            task_id=uuid4(),
            project_id=scope.project_id,
            model_version_id=command.model_version_id,
            service_id=command.service_id,
            dataset_version_id=command.dataset_version_id,
            episode_ids=command.episode_ids,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._tasks.add(scope, task)
        if command.adapter_id:
            self._gateway.submit_inference(
                adapter_id=command.adapter_id,
                adapter_version="1.0.0",
                task_id=created.task_id,
                model_version_id=created.model_version_id,
                service_id=created.service_id,
                dataset_version_id=created.dataset_version_id,
                episode_ids=created.episode_ids,
                config=dict(command.config or {}),
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_TASK,
            resource_id=created.task_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"model_version_id": str(created.model_version_id)},
            event_type="modelops.inference_task.created",
            event_payload={"task_id": str(created.task_id)},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=202,
            response_ref=str(created.task_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        task_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InferenceTask:
        action_name = validate_action_name(action, family="inference-task")
        if action_name != "cancel":
            raise DomainValidationError("推理任务仅支持 cancel")
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        task = self.get(scope, task_id)
        if task.row_version != expected_version:
            raise VersionConflictError("推理任务")
        if action_name not in task.allowed_actions:
            raise InvalidStateTransitionError("推理任务", task.status, action_name)
        updated = replace(
            task,
            status=InferenceTaskStatus.CANCELLED.value,
            error_code="cancelled_by_user",
            error_summary=reason,
            finished_at=now,
            updated_at=now,
        )
        saved = self._tasks.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_TASK,
            resource_id=task_id,
            action=action_name,
            from_status=task.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={},
            event_type="modelops.inference_task.cancel",
            event_payload={"task_id": str(task_id)},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.task_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def complete_from_callback(
        self,
        *,
        project_id: UUID,
        task_id: UUID,
        result_ref: dict[str, object] | None,
        metrics: dict[str, float] | None,
        log_ref: str | None,
        error_code: str | None,
        error_summary: str | None,
        now: datetime | None = None,
    ) -> InferenceTask:
        now = now or _utcnow()
        lookup = ProjectScope(project_id=project_id, actor_id=project_id)
        task = self._tasks.get(lookup, task_id)
        if task is None:
            raise ResourceNotFoundError("推理任务", task_id)
        scope = ProjectScope(project_id=project_id, actor_id=task.created_by or project_id)
        if task.status in {
            InferenceTaskStatus.SUCCEEDED.value,
            InferenceTaskStatus.CANCELLED.value,
        }:
            return task
        if error_code:
            updated = replace(
                task,
                status=InferenceTaskStatus.FAILED.value,
                error_code=error_code,
                error_summary=error_summary,
                log_ref=log_ref,
                started_at=task.started_at or now,
                finished_at=now,
                updated_at=now,
            )
        else:
            updated = replace(
                task,
                status=InferenceTaskStatus.SUCCEEDED.value,
                result_ref=result_ref,
                metrics=metrics or {},
                log_ref=log_ref,
                started_at=task.started_at or now,
                finished_at=now,
                updated_at=now,
            )
        saved = self._tasks.save(scope, updated, expected_version=task.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_TASK,
            resource_id=task_id,
            action="callback",
            from_status=task.status,
            to_status=saved.status,
            reason=None,
            request_id=None,
            details={},
            event_type="modelops.inference_task.callback",
            event_payload={"task_id": str(task_id)},
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> InferenceTask:
        if not response_ref:
            raise ResourceNotFoundError("推理任务", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("推理任务", response_ref) from exc


__all__ = ["InferenceServiceUseCases", "InferenceTaskUseCases"]
