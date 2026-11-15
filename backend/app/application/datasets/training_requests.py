"""TrainingRequest / Revision 用例：创建、版本化变更、提交、审批与需求动作。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasets.training_commands import (
    NewTrainingRequest,
    NewTrainingRequestRevision,
)
from app.application.datasets.training_ports import (
    TrainingRequestRepository,
    TrainingRequestRevisionRepository,
)
from app.domain.datasets.errors import (
    ConflictError,
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasets.training_enums import RevisionStatus
from app.domain.datasets.training_errors import TrainingRequestRevisionConflictError
from app.domain.datasets.training_requests import (
    TrainingRequest,
    TrainingRequestRevision,
    new_revision,
    new_training_request,
)
from app.domain.datasets.training_state_machines import (
    action_requires_reason,
    allowed_request_actions,
    allowed_revision_actions,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE = "TRAINING_REQUEST"
_REVISION_RESOURCE = "TRAINING_REQUEST_REVISION"

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合与各自 ``apply_action`` 的显式白名单一致；``system-*`` 动作不在此产生事件。
TRAINING_REVISION_ACTION_EVENTS: dict[str, str] = {
    "submit": "training.request.revision_submit",
    "approve": "training.request.revision_approve",
    "reject": "training.request.revision_reject",
}
TRAINING_REQUEST_ACTION_EVENTS: dict[str, str] = {
    "block": "training.request.block",
    "unblock": "training.request.unblock",
    "cancel": "training.request.cancel",
    "close": "training.request.close",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrainingRequestUseCases:
    def __init__(
        self,
        requests: TrainingRequestRepository,
        revisions: TrainingRequestRevisionRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._requests = requests
        self._revisions = revisions
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    # ---- 读取 ----
    def get(self, scope: ProjectScope, request_id: UUID) -> TrainingRequest:
        request = self._requests.get(scope, request_id)
        if request is None:
            raise ResourceNotFoundError("训练需求", request_id)
        return request

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        priority: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingRequest]:
        return self._requests.list(
            scope,
            status=status,
            priority=priority,
            after_id=after_id,
            limit=limit,
        )

    def list_revisions(
        self,
        scope: ProjectScope,
        request_id: UUID,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingRequestRevision]:
        self.get(scope, request_id)
        return self._revisions.list(
            scope,
            request_id=request_id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def get_revision(
        self,
        scope: ProjectScope,
        request_id: UUID,
        revision_id: UUID,
    ) -> TrainingRequestRevision:
        revision = self._revisions.get_for_request(
            scope,
            request_id=request_id,
            revision_id=revision_id,
        )
        if revision is None:
            raise ResourceNotFoundError("需求修订", revision_id)
        return revision

    # ---- 创建 ----
    def create(
        self,
        scope: ProjectScope,
        command: NewTrainingRequest,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> TrainingRequest:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        if self._requests.get_by_code(scope, command.code) is not None:
            raise DuplicateResourceError("项目内训练需求编码重复")
        request, revision = new_training_request(
            training_request_id=uuid4(),
            revision_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            title=command.title,
            owner_id=command.owner_id,
            snapshot=command.snapshot,
            actor_id=scope.actor_id,
            now=now,
        )
        if request.priority.value != command.priority:
            raise DomainValidationError("priority 必须与 snapshot.priority 一致")
        created = self._requests.add(scope, request)
        self._revisions.add(scope, revision)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=created.training_request_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "revision_no": revision.revision_no,
                "revision_sha256": revision.revision_sha256,
            },
            event_type="training.request.created",
            event_payload={
                "training_request_id": str(created.training_request_id),
                "revision_id": str(revision.revision_id),
                "revision_no": revision.revision_no,
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
            response_ref=str(created.training_request_id),
            now=now,
        )
        self._uow.commit()
        return created

    def create_revision(
        self,
        scope: ProjectScope,
        request_id: UUID,
        command: NewTrainingRequestRevision,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id_text: str | None = None,
    ) -> TrainingRequestRevision:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._revision_replay(scope, request_id, replay.response_ref)
        request = self.get(scope, request_id)
        if request.current_revision_id is None:
            raise ConflictError("训练需求缺少当前 revision")
        previous = self.get_revision(scope, request_id, request.current_revision_id)
        if previous.status is RevisionStatus.SUPERSEDED:
            raise TrainingRequestRevisionConflictError("当前 revision 已被取代")
        revision = new_revision(
            revision_id=uuid4(),
            request=request,
            previous=previous,
            snapshot=command.snapshot,
            change_reason=command.change_reason,
            actor_id=scope.actor_id,
            now=now,
        )
        superseded = previous.supersede()
        self._revisions.save(
            scope,
            superseded,
            expected_version=previous.row_version,
        )
        created = self._revisions.add(scope, revision)
        updated_request = request.with_revision(created, now=now, actor_id=scope.actor_id)
        saved_request = self._requests.save(
            scope, updated_request, expected_version=request.row_version
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved_request.training_request_id,
            action="new-revision",
            from_status=request.status.value,
            to_status=saved_request.status.value,
            reason=command.change_reason,
            request_id=request_id_text,
            details={
                "revision_no": created.revision_no,
                "revision_sha256": created.revision_sha256,
                "supersedes_revision_id": str(previous.revision_id),
            },
            event_type="training.request.revision_created",
            event_payload={
                "training_request_id": str(request_id),
                "revision_id": str(created.revision_id),
                "revision_no": created.revision_no,
                "supersedes_revision_id": str(previous.revision_id),
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
            response_ref=str(created.revision_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_revision(
        self,
        scope: ProjectScope,
        request_id: UUID,
        revision_id: UUID,
        *,
        expected_version: int,
        changes: dict[str, object],
        request_id_text: str | None = None,
    ) -> TrainingRequestRevision:
        request = self.get(scope, request_id)
        revision = self.get_revision(scope, request_id, revision_id)
        if request.current_revision_id != revision_id:
            raise TrainingRequestRevisionConflictError("只能修改当前 revision")
        if revision.row_version != expected_version:
            raise VersionConflictError("需求修订")
        if revision.status is not RevisionStatus.DRAFT:
            from app.domain.datasets.training_errors import TrainingRequestImmutableError

            raise TrainingRequestImmutableError("只有 DRAFT revision 可以原地修改")
        snapshot = changes.get("snapshot", revision.snapshot)
        reason = changes.get("change_reason", revision.change_reason)
        updated = revision.with_content(
            snapshot,
            change_reason=reason if isinstance(reason, str) else revision.change_reason,
            actor_id=scope.actor_id,
            now=_utcnow(),
        )
        saved = self._revisions.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_REVISION_RESOURCE,
            resource_id=saved.revision_id,
            action="update",
            from_status=revision.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id_text,
            details={"revision_sha256": saved.revision_sha256},
            event_type="training.request.revision_updated",
            event_payload={
                "training_request_id": str(request_id),
                "revision_id": str(saved.revision_id),
                "revision_no": saved.revision_no,
            },
            occurred_at=_utcnow(),
        )
        self._uow.commit()
        return saved

    # ---- 动作 ----
    def apply_revision_action(
        self,
        scope: ProjectScope,
        request_id: UUID,
        revision_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id_text: str | None = None,
    ) -> TrainingRequestRevision:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="revision")
        if action_name not in {"submit", "approve", "reject"}:
            raise DomainValidationError("需求修订仅支持 submit/approve/reject")
        body = {
            "action": action_name,
            "revision_id": str(revision_id),
            "expected_version": expected_version,
            "reason": reason,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._revision_replay(scope, request_id, replay.response_ref)
        request = self.get(scope, request_id)
        revision = self.get_revision(scope, request_id, revision_id)
        if request.current_revision_id != revision_id:
            raise TrainingRequestRevisionConflictError("只能操作当前 revision")
        if revision.row_version != expected_version:
            raise VersionConflictError("需求修订")
        if action_name not in allowed_revision_actions(revision.status):
            from app.domain.datasets.errors import InvalidStateTransitionError

            raise InvalidStateTransitionError("需求修订", revision.status.value, action_name)
        if action_name == "submit":
            saved = self._revisions.save(
                scope,
                revision.submit(actor_id=scope.actor_id, now=now),
                expected_version=revision.row_version,
            )
            saved = self._revisions.save(
                scope,
                saved.start_review(now=now),
                expected_version=saved.row_version,
            )
            # 状态机要求 DRAFT→SUBMITTED→PENDING_REVIEW 两次显式迁移，
            # 不能在同一 UPDATE 中跳跃，否则数据库兜底触发器会拒绝。
            request = request.apply_action("submit-revision", now=now, actor_id=scope.actor_id)
            request = self._requests.save(scope, request, expected_version=request.row_version - 1)
            request = request.apply_action("system-review-start", now=now, actor_id=scope.actor_id)
        elif action_name == "approve":
            saved = self._revisions.save(
                scope,
                revision.approve(actor_id=scope.actor_id, now=now),
                expected_version=revision.row_version,
            )
            request = request.approve_revision(now=now, actor_id=scope.actor_id)
        else:
            if not reason or not reason.strip():
                raise DomainValidationError("驳回需求必须填写原因")
            saved = self._revisions.save(
                scope,
                revision.reject(
                    actor_id=scope.actor_id,
                    now=now,
                    reason=reason,
                ),
                expected_version=revision.row_version,
            )
            request = request.reject_revision(now=now, actor_id=scope.actor_id)
        self._requests.save(scope, request, expected_version=request.row_version - 1)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_REVISION_RESOURCE,
            resource_id=saved.revision_id,
            action=action_name,
            from_status=revision.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id_text,
            details={"revision_no": saved.revision_no},
            event_type=TRAINING_REVISION_ACTION_EVENTS[action_name],
            event_payload={
                "training_request_id": str(request_id),
                "revision_id": str(saved.revision_id),
                "from_status": revision.status.value,
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
            response_ref=str(saved.revision_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def apply_request_action(
        self,
        scope: ProjectScope,
        request_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        restore_status: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id_text: str | None = None,
    ) -> TrainingRequest:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="request")
        if action_name not in {"block", "unblock", "cancel", "close"}:
            raise DomainValidationError("需求仅支持 block/unblock/cancel/close 动作")
        body = {
            "action": action_name,
            "expected_version": expected_version,
            "reason": reason,
            "restore_status": restore_status,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        request = self.get(scope, request_id)
        if request.row_version != expected_version:
            raise VersionConflictError("训练需求")
        if action_name not in allowed_request_actions(request.status):
            from app.domain.datasets.errors import InvalidStateTransitionError

            raise InvalidStateTransitionError("训练需求", request.status.value, action_name)
        if action_requires_reason("request", action_name) and not (reason and reason.strip()):
            raise DomainValidationError("该动作必须填写原因")
        updated = request.apply_action(
            action_name,
            now=now,
            actor_id=scope.actor_id,
            reason=reason,
            restore_status=restore_status,
        )
        saved = self._requests.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.training_request_id,
            action=action_name,
            from_status=request.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id_text,
            details={"restore_status": restore_status},
            event_type=TRAINING_REQUEST_ACTION_EVENTS[action_name],
            event_payload={
                "training_request_id": str(saved.training_request_id),
                "from_status": request.status.value,
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
            response_ref=str(saved.training_request_id),
            now=now,
        )
        self._uow.commit()
        return saved

    # ---- 内部 ----
    def _replay(self, scope: ProjectScope, response_ref: str | None) -> TrainingRequest:
        if not response_ref:
            raise ResourceNotFoundError("训练需求", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("训练需求", response_ref) from exc

    def _revision_replay(
        self,
        scope: ProjectScope,
        request_id: UUID,
        response_ref: str | None,
    ) -> TrainingRequestRevision:
        if not response_ref:
            raise ResourceNotFoundError("需求修订", response_ref)
        try:
            return self.get_revision(scope, request_id, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("需求修订", response_ref) from exc


__all__ = ["TrainingRequestUseCases"]
