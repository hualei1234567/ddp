"""供应商域应用层端口与 DTO。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.application.idempotency import IdempotencyRecord as _IdempotencyRecord
from app.application.idempotency import IdempotencyRepository as _IdempotencyRepository
from app.domain.identity.models import ProjectScope
from app.domain.suppliers.models import (
    AcceptanceLedgerEntry,
    DeliveryBatch,
    InboundSegment,
    Supplier,
)


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class SupplierRepository(Protocol):
    def add(self, scope: ProjectScope, supplier: Supplier) -> Supplier: ...

    def get(self, scope: ProjectScope, supplier_id: UUID) -> Supplier | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        category: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Supplier]: ...

    def save(
        self, scope: ProjectScope, supplier: Supplier, *, expected_version: int
    ) -> Supplier: ...


class DeliveryBatchRepository(Protocol):
    def add(self, scope: ProjectScope, batch: DeliveryBatch) -> DeliveryBatch: ...

    def get(self, scope: ProjectScope, batch_id: UUID) -> DeliveryBatch | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        supplier_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DeliveryBatch]: ...

    def save(
        self, scope: ProjectScope, batch: DeliveryBatch, *, expected_version: int
    ) -> DeliveryBatch: ...

    def all_batches(self, scope: ProjectScope, supplier_id: UUID) -> list[DeliveryBatch]: ...


class InboundSegmentRepository(Protocol):
    def add(self, scope: ProjectScope, segment: InboundSegment) -> InboundSegment: ...

    def get(self, scope: ProjectScope, segment_id: UUID) -> InboundSegment | None: ...

    def list(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InboundSegment]: ...

    def save(
        self, scope: ProjectScope, segment: InboundSegment, *, expected_version: int
    ) -> InboundSegment: ...

    def status_counts(self, scope: ProjectScope, batch_id: UUID) -> dict[str, int]: ...

    def decided_asset_refs_missing(self, scope: ProjectScope, batch_id: UUID) -> list[str]: ...

    def count_decided(self, scope: ProjectScope, supplier_id: UUID) -> tuple[int, int]: ...


class AcceptanceLedgerRepository(Protocol):
    def append(self, scope: ProjectScope, entry: AcceptanceLedgerEntry) -> None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID | None = None,
        supplier_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AcceptanceLedgerEntry]: ...

    def count(self, scope: ProjectScope, batch_id: UUID) -> int: ...


class RawAssetExistence(Protocol):
    """跨模块只读校验：片段引用的 RawAsset 必须存在于同项目。"""

    def exists(self, project_id: UUID, raw_asset_id: UUID) -> bool: ...


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: object) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: object) -> None: ...


# 幂等记录类型与仓储协议由 application 顶层统一声明；本域直接复用，
# 不再各写一份 ``object`` 签名（那等于没有协议约束）。
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository


__all__ = [
    "AcceptanceLedgerRepository",
    "AuditRepository",
    "DeliveryBatchRepository",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "InboundSegmentRepository",
    "OutboxRepository",
    "RawAssetExistence",
    "SupplierRepository",
    "UnitOfWork",
]
