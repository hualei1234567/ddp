"""数据源应用层端口。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.application.audit import AuditRepository as _AuditRepository
from app.application.audit import OutboxRepository as _OutboxRepository
from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class DataSourceRepository(Protocol):
    def add(self, scope: ProjectScope, item: DataSource) -> DataSource: ...

    def get(self, scope: ProjectScope, data_source_id: UUID) -> DataSource | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> DataSource | None: ...

    def get_by_revision(
        self,
        scope: ProjectScope,
        *,
        repo_id: str,
        revision: str,
        config_name: str | None,
        split: str | None,
    ) -> DataSource | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSource]: ...

    def save(
        self,
        scope: ProjectScope,
        item: DataSource,
        *,
        expected_version: int,
    ) -> DataSource: ...


class DataSourceImportRepository(Protocol):
    def add(self, scope: ProjectScope, item: DataSourceImport) -> DataSourceImport: ...

    def get(self, scope: ProjectScope, import_id: UUID) -> DataSourceImport | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        data_source_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSourceImport]: ...

    def save(
        self,
        scope: ProjectScope,
        item: DataSourceImport,
        *,
        expected_version: int,
    ) -> DataSourceImport: ...


# 审计、outbox 与幂等记录类型统一由 application 顶层声明。
AuditRecord = object
OutboxEvent = object
AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository

__all__ = [
    "AuditRepository",
    "DataSourceImportRepository",
    "DataSourceRepository",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "OutboxRepository",
    "UnitOfWork",
]
