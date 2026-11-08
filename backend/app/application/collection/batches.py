"""采集批次用例：批次拆分、修改、具名动作与任务兼容校验。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.commands import NewCollectionBatch
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.collection.ports import (
    AssignmentDirectory,
    AuditRepository,
    CollectionBatchRepository,
    CollectionTaskRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.collection.enums import (
    PUBLIC_BATCH_ACTIONS,
    CollectionBatchStatus,
    CollectionTaskStatus,
)
from app.domain.collection.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceImmutableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.collection.models import CollectionBatch
from app.domain.collection.state_machines import validate_action_name
from app.domain.identity.models import ProjectScope

_BATCH_PATCHABLE_FIELDS = frozenset(
    {
        "expected_count",
        "assigned_to",
        "scheduled_start",
        "scheduled_end",
    }
)
_BATCH_ACTION_FIELDS = frozenset({"reason", "assigned_to"})
_RESOURCE_TYPE = "COLLECTION_BATCH"


class CollectionBatchUseCases:
    def __init__(
        self,
        tasks: CollectionTaskRepository,
        batches: CollectionBatchRepository,
        directory: AssignmentDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._tasks = tasks
        self._batches = batches
        self._directory = directory
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def list_batches(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[CollectionBatch]:
        self._get_task(scope, task_id)
        bounded_limit = normalize_limit(limit)
        items = self._batches.list(
            scope,
            task_id=task_id,
            status=_batch_status(status),
            after_id=decode_cursor(cursor),
            limit=bounded_limit + 1,
        )
        return build_page(items, limit=bounded_limit, id_of=lambda item: item.batch_id)

    def get_batch(self, scope: ProjectScope, batch_id: UUID) -> CollectionBatch:
        batch = self._batches.get(scope, batch_id)
        if batch is None:
            raise ResourceNotFoundError("采集批次", batch_id)
        return batch

    def create_batch(
        self,
        scope: ProjectScope,
        task_id: UUID,
        command: NewCollectionBatch,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CollectionBatch:
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

        task = self._get_task(scope, task_id)
        sequence_no = command.sequence_no or self._batches.next_sequence_no(scope, task_id)
        code = command.code or f"B-{sequence_no:04d}"
        if task.target_episode_count > 0:
            total = self._batches.sum_expected_count(scope, task_id) + command.expected_count
            if total > task.target_episode_count:
                raise DomainValidationError("批次预计数量之和不得超过任务目标")
        if command.assigned_to is not None and not self._directory.user_is_member(
            scope, command.assigned_to
        ):
            raise ResourceNotFoundError("项目成员", command.assigned_to)

        batch = CollectionBatch(
            batch_id=uuid4(),
            project_id=scope.project_id,
            task_id=task_id,
            code=code,
            sequence_no=sequence_no,
            expected_count=command.expected_count,
            assigned_to=command.assigned_to,
            scheduled_start=command.scheduled_start,
            scheduled_end=command.scheduled_end,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._batches.add(scope, batch)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.batch_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={"code": created.code, "task_id": str(task_id)},
            event_type="collection.batch.created",
            event_payload={
                "batch_id": str(created.batch_id),
                "task_id": str(task_id),
                "code": created.code,
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
            response_ref=str(created.batch_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_batch(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> CollectionBatch:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        unknown = set(changes) - _BATCH_PATCHABLE_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        current = self.get_batch(scope, batch_id)
        if current.version != expected_version:
            raise VersionConflictError("采集批次")
        if current.status is not CollectionBatchStatus.PLANNED:
            raise ResourceImmutableError("仅 PLANNED 批次允许修改核心字段")
        if "assigned_to" in changes and changes["assigned_to"] is not None:
            assigned_to = changes["assigned_to"]
            if not isinstance(assigned_to, UUID) or not self._directory.user_is_member(
                scope, assigned_to
            ):
                raise ResourceNotFoundError("项目成员", assigned_to)
        if "expected_count" in changes:
            task = self._get_task(scope, current.task_id)
            new_count = changes["expected_count"]
            if not isinstance(new_count, int) or isinstance(new_count, bool) or new_count < 0:
                raise DomainValidationError("expected_count 必须是非负整数")
            if task.target_episode_count > 0:
                total = (
                    self._batches.sum_expected_count(scope, current.task_id)
                    - current.expected_count
                    + new_count
                )
                if total > task.target_episode_count:
                    raise DomainValidationError("批次预计数量之和不得超过任务目标")

        now = _utcnow()
        updated = replace(
            current,
            **dict(changes),
            updated_at=now,
            updated_by=scope.actor_id,
        )
        saved = self._batches.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.batch_id,
            action="update",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes)},
            event_type="collection.batch.updated",
            event_payload={
                "batch_id": str(saved.batch_id),
                "fields": sorted(changes),
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CollectionBatch:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            **dict(payload or {}),
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

        unknown = set(payload or {}) - _BATCH_ACTION_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许的动作字段")
        if action not in PUBLIC_BATCH_ACTIONS:
            raise DomainValidationError("未知状态动作")
        action_name = validate_action_name(action, batch=True)

        current = self.get_batch(scope, batch_id)
        if current.version != expected_version:
            raise VersionConflictError("采集批次")
        task = self._get_task(scope, current.task_id)
        _ensure_task_compatible(current, action_name, task.status)
        if "assigned_to" in (payload or {}) and payload["assigned_to"] is not None:
            assigned_to = payload["assigned_to"]
            if not isinstance(assigned_to, UUID) or not self._directory.user_is_member(
                scope, assigned_to
            ):
                raise ResourceNotFoundError("项目成员", assigned_to)

        updated = current.apply_action(action_name, payload, now=now)
        updated = replace(updated, updated_by=scope.actor_id)
        saved = self._batches.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.batch_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=_reason(payload),
            request_id=request_id,
            details={"task_id": str(current.task_id)},
            event_type="collection.batch.status_changed",
            event_payload={
                "batch_id": str(saved.batch_id),
                "task_id": str(current.task_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
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
            response_ref=str(saved.batch_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _get_task(self, scope: ProjectScope, task_id: UUID):
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("采集任务", task_id)
        return task

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> CollectionBatch:
        if not response_ref:
            raise ResourceNotFoundError("采集批次", response_ref)
        try:
            batch_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("采集批次", response_ref) from exc
        return self.get_batch(scope, batch_id)


def _ensure_task_compatible(
    batch: CollectionBatch,
    action: str,
    task_status: CollectionTaskStatus,
) -> None:
    allowed: set[CollectionTaskStatus] | None = None
    if action == "ready":
        allowed = {
            CollectionTaskStatus.READY,
            CollectionTaskStatus.ASSIGNED,
            CollectionTaskStatus.IN_PROGRESS,
        }
    elif action == "start":
        allowed = {CollectionTaskStatus.IN_PROGRESS}
    elif action == "submit":
        allowed = {
            CollectionTaskStatus.IN_PROGRESS,
            CollectionTaskStatus.SUBMITTED,
            CollectionTaskStatus.DATA_CHECKING,
            CollectionTaskStatus.REJECTED,
            CollectionTaskStatus.REWORK,
        }
    elif action == "accept":
        allowed = {
            CollectionTaskStatus.DATA_CHECKING,
            CollectionTaskStatus.ACCEPTED,
            CollectionTaskStatus.COMPLETED,
        }
    if allowed is not None and task_status not in allowed:
        raise InvalidStateTransitionError(
            "采集批次",
            batch.status.value,
            f"{action}（任务状态 {task_status.value} 不允许）",
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _batch_status(value: str | None) -> CollectionBatchStatus | None:
    if value is None:
        return None
    try:
        return CollectionBatchStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _reason(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("reason")
    return value if isinstance(value, str) else None
