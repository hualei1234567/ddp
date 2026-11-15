"""数据源用例：登记、改档、导入任务编排。

不改写既有模块的规则：幂等、审计、乐观锁都复用
``app.application.{idempotency,audit}`` 的权威实现。
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.datasources.commands import (
    DataSourcePatch,
    NewDataSource,
    NewDataSourceImport,
)
from app.application.datasources.idempotency import IdempotencyGuard, validate_key
from app.application.datasources.ports import (
    AuditRepository,
    DataSourceImportRepository,
    DataSourceRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.datasources.state_machines import (
    allowed_import_actions,
    import_target_status,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_SOURCE = "data_source"
_RESOURCE_IMPORT = "data_source_import"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DataSourceUseCases:
    def __init__(
        self,
        sources: DataSourceRepository,
        imports: DataSourceImportRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._sources = sources
        self._imports = imports
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    # ---------- 数据源 ----------

    def list_sources(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSource]:
        return self._sources.list(scope, kind=kind, after_id=after_id, limit=limit)

    def get_source(self, scope: ProjectScope, data_source_id: UUID) -> DataSource:
        item = self._sources.get(scope, data_source_id)
        if item is None:
            raise ResourceNotFoundError("数据源", data_source_id)
        return item

    def create_source(
        self,
        scope: ProjectScope,
        command: NewDataSource,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataSource:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_source(scope, replay.response_ref)

        if self._sources.get_by_code(scope, command.code) is not None:
            raise DuplicateResourceError("项目内数据源编码重复")
        # 同一 repo + revision + config + split 只登记一次，否则血缘分叉。
        if (
            self._sources.get_by_revision(
                scope,
                repo_id=command.repo_id,
                revision=command.revision,
                config_name=command.config_name,
                split=command.split,
            )
            is not None
        ):
            raise DuplicateResourceError("同一 repo/revision/config/split 已登记")

        item = DataSource(
            data_source_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            kind=command.kind,
            repo_id=command.repo_id,
            revision=command.revision,
            license=command.license,
            access_condition=command.access_condition,
            is_gated=command.is_gated,
            config_name=command.config_name,
            split=command.split,
            dataset_card_url=command.dataset_card_url,
            notes=command.notes,
            metadata=dict(command.metadata or {}),
        )
        created = self._sources.add(scope, item)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_SOURCE,
            resource_id=created.data_source_id,
            action="register",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "repo_id": created.repo_id,
                "revision": created.revision,
                "license": created.license,
                "access_condition": str(created.access_condition),
                "is_gated": created.is_gated,
            },
            event_type="datasource.registered",
            event_payload={
                "data_source_id": str(created.data_source_id),
                "repo_id": created.repo_id,
                "revision": created.revision,
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
            response_ref=str(created.data_source_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_source(
        self,
        scope: ProjectScope,
        data_source_id: UUID,
        patch: DataSourcePatch,
        *,
        expected_version: int,
        request_id: str | None = None,
    ) -> DataSource:
        current = self.get_source(scope, data_source_id)
        if current.row_version != expected_version:
            raise VersionConflictError("数据源")
        updates: dict[str, object] = {}
        for name in (
            "name",
            "license",
            "access_condition",
            "is_gated",
            "config_name",
            "split",
            "notes",
        ):
            value = getattr(patch, name)
            if value is not None:
                updates[name] = value
        if not updates:
            raise DomainValidationError("没有需要修改的字段")
        # repo_id / revision 不可改：改 revision 等于换数据，必须重新登记。
        now = _utcnow()
        updated = replace(current, **updates, row_version=current.row_version + 1)
        saved = self._sources.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_SOURCE,
            resource_id=saved.data_source_id,
            action="update",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={"changed_fields": sorted(updates)},
            event_type="datasource.updated",
            event_payload={
                "data_source_id": str(saved.data_source_id),
                "changed_fields": sorted(updates),
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    # ---------- 导入任务 ----------

    def list_imports(
        self,
        scope: ProjectScope,
        *,
        data_source_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSourceImport]:
        return self._imports.list(
            scope,
            data_source_id=data_source_id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def get_import(self, scope: ProjectScope, import_id: UUID) -> DataSourceImport:
        item = self._imports.get(scope, import_id)
        if item is None:
            raise ResourceNotFoundError("导入任务", import_id)
        return item

    def create_import(
        self,
        scope: ProjectScope,
        data_source_id: UUID,
        command: NewDataSourceImport,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataSourceImport:
        """登记一次导入；**不在此处下载数据**，只建任务并投递事件。"""
        key = validate_key(idempotency_key)
        body = {
            "data_source_id": str(data_source_id),
            "scope": command.scope,
            "tool_name": command.tool_name,
            "tool_version": command.tool_version,
            "sample_limit": command.sample_limit,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_import(scope, replay.response_ref)

        source = self.get_source(scope, data_source_id)
        item = DataSourceImport(
            import_id=uuid4(),
            project_id=scope.project_id,
            data_source_id=data_source_id,
            status=ImportStatus.PENDING,
            scope=command.scope,
            tool_name=command.tool_name,
            tool_version=command.tool_version,
            sample_limit=command.sample_limit,
            cache_location=command.cache_location,
        )
        created = self._imports.add(scope, item)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_IMPORT,
            resource_id=created.import_id,
            action="create",
            from_status=None,
            to_status=ImportStatus.PENDING.value,
            reason=None,
            request_id=request_id,
            details={
                "data_source_id": str(data_source_id),
                "repo_id": source.repo_id,
                "revision": source.revision,
                "scope": created.scope.value,
                "sample_limit": created.sample_limit,
            },
            event_type="datasource.import_requested",
            event_payload={
                "import_id": str(created.import_id),
                "data_source_id": str(data_source_id),
                "repo_id": source.repo_id,
                "revision": source.revision,
                "scope": created.scope.value,
                "sample_limit": created.sample_limit,
            },
            occurred_at=now,
            # 投递类：worker 据此事真的去下载并解析（R-13 / T-1）。
            dispatch=True,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.import_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_import_action(
        self,
        scope: ProjectScope,
        import_id: UUID,
        action: str,
        *,
        expected_version: int,
        row_count: int | None = None,
        file_count: int | None = None,
        error_message: str | None = None,
        request_id: str | None = None,
    ) -> DataSourceImport:
        current = self.get_import(scope, import_id)
        if current.row_version != expected_version:
            raise VersionConflictError("导入任务")
        target = import_target_status(current.status, action)
        now = _utcnow()
        updates: dict[str, object] = {
            "status": target,
            "row_version": current.row_version + 1,
        }
        if target is ImportStatus.RUNNING:
            updates["started_at"] = now
        if target in {ImportStatus.SUCCEEDED, ImportStatus.FAILED, ImportStatus.CANCELLED}:
            updates["finished_at"] = now
        if row_count is not None:
            updates["imported_row_count"] = row_count
        if file_count is not None:
            updates["imported_file_count"] = file_count
        if target is ImportStatus.FAILED and not (error_message or "").strip():
            raise DomainValidationError("导入失败必须记录 error_message")
        if error_message is not None:
            updates["error_message"] = error_message

        saved = self._imports.save(
            scope, replace(current, **updates), expected_version=expected_version
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_IMPORT,
            resource_id=saved.import_id,
            action=action,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=error_message,
            request_id=request_id,
            details={
                "row_count": saved.imported_row_count,
                "file_count": saved.imported_file_count,
            },
            event_type=f"datasource.import.{action}",
            event_payload={
                "import_id": str(saved.import_id),
                "data_source_id": str(saved.data_source_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    # ---------- 幂等重放 ----------

    def _replay_source(self, scope: ProjectScope, response_ref: str | None) -> DataSource:
        if not response_ref:
            raise ResourceNotFoundError("数据源", response_ref)
        try:
            return self.get_source(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("数据源", response_ref) from exc

    def _replay_import(self, scope: ProjectScope, response_ref: str | None) -> DataSourceImport:
        if not response_ref:
            raise ResourceNotFoundError("导入任务", response_ref)
        try:
            return self.get_import(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("导入任务", response_ref) from exc


def import_actions(status: str) -> frozenset[str]:
    """当前状态允许的导入动作名；供 API 的 ``allowed_actions`` 出口。"""
    return allowed_import_actions(status)
