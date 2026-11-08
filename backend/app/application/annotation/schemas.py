"""标签 Schema 用例：创建、版本绑定、审批/发布/废弃/撤销与审计幂等。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationSchema
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationSchemaRepository,
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import AnnotationSchemaAction, AnnotationSchemaStatus
from app.domain.annotation.errors import (
    DomainValidationError,
    ForbiddenOperationError,
    ResourceNotFoundError,
    SchemaImmutableError,
    VersionConflictError,
)
from app.domain.annotation.schema import AnnotationSchema, LabelDefinition
from app.domain.annotation.state_machines import (
    allowed_schema_actions,
    assert_schema_action,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_PATCHABLE = frozenset({"name", "description", "annotation_levels", "labels", "compatibility"})
_RESOURCE_TYPE = "ANNOTATION_SCHEMA"


class AnnotationSchemaUseCases:
    def __init__(
        self,
        schemas: AnnotationSchemaRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._schemas = schemas
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, schema_id: UUID) -> AnnotationSchema:
        schema = self._schemas.get(scope, schema_id)
        if schema is None:
            raise ResourceNotFoundError("标注 Schema", schema_id)
        return schema

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationSchema]:
        return self._schemas.list(scope, status=status, after_id=after_id, limit=limit)

    def allowed_actions(self, scope: ProjectScope, schema_id: UUID) -> frozenset[str]:
        return allowed_schema_actions(self.get(scope, schema_id).status)

    def create(
        self,
        scope: ProjectScope,
        command: NewAnnotationSchema,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSchema:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        lineage_id = command.schema_lineage_id
        if command.supersedes_id is not None:
            previous = self.get(scope, command.supersedes_id)
            if previous.schema_lineage_id != lineage_id and lineage_id is not None:
                raise DomainValidationError("新版本必须沿用同一 schema_lineage_id")
            lineage_id = previous.schema_lineage_id
            if command.schema_version <= previous.schema_version:
                raise DomainValidationError("新版本 schema_version 必须大于被替代版本")
        lineage_id = lineage_id or uuid4()
        labels = tuple(
            (
                item if isinstance(item, LabelDefinition) else LabelDefinition(**item)  # type: ignore[arg-type]
            )
            for item in command.labels
        )
        schema = AnnotationSchema(
            schema_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            description=command.description,
            schema_lineage_id=lineage_id,
            schema_version=command.schema_version,
            annotation_levels=tuple(command.annotation_levels),
            labels=labels,
            compatibility=command.compatibility,
            supersedes_id=command.supersedes_id,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._schemas.add(scope, schema)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.schema_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "schema_version": created.schema_version,
                "schema_sha256": created.schema_sha256,
            },
            event_type="annotation.schema.created",
            event_payload={
                "schema_id": str(created.schema_id),
                "schema_lineage_id": str(created.schema_lineage_id),
                "schema_version": created.schema_version,
                "schema_sha256": created.schema_sha256,
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
            response_ref=str(created.schema_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update(
        self,
        scope: ProjectScope,
        schema_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> AnnotationSchema:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        unknown = set(changes) - _PATCHABLE
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        current = self.get(scope, schema_id)
        if current.version != expected_version:
            raise VersionConflictError("标注 Schema")
        if current.status is not AnnotationSchemaStatus.DRAFT:
            raise SchemaImmutableError("仅 DRAFT Schema 允许修改定义")
        if "labels" in changes:
            changes = {
                **changes,
                "labels": tuple(
                    (
                        item if isinstance(item, LabelDefinition) else LabelDefinition(**item)  # type: ignore[arg-type]
                    )
                    for item in changes["labels"]  # type: ignore[union-attr]
                ),
            }
        now = _utcnow()
        updated = replace(
            current,
            schema_sha256=None,
            **dict(changes),
            updated_at=now,
            updated_by=scope.actor_id,
        )
        saved = self._schemas.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.schema_id,
            action="update",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes), "schema_sha256": saved.schema_sha256},
            event_type="annotation.schema.updated",
            event_payload={
                "schema_id": str(saved.schema_id),
                "schema_sha256": saved.schema_sha256,
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        schema_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSchema:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        action_name = validate_action_name(action, family="schema")
        if action_name not in allowed_schema_actions(self.get(scope, schema_id).status):
            raise DomainValidationError("当前状态不允许该动作")
        current = self.get(scope, schema_id)
        if current.version != expected_version:
            raise VersionConflictError("标注 Schema")
        assert_schema_action(current.status, action_name)
        if (
            action_name == AnnotationSchemaAction.PUBLISH.value
            and current.created_by == scope.actor_id
        ):
            raise ForbiddenOperationError("Schema 创建人不得批准自己的 Schema")

        updated = replace(
            current.apply_action(action_name, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._schemas.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.schema_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={"schema_sha256": saved.schema_sha256},
            event_type="annotation.schema.status_changed",
            event_payload={
                "schema_id": str(saved.schema_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
                "schema_sha256": saved.schema_sha256,
                "version": saved.version,
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
            response_ref=str(saved.schema_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> AnnotationSchema:
        if not response_ref:
            raise ResourceNotFoundError("标注 Schema", response_ref)
        try:
            schema_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("标注 Schema", response_ref) from exc
        return self.get(scope, schema_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)
