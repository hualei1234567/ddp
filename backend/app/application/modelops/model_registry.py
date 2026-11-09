"""模型与模型版本用例。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.modelops.audit import record_change
from app.application.modelops.commands import NewModel, NewModelVersion
from app.application.modelops.idempotency import IdempotencyGuard, validate_key
from app.application.modelops.ports import (
    ArtifactInspector,
    AuditRepository,
    DatasetGate,
    IdempotencyRepository,
    ModelRepository,
    ModelVersionRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.modelops.artifacts import ModelArtifact, ModelRuntime
from app.domain.modelops.enums import ModelVersionStatus
from app.domain.modelops.errors import (
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ModelReleaseApprovalForbiddenError,
    ModelVersionInUseError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.modelops.models import Model, ModelVersion, new_model, new_model_version
from app.domain.modelops.state_machines import validate_action_name

_MODEL = "MODEL"
_VERSION = "MODEL_VERSION"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ModelUseCases:
    def __init__(
        self,
        *,
        models: ModelRepository,
        versions: ModelVersionRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._models = models
        self._versions = versions
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, model_id: UUID) -> Model:
        model = self._models.get(scope, model_id)
        if model is None:
            raise ResourceNotFoundError("模型", model_id)
        return model

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        task_type: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Model]:
        return self._models.list(
            scope, status=status, task_type=task_type, after_id=after_id, limit=limit
        )

    def create(
        self,
        scope: ProjectScope,
        command: NewModel,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Model:
        key = validate_key(idempotency_key)
        body = {
            "code": command.code,
            "name": command.name,
            "task_type": command.task_type,
            "framework": command.framework,
            "owner_id": str(command.owner_id),
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        if self._models.get_by_code(scope, command.code.strip()) is not None:
            raise DuplicateResourceError("项目内模型编码已存在")
        model = new_model(
            model_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            task_type=command.task_type,
            framework=command.framework,
            owner_id=command.owner_id,
            description=command.description,
            tags=command.tags,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._models.add(scope, model)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_MODEL,
            resource_id=created.model_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"code": created.code},
            event_type="modelops.model.created",
            event_payload={"model_id": str(created.model_id), "code": created.code},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.model_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update(
        self,
        scope: ProjectScope,
        model_id: UUID,
        patch: dict[str, object],
        *,
        expected_version: int,
        request_id: str | None = None,
    ) -> Model:
        model = self.get(scope, model_id)
        if model.row_version != expected_version:
            raise VersionConflictError("模型")
        updated = replace(
            model,
            name=patch.get("name", model.name) or model.name,
            description=patch.get("description", model.description),
            framework=patch.get("framework", model.framework) or model.framework,
            tags=patch.get("tags", model.tags),
            updated_at=_utcnow(),
            updated_by=scope.actor_id,
        )
        saved = self._models.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_MODEL,
            resource_id=model_id,
            action="update",
            from_status=model.status,
            to_status=saved.status,
            reason=None,
            request_id=request_id,
            details={},
            event_type="modelops.model.updated",
            event_payload={"model_id": str(model_id)},
            occurred_at=_utcnow(),
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        model_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Model:
        action_name = validate_action_name(action, family="model")
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version, "reason": reason}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        model = self.get(scope, model_id)
        if model.row_version != expected_version:
            raise VersionConflictError("模型")
        if action_name not in model.allowed_actions:
            raise InvalidStateTransitionError("模型", model.status, action_name)
        if action_name in {"deprecate", "archive"} and not reason:
            raise DomainValidationError(f"{action_name} 必须提供原因")
        target = _target("model", model.status, action_name)
        updated = replace(
            model,
            status=target,
            updated_at=now,
            updated_by=scope.actor_id,
        )
        saved = self._models.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_MODEL,
            resource_id=model_id,
            action=action_name,
            from_status=model.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={},
            event_type=f"modelops.model.{action_name}",
            event_payload={"model_id": str(model_id), "to_status": saved.status},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.model_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> Model:
        if not response_ref:
            raise ResourceNotFoundError("模型", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("模型", response_ref) from exc


class ModelVersionUseCases:
    def __init__(
        self,
        *,
        models: ModelRepository,
        versions: ModelVersionRepository,
        services,
        datasets: DatasetGate,
        artifacts: ArtifactInspector,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._models = models
        self._versions = versions
        self._services = services
        self._datasets = datasets
        self._artifacts = artifacts
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, model_id: UUID, version_id: UUID) -> ModelVersion:
        version = self._versions.get(scope, version_id)
        if version is None or version.model_id != model_id:
            raise ResourceNotFoundError("模型版本", version_id)
        return version

    def list(
        self,
        scope: ProjectScope,
        model_id: UUID,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ModelVersion]:
        if self._models.get(scope, model_id) is None:
            raise ResourceNotFoundError("模型", model_id)
        return self._versions.list(scope, model_id, status=status, after_id=after_id, limit=limit)

    def create(
        self,
        scope: ProjectScope,
        model_id: UUID,
        command: NewModelVersion,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ModelVersion:
        model = self._models.get(scope, model_id)
        if model is None:
            raise ResourceNotFoundError("模型", model_id)
        key = validate_key(idempotency_key)
        body = {
            "model_id": str(model_id),
            "artifact": command.artifact.to_payload(),
            "runtime": command.runtime.to_payload(),
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, model_id, replay.response_ref)
        for dataset_version_id in command.dataset_version_ids or []:
            state = self._datasets.version_state(scope.project_id, dataset_version_id)
            if state is None:
                raise DomainValidationError("dataset_version_ids 含不存在或跨项目版本")
        version_no = self._versions.next_version_no(scope, model_id)
        version = new_model_version(
            model_version_id=uuid4(),
            model_id=model_id,
            project_id=scope.project_id,
            version_no=version_no,
            artifact=command.artifact,
            runtime=command.runtime,
            training_request_id=command.training_request_id,
            training_delivery_id=command.training_delivery_id,
            external_ref_id=command.external_ref_id,
            dataset_version_ids=command.dataset_version_ids,
            metrics_summary=command.metrics_summary,
            release_notes=command.release_notes,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._versions.add(scope, version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=created.model_version_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"model_id": str(model_id), "version_no": version_no},
            event_type="modelops.model_version.created",
            event_payload={
                "model_version_id": str(created.model_version_id),
                "model_id": str(model_id),
                "version_no": version_no,
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
            response_ref=str(created.model_version_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        model_id: UUID,
        version_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ModelVersion:
        action_name = validate_action_name(action, family="model-version")
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version, "reason": reason}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, model_id, replay.response_ref)
        version = self.get(scope, model_id, version_id)
        if version.row_version != expected_version:
            raise VersionConflictError("模型版本")
        if action_name not in version.allowed_actions:
            raise InvalidStateTransitionError("模型版本", version.status, action_name)
        if action_name == "publish":
            if version.created_by == scope.actor_id:
                raise ModelReleaseApprovalForbiddenError("发布人不得是版本唯一创建人")
        if action_name == "archive":
            if self._services.count_active_for_version(scope, version_id) > 0:
                raise ModelVersionInUseError("存在 ACTIVE 推理服务，版本不可归档")
            if not reason:
                raise DomainValidationError("archive 必须提供原因")
        if action_name == "deprecate" and not reason:
            raise DomainValidationError("deprecate 必须提供原因")
        if action_name == "return-draft" and not reason:
            raise DomainValidationError("return-draft 必须提供原因")

        updated = self._execute(
            version, action_name, now=now, actor_id=scope.actor_id, reason=reason
        )
        saved = self._versions.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=version_id,
            action=action_name,
            from_status=version.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={"model_id": str(model_id)},
            event_type=f"modelops.model_version.{action_name}",
            event_payload={
                "model_version_id": str(version_id),
                "model_id": str(model_id),
                "to_status": saved.status,
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
            response_ref=str(saved.model_version_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _execute(
        self,
        version: ModelVersion,
        action: str,
        *,
        now: datetime,
        actor_id: UUID,
        reason: str | None,
    ) -> ModelVersion:
        if action == "validate":
            self._inspect_artifact(version.artifact)
            return replace(
                version,
                status=ModelVersionStatus.READY.value,
                validated_at=now,
                updated_at=now,
                updated_by=actor_id,
            )
        if action == "publish":
            return replace(
                version,
                status=ModelVersionStatus.RELEASED.value,
                released_by=actor_id,
                released_at=now,
                updated_at=now,
                updated_by=actor_id,
            )
        if action == "deprecate":
            return replace(
                version,
                status=ModelVersionStatus.DEPRECATED.value,
                deprecated_at=now,
                deprecation_reason=reason,
                updated_at=now,
                updated_by=actor_id,
            )
        if action == "return-draft":
            return replace(
                version,
                status=ModelVersionStatus.DRAFT.value,
                deprecation_reason=reason,
                updated_at=now,
                updated_by=actor_id,
            )
        target = _target("model-version", version.status, action)
        return replace(version, status=target, updated_at=now, updated_by=actor_id)

    def _inspect_artifact(self, artifact: ModelArtifact) -> None:
        size = self._artifacts.object_size(artifact.object_key)
        if size is None:
            raise DomainValidationError("模型产物不存在")
        if size != artifact.size_bytes:
            raise DomainValidationError(
                f"模型产物大小与声明不一致：actual={size} declared={artifact.size_bytes}"
            )

    def _replay(
        self, scope: ProjectScope, model_id: UUID, response_ref: str | None
    ) -> ModelVersion:
        if not response_ref:
            raise ResourceNotFoundError("模型版本", response_ref)
        try:
            return self.get(scope, model_id, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("模型版本", response_ref) from exc


def _target(family: str, current: str, action: str) -> str:
    from app.domain.modelops.state_machines import target_status

    return target_status(family, current, action)


__all__ = ["ModelUseCases", "ModelVersionUseCases", "ModelArtifact", "ModelRuntime"]
