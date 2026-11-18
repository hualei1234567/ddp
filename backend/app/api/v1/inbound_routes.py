"""到货批次、片段裁决与验收账本 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.schemas import PageOut
from app.api.v1.supplier_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    SupplierServicesDep,
    WriteScope,
    request_id,
)
from app.api.v1.supplier_response_models import (
    AcceptanceLedgerEntryOut,
    DeliveryBatchOut,
    InboundSegmentOut,
)
from app.api.v1.supplier_schemas import (
    BatchActionRequest,
    BatchCreate,
    SegmentCreate,
    SegmentDecisionRequest,
)
from app.application.suppliers.commands import (
    NewDeliveryBatch,
    NewInboundSegment,
    SegmentDecisionCommand,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["inbound-batches"])


@router.get("/inbound-batches", response_model=PageOut[DeliveryBatchOut])
def list_batches(
    scope: ReadScope,
    services: SupplierServicesDep,
    status: str | None = None,
    supplier_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[DeliveryBatchOut]:
    items = services.batches.list(
        scope,
        status=status,
        supplier_id=supplier_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.batch_id)
    return PageOut(
        items=[DeliveryBatchOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/inbound-batches", response_model=DeliveryBatchOut, status_code=201)
def create_batch(
    payload: BatchCreate,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> DeliveryBatchOut:
    batch = services.batches.create(
        scope,
        NewDeliveryBatch(
            supplier_id=payload.supplier_id,
            batch_code=payload.batch_code,
            title=payload.title,
            planned_segment_count=payload.planned_segment_count,
            planned_arrival_date=payload.planned_arrival_date,
            storage_prefix=payload.storage_prefix,
            notes=payload.notes,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DeliveryBatchOut.from_domain(batch)


@router.get("/inbound-batches/{batch_id}", response_model=DeliveryBatchOut)
def get_batch(batch_id: UUID, scope: ReadScope, services: SupplierServicesDep) -> DeliveryBatchOut:
    return DeliveryBatchOut.from_domain(services.batches.get(scope, batch_id))


@router.post(
    "/inbound-batches/{batch_id}/actions/{action}",
    response_model=DeliveryBatchOut,
)
def act_on_batch(
    batch_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    payload: BatchActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeliveryBatchOut:
    batch = services.batches.apply_action(
        scope,
        batch_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DeliveryBatchOut.from_domain(batch)


@router.post("/inbound-batches/{batch_id}/finalize", response_model=DeliveryBatchOut)
def finalize_batch(
    batch_id: UUID,
    request: Request,
    scope: OwnerScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    payload: BatchActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeliveryBatchOut:
    """终审属于高风险放行：要求项目 Owner；结果由片段统计决定。"""
    batch = services.batches.apply_action(
        scope,
        batch_id,
        "finalize",
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DeliveryBatchOut.from_domain(batch)


@router.get("/inbound-batches/{batch_id}/segments", response_model=PageOut[InboundSegmentOut])
def list_segments(
    batch_id: UUID,
    scope: ReadScope,
    services: SupplierServicesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[InboundSegmentOut]:
    items = services.batches.list_segments(
        scope, batch_id, status=status, after_id=decode_cursor(cursor), limit=fetch_limit(limit)
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.segment_id)
    return PageOut(
        items=[InboundSegmentOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/inbound-batches/{batch_id}/segments", response_model=InboundSegmentOut, status_code=201
)
def add_segment(
    batch_id: UUID,
    payload: SegmentCreate,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> InboundSegmentOut:
    segment = services.batches.add_segment(
        scope,
        batch_id,
        NewInboundSegment(
            segment_code=payload.segment_code,
            raw_asset_id=payload.raw_asset_id,
            duration_ns=payload.duration_ns,
            task_name=payload.task_name,
            diagnostics=payload.diagnostics,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return InboundSegmentOut.from_domain(segment)


@router.get(
    "/inbound-batches/{batch_id}/segments/{segment_id}",
    response_model=InboundSegmentOut,
)
def get_segment(
    batch_id: UUID,
    segment_id: UUID,
    scope: ReadScope,
    services: SupplierServicesDep,
) -> InboundSegmentOut:
    return InboundSegmentOut.from_domain(services.batches.get_segment(scope, batch_id, segment_id))


@router.post(
    "/inbound-batches/{batch_id}/segments/{segment_id}/actions/{action}",
    response_model=InboundSegmentOut,
)
def act_on_segment(
    batch_id: UUID,
    segment_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> InboundSegmentOut:
    segment = services.batches.apply_segment_action(
        scope,
        batch_id,
        segment_id,
        action,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return InboundSegmentOut.from_domain(segment)


@router.post(
    "/inbound-batches/{batch_id}/segments/{segment_id}/decision",
    response_model=InboundSegmentOut,
)
def decide_segment(
    batch_id: UUID,
    segment_id: UUID,
    payload: SegmentDecisionRequest,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> InboundSegmentOut:
    segment = services.batches.decide_segment(
        scope,
        batch_id,
        segment_id,
        SegmentDecisionCommand(decision=payload.decision, reason=payload.reason),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return InboundSegmentOut.from_domain(segment)


@router.get("/acceptance-ledger", response_model=PageOut[AcceptanceLedgerEntryOut])
def list_ledger(
    scope: ReadScope,
    services: SupplierServicesDep,
    batch_id: UUID | None = None,
    supplier_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AcceptanceLedgerEntryOut]:
    items = services.batches.ledger(
        scope,
        batch_id=batch_id,
        supplier_id=supplier_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.ledger_id)
    return PageOut(
        items=[AcceptanceLedgerEntryOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


__all__ = ["router"]
