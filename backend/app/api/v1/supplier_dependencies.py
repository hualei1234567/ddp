"""供应商域 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession, get_project_context, get_project_scope, get_write_scope
from app.api.v1.dataset_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    WriteScope,
    request_id,
)
from app.application.suppliers.inbound import InboundBatchUseCases
from app.application.suppliers.supplier_directory import SupplierUseCases
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.idempotency import SqlIdempotencyRepository
from app.infrastructure.repositories.supplier_batches import SqlDeliveryBatchRepository
from app.infrastructure.repositories.supplier_raw_assets import SqlRawAssetLookup
from app.infrastructure.repositories.suppliers import (
    SqlAcceptanceLedgerRepository,
    SqlInboundSegmentRepository,
    SqlSupplierRepository,
)

Expected = ExpectedVersion


@dataclass(frozen=True, slots=True)
class SupplierServices:
    suppliers: SupplierUseCases
    batches: InboundBatchUseCases


def get_supplier_services(session: DbSession) -> SupplierServices:
    suppliers = SqlSupplierRepository(session)
    batches = SqlDeliveryBatchRepository(session)
    segments = SqlInboundSegmentRepository(session)
    ledger = SqlAcceptanceLedgerRepository(session)
    audit = SqlDatasetAuditRepository(session)
    outbox = SqlDatasetOutboxRepository(session)
    raw_assets = SqlRawAssetLookup(session)
    idempotency = SqlIdempotencyRepository(session)
    return SupplierServices(
        suppliers=SupplierUseCases(
            suppliers, batches, segments, ledger, audit, outbox, session, idempotency
        ),
        batches=InboundBatchUseCases(
            suppliers, batches, segments, ledger, audit, outbox, raw_assets, session, idempotency
        ),
    )


SupplierServicesDep = Annotated[SupplierServices, Depends(get_supplier_services)]

__all__ = [
    "Expected",
    "ExpectedVersion",
    "IdempotencyKey",
    "OwnerScope",
    "ReadScope",
    "SupplierServices",
    "SupplierServicesDep",
    "WriteScope",
    "get_project_context",
    "get_project_scope",
    "get_write_scope",
    "request_id",
    "ProjectScope",
]
