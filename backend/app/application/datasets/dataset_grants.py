"""DatasetGrant 用例与跨项目访问授权检查。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    DatasetRepository,
    DatasetVersionRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasets.training_commands import NewDatasetGrant
from app.application.datasets.training_ports import GrantRepository
from app.domain.datasets.dataset_grants import DatasetGrant, new_dataset_grant
from app.domain.datasets.errors import (
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasets.training_enums import DatasetGrantStatus, GrantScope
from app.domain.datasets.training_errors import (
    DatasetGrantExpiredError,
    DatasetGrantForbiddenError,
    DatasetGrantRevokedError,
)
from app.domain.datasets.training_state_machines import validate_action_name
from app.domain.identity.models import ProjectScope

_RESOURCE = "DATASET_GRANT"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DatasetGrantUseCases:
    def __init__(
        self,
        *,
        grants: GrantRepository,
        datasets: DatasetRepository,
        versions: DatasetVersionRepository,
        projects_exist,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._grants = grants
        self._datasets = datasets
        self._versions = versions
        self._projects_exist = projects_exist
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, dataset_id: UUID, grant_id: UUID) -> DatasetGrant:
        grant = self._grants.get(scope, grant_id)
        if grant is None or grant.dataset_id != dataset_id:
            raise ResourceNotFoundError("数据集授权", grant_id)
        return self._effective(grant)

    def list(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        *,
        status: str | None = None,
        grantee_project_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetGrant]:
        dataset = self._datasets.get(scope, dataset_id)
        if dataset is None:
            raise ResourceNotFoundError("数据集", dataset_id)
        grants = self._grants.list(
            scope,
            dataset_id=dataset_id,
            status=status,
            grantee_project_id=grantee_project_id,
            after_id=after_id,
            limit=limit,
        )
        return [self._effective(grant) for grant in grants]

    def create(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        command: NewDatasetGrant,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DatasetGrant:
        key = validate_key(idempotency_key)
        body = {
            "dataset_id": str(dataset_id),
            "dataset_version_id": (
                str(command.dataset_version_id) if command.dataset_version_id else None
            ),
            "grantee_project_id": str(command.grantee_project_id),
            "grantee_team": command.grantee_team,
            "scope": list(command.scope),
            "reason": command.reason,
            "expires_at": command.expires_at.isoformat(),
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=now
        )
        if replay is not None:
            return self._replay(scope, dataset_id, replay.response_ref)
        dataset = self._datasets.get(scope, dataset_id)
        if dataset is None:
            raise ResourceNotFoundError("数据集", dataset_id)
        if command.dataset_version_id is not None:
            version = self._versions.get_for_dataset(
                scope, dataset_id, command.dataset_version_id
            )
            if version is None:
                raise ResourceNotFoundError("数据集版本", command.dataset_version_id)
        if command.grantee_project_id == scope.project_id:
            raise DomainValidationError("跨项目授权不得指向授权方自身项目")
        if not self._projects_exist(command.grantee_project_id):
            raise ResourceNotFoundError("接收项目", command.grantee_project_id)
        if command.expires_at <= now:
            raise DatasetGrantExpiredError("授权到期时间必须晚于当前时间")
        grant = new_dataset_grant(
            grant_id=uuid4(),
            project_id=scope.project_id,
            dataset_id=dataset_id,
            dataset_version_id=command.dataset_version_id,
            grantee_project_id=command.grantee_project_id,
            grantee_team=command.grantee_team,
            scope=list(command.scope),
            reason=command.reason,
            expires_at=command.expires_at,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._grants.add(scope, grant)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=created.grant_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=created.reason,
            request_id=request_id,
            details={
                "dataset_id": str(created.dataset_id),
                "dataset_version_id": (
                    str(created.dataset_version_id) if created.dataset_version_id else None
                ),
                "grantee_project_id": str(created.grantee_project_id),
                "scope": list(created.scope),
                "expires_at": created.expires_at.isoformat(),
            },
            event_type="dataset.grant.created",
            event_payload={
                "grant_id": str(created.grant_id),
                "dataset_id": str(created.dataset_id),
                "grantee_project_id": str(created.grantee_project_id),
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
            response_ref=str(created.grant_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        grant_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DatasetGrant:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="grant")
        if action_name not in {"approve", "reject", "revoke"}:
            raise DomainValidationError("授权仅支持 approve/reject/revoke")
        body = {
            "action": action_name,
            "expected_version": expected_version,
            "reason": reason,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=now
        )
        if replay is not None:
            return self._replay(scope, dataset_id, replay.response_ref)
        current = self._grants.get(scope, grant_id)
        if current is None or current.dataset_id != dataset_id:
            raise ResourceNotFoundError("数据集授权", grant_id)
        if current.row_version != expected_version:
            raise VersionConflictError("数据集授权")
        if action_name not in current.allowed_actions:
            from app.domain.datasets.errors import InvalidStateTransitionError

            raise InvalidStateTransitionError(
                "数据集授权", current.status.value, action_name
            )
        if action_name == "approve":
            updated = current.approve(actor_id=scope.actor_id, now=now)
        elif action_name == "reject":
            if not reason or not reason.strip():
                raise DomainValidationError("驳回授权必须填写原因")
            updated = current.reject(
                actor_id=scope.actor_id, now=now, reason=reason
            )
        else:
            if not reason or not reason.strip():
                raise DomainValidationError("撤销授权必须填写原因")
            updated = current.revoke(
                actor_id=scope.actor_id, now=now, reason=reason
            )
        saved = self._grants.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.grant_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={
                "dataset_id": str(saved.dataset_id),
                "grantee_project_id": str(saved.grantee_project_id),
                "expires_at": saved.expires_at.isoformat(),
            },
            event_type=f"dataset.grant.{action_name}",
            event_payload={
                "grant_id": str(saved.grant_id),
                "from_status": current.status.value,
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
            response_ref=str(saved.grant_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _effective(self, grant: DatasetGrant) -> DatasetGrant:
        return replace(grant, status=grant.status_at(_utcnow()))

    def _replay(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        response_ref: str | None,
    ) -> DatasetGrant:
        if not response_ref:
            raise ResourceNotFoundError("数据集授权", response_ref)
        try:
            return self.get(scope, dataset_id, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("数据集授权", response_ref) from exc


class DatasetGrantAccessService:
    """跨项目数据集访问检查：默认拒绝，授权到期/撤销立即失效。"""

    def __init__(self, grants: GrantRepository) -> None:
        self._grants = grants

    def authorize(
        self,
        *,
        project_id: UUID,
        dataset_id: UUID,
        version_id: UUID | None,
        user_id: UUID,
        required: GrantScope,
        now: datetime | None = None,
    ) -> bool:
        now = now or _utcnow()
        grants = self._grants.find_for_user(
            project_id=project_id,
            dataset_id=dataset_id,
            user_id=user_id,
        )
        denied_reason: Exception | None = None
        for grant in grants:
            if grant.dataset_version_id is not None and version_id is not None:
                if grant.dataset_version_id != version_id:
                    continue
            if grant.dataset_version_id is not None and version_id is None:
                continue
            try:
                if grant.allows(required, now):
                    return True
            except (DatasetGrantExpiredError, DatasetGrantRevokedError) as exc:
                denied_reason = exc
        if denied_reason is not None:
            raise denied_reason
        return False


__all__ = ["DatasetGrantAccessService", "DatasetGrantUseCases"]
