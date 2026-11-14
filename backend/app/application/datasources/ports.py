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
from app.domain.datasources.media import MediaAsset as MediaAssetT
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.datasources.review import DataReview as DataReviewT
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


class DataReviewRepository(Protocol):
    def add(self, scope: ProjectScope, review: DataReviewT) -> DataReviewT: ...

    def get_by_import(self, scope: ProjectScope, import_id: UUID) -> DataReviewT | None: ...

    def save(
        self, scope: ProjectScope, review: DataReviewT, *, expected_version: int
    ) -> DataReviewT: ...


class ObjectReader(Protocol):
    """签发临时只读访问；实现负责签名，不把存储凭据交给前端。"""

    def presign_get(self, object_key: str, *, expires_seconds: int) -> str: ...


class ObjectWriter(Protocol):
    """对象存储写入端口；实现不得在此层做业务校验。"""

    def put(self, object_key: str, data: bytes, *, content_type: str) -> None: ...


class MediaAssetRepository(Protocol):
    def add(self, scope: ProjectScope, asset: MediaAssetT) -> MediaAssetT: ...

    def list_by_import(
        self, scope: ProjectScope, import_id: UUID, *, limit: int = 200
    ) -> list[MediaAssetT]: ...


# 审计、outbox 与幂等记录类型统一由 application 顶层声明。
AuditRecord = object
OutboxEvent = object
AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository

__all__ = [
    "AuditRepository",
    "DataReviewRepository",
    "MediaAssetRepository",
    "ObjectReader",
    "ObjectWriter",
    "DataSourceImportRepository",
    "DataSourceRepository",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "OutboxRepository",
    "UnitOfWork",
]
