"""采集会话用例：创建、列表、详情、具名状态动作、审计与幂等。

边界（ADR-0037）：会话只记录"这段执行真的开始了/暂停了/结束了"，
不推进任务审核流、不修改任务状态、不写素材版本。与任务/批次的关联只做
**同项目存在性**校验，跨项目一律 404。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.commands import NewCaptureSession
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
    CaptureSessionRepository,
    CollectionBatchRepository,
    CollectionTaskRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.locations.ports import LocationDirectory
from app.domain.collection.enums import (
    PUBLIC_CAPTURE_SESSION_ACTIONS,
    CaptureSessionStatus,
)
from app.domain.collection.errors import (
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.collection.models import CaptureSession
from app.domain.collection.state_machines import (
    assert_session_action,
    validate_session_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "CAPTURE_SESSION"
# 事件类型写成模块级字面量，``scripts/check_outbox_events.py`` 才能静态提取。
SESSION_CREATED_EVENT = "collection.capture_session.created"
SESSION_ACTION_EVENTS: Mapping[str, str] = MappingProxyType(
    {
        "start": "collection.capture_session.started",
        "pause": "collection.capture_session.paused",
        "resume": "collection.capture_session.resumed",
        "complete": "collection.capture_session.completed",
        "abort": "collection.capture_session.aborted",
    }
)
_ACTION_FIELDS = frozenset({"reason"})


class CaptureSessionUseCases:
    def __init__(
        self,
        sessions: CaptureSessionRepository,
        tasks: CollectionTaskRepository,
        batches: CollectionBatchRepository,
        directory: AssignmentDirectory,
        locations: LocationDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._batches = batches
        self._directory = directory
        self._locations = locations
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def list_sessions(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[CaptureSession]:
        bounded_limit = normalize_limit(limit)
        items = self._sessions.list(
            scope,
            status=_session_status(status),
            task_id=task_id,
            batch_id=batch_id,
            after_id=decode_cursor(cursor),
            limit=bounded_limit + 1,
        )
        return build_page(items, limit=bounded_limit, id_of=lambda item: item.session_id)

    def get_session(self, scope: ProjectScope, session_id: UUID) -> CaptureSession:
        session = self._sessions.get(scope, session_id)
        if session is None:
            raise ResourceNotFoundError("采集会话", session_id)
        return session

    def create_session(
        self,
        scope: ProjectScope,
        command: NewCaptureSession,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CaptureSession:
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

        task_id, batch_id = self._resolve_relations(
            scope,
            task_id=command.task_id,
            batch_id=command.batch_id,
        )
        self._validate_assignment(
            scope,
            operator_id=command.operator_id,
            robot_id=command.robot_id,
            location_id=command.location_id,
        )

        session = CaptureSession(
            session_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            task_id=task_id,
            batch_id=batch_id,
            operator_id=command.operator_id,
            robot_id=command.robot_id,
            location_id=command.location_id,
            status=CaptureSessionStatus.SCHEDULED,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._sessions.add(scope, session)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.session_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "task_id": str(task_id) if task_id is not None else None,
                "batch_id": str(batch_id) if batch_id is not None else None,
            },
            event_type=SESSION_CREATED_EVENT,
            event_payload={
                "session_id": str(created.session_id),
                "code": created.code,
                "task_id": str(task_id) if task_id is not None else None,
                "batch_id": str(batch_id) if batch_id is not None else None,
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
            response_ref=str(created.session_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        session_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CaptureSession:
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

        unknown = set(payload or {}) - _ACTION_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许的动作字段")
        if action not in PUBLIC_CAPTURE_SESSION_ACTIONS:
            raise DomainValidationError("未知状态动作")
        action_name = validate_session_action_name(action)

        current = self.get_session(scope, session_id)
        if current.version != expected_version:
            raise VersionConflictError("采集会话")
        # 先校验状态机，再进入写路径；非法迁移不能被后续错误掩盖。
        assert_session_action(current.status, action_name)

        updated = current.apply_action(action_name, payload, now=now)
        updated = replace(updated, updated_by=scope.actor_id)
        saved = self._sessions.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.session_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=_reason(payload),
            request_id=request_id,
            details={
                "task_id": str(saved.task_id) if saved.task_id is not None else None,
                "batch_id": str(saved.batch_id) if saved.batch_id is not None else None,
            },
            event_type=SESSION_ACTION_EVENTS[action_name],
            event_payload={
                "session_id": str(saved.session_id),
                "task_id": str(saved.task_id) if saved.task_id is not None else None,
                "batch_id": str(saved.batch_id) if saved.batch_id is not None else None,
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
            response_ref=str(saved.session_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _resolve_relations(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID | None,
        batch_id: UUID | None,
    ) -> tuple[UUID | None, UUID | None]:
        """校验任务/批次同项目存在，并保证批次确实属于该任务。

        只给批次时自动继承其任务：会话的最小可用粒度是"某任务下的一次执行"，
        让客户端重复提交 task_id 只会制造两者不一致的机会。
        """
        if batch_id is not None:
            batch = self._batches.get(scope, batch_id)
            if batch is None:
                raise ResourceNotFoundError("采集批次", batch_id)
            if task_id is None:
                task_id = batch.task_id
            elif batch.task_id != task_id:
                raise DomainValidationError("批次不属于指定任务")
        if task_id is not None and self._tasks.get(scope, task_id) is None:
            raise ResourceNotFoundError("采集任务", task_id)
        return task_id, batch_id

    def _validate_assignment(
        self,
        scope: ProjectScope,
        *,
        operator_id: UUID | None,
        robot_id: UUID | None,
        location_id: UUID | None,
    ) -> None:
        if operator_id is not None and not self._directory.user_is_member(scope, operator_id):
            raise ResourceNotFoundError("项目成员", operator_id)
        if robot_id is not None:
            status = self._directory.robot_status(scope, robot_id)
            if status is None:
                raise ResourceNotFoundError("机器人", robot_id)
            if status != "ACTIVE":
                raise DomainValidationError("机器人当前不可用于采集")
        if location_id is not None and not self._locations.location_exists(scope, location_id):
            raise ResourceNotFoundError("采集场地", location_id)

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> CaptureSession:
        if not response_ref:
            raise ResourceNotFoundError("采集会话", response_ref)
        try:
            session_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("采集会话", response_ref) from exc
        return self.get_session(scope, session_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _session_status(value: str | None) -> CaptureSessionStatus | None:
    if value is None:
        return None
    try:
        return CaptureSessionStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _reason(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("reason")
    return value if isinstance(value, str) else None
