"""Dataset 用例：创建、列表、详情、编辑、归档与恢复。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.commands import NewDataset
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    DatasetRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasets.errors import (
    ArchiveBlockedError,
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasets.models import Dataset, new_dataset, update_dataset
from app.domain.datasets.state_machines import (
    action_requires_reason,
    allowed_dataset_actions,
    dataset_target,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "DATASET"


class DatasetUseCases:
    def __init__(
        self,
        datasets: DatasetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._datasets = datasets
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, dataset_id: UUID) -> Dataset:
        dataset = self._datasets.get(scope, dataset_id)
        if dataset is None:
            raise ResourceNotFoundError("数据集", dataset_id)
        return dataset

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        purpose: str | None = None,
        usage_policy: str | None = None,
        tags: tuple[str, ...] = (),
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Dataset]:
        """列出数据集；``tags`` 语义为"任一命中"，由仓储在 SQL 层下推。"""
        return self._datasets.list(
            scope,
            status=status,
            purpose=purpose,
            usage_policy=usage_policy,
            tags=tags,
            after_id=after_id,
            limit=limit,
        )

    def allowed_actions(self, scope: ProjectScope, dataset_id: UUID) -> frozenset[str]:
        return allowed_dataset_actions(self.get(scope, dataset_id).status)

    def create(
        self,
        scope: ProjectScope,
        command: NewDataset,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Dataset:
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
        if self._datasets.get_by_code(scope, command.code) is not None:
            raise DuplicateResourceError("项目内数据集编码重复")
        dataset = new_dataset(
            dataset_id=uuid4(),
            project_id=scope.project_id,
            dataset_lineage_id=uuid4(),
            code=command.code,
            name=command.name,
            purpose=command.purpose,
            owner_id=command.owner_id,
            usage_policy=command.usage_policy,
            description=command.description,
            tags=command.tags,
            retention_policy=dict(command.retention_policy or {}) or None,
            default_dedup_policy_id=command.default_dedup_policy_id,
            default_split_policy_id=command.default_split_policy_id,
            default_leakage_policy_id=command.default_leakage_policy_id,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._datasets.add(scope, dataset)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.dataset_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={"code": created.code, "usage_policy": created.usage_policy.value},
            event_type="dataset.created",
            event_payload={
                "dataset_id": str(created.dataset_id),
                "dataset_lineage_id": str(created.dataset_lineage_id),
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
            response_ref=str(created.dataset_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> Dataset:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        current = self.get(scope, dataset_id)
        if current.row_version != expected_version:
            raise VersionConflictError("数据集")
        now = _utcnow()
        updated = update_dataset(
            current,
            dict(changes),
            actor_id=scope.actor_id,
            now=now,
        )
        saved = self._datasets.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.dataset_id,
            action="update",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes)},
            event_type="dataset.updated",
            event_payload={
                "dataset_id": str(saved.dataset_id),
                "version": saved.row_version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Dataset:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="dataset")
        body = {"action": action_name, "expected_version": expected_version, "reason": reason}
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
        current = self.get(scope, dataset_id)
        if current.row_version != expected_version:
            raise VersionConflictError("数据集")
        if action_name not in allowed_dataset_actions(current.status):
            raise InvalidStateTransitionError("数据集", current.status.value, action_name)
        if action_requires_reason("dataset", action_name) and not reason:
            raise DomainValidationError("该动作必须填写原因")
        target = dataset_target(current.status, action_name)
        changes: dict[str, object] = {"status": target}
        if action_name == "archive":
            if current.status.value == "ARCHIVED":
                raise ArchiveBlockedError("数据集已归档")
            changes.update(
                archived_at=now,
                archived_by=scope.actor_id,
                archive_reason=reason,
            )
        elif action_name == "restore":
            changes.update(archived_at=None, archived_by=None, archive_reason=None)
        updated = replace(
            current,
            **changes,
            updated_at=now,
            updated_by=scope.actor_id,
            row_version=current.row_version + 1,
        )
        saved = self._datasets.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.dataset_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={},
            event_type="dataset.status_changed",
            event_payload={
                "dataset_id": str(saved.dataset_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
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
            response_ref=str(saved.dataset_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> Dataset:
        if not response_ref:
            raise ResourceNotFoundError("数据集", response_ref)
        try:
            dataset_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("数据集", response_ref) from exc
        return self.get(scope, dataset_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)
