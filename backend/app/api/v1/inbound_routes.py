"""到货批次、片段裁决与验收账本 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.supplier_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    SupplierServicesDep,
    WriteScope,
    request_id,
)
from app.api.v1.supplier_schemas import (
    BatchActionRequest,
    BatchCreate,
    SegmentCreate,
    SegmentDecisionRequest,
    batch_out,
    ledger_out,
    segment_out,
)
from app.application.suppliers.commands import (
    NewDeliveryBatch,
    NewInboundSegment,
    SegmentDecisionCommand,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["inbound-batches"])


@router.get("/inbound-batches")
def list_batches(
    scope: ReadScope,
    services: SupplierServicesDep,
    status: str | None = None,
    supplier_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.batches.list(
        scope,
        status=status,
        supplier_id=supplier_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.batch_id)
    return {
        "items": [batch_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/inbound-batches", status_code=201)
def create_batch(
    payload: BatchCreate,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
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
    return batch_out(batch)


@router.get("/inbound-batches/{batch_id}")
def get_batch(batch_id: UUID, scope: ReadScope, services: SupplierServicesDep) -> dict[str, Any]:
    return batch_out(services.batches.get(scope, batch_id))


@router.post("/inbound-batches/{batch_id}/actions/{action}")
def act_on_batch(
    batch_id: UUID,
    action: str,
    payload: BatchActionRequest | None,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
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
    return batch_out(batch)


@router.post("/inbound-batches/{batch_id}/finalize")
def finalize_batch(
    batch_id: UUID,
    payload: BatchActionRequest | None,
    request: Request,
    scope: OwnerScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
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
    return batch_out(batch)


@router.get("/inbound-batches/{batch_id}/segments")
def list_segments(
    batch_id: UUID,
    scope: ReadScope,
    services: SupplierServicesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.batches.list_segments(
        scope, batch_id, status=status, after_id=decode_cursor(cursor), limit=fetch_limit(limit)
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.segment_id)
    return {
        "items": [segment_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/inbound-batches/{batch_id}/segments", status_code=201)
def add_segment(
    batch_id: UUID,
    payload: SegmentCreate,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
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
    return segment_out(segment)


@router.get("/inbound-batches/{batch_id}/segments/{segment_id}")
def get_segment(
    batch_id: UUID,
    segment_id: UUID,
    scope: ReadScope,
    services: SupplierServicesDep,
) -> dict[str, Any]:
    return segment_out(services.batches.get_segment(scope, batch_id, segment_id))


@router.post("/inbound-batches/{batch_id}/segments/{segment_id}/actions/{action}")
def act_on_segment(
    batch_id: UUID,
    segment_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
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
    return segment_out(segment)


@router.post("/inbound-batches/{batch_id}/segments/{segment_id}/decision")
def decide_segment(
    batch_id: UUID,
    segment_id: UUID,
    payload: SegmentDecisionRequest,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
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
    return segment_out(segment)


@router.get("/acceptance-ledger")
def list_ledger(
    scope: ReadScope,
    services: SupplierServicesDep,
    batch_id: UUID | None = None,
    supplier_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.batches.ledger(
        scope,
        batch_id=batch_id,
        supplier_id=supplier_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.ledger_id)
    return {
        "items": [ledger_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


__all__ = ["router"]
