"""标注 Schema 与样本集 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.annotation_catalog_models import (
    AnnotationSampleSetOut,
    AnnotationSchemaActionRequest,
    AnnotationSchemaCreate,
    AnnotationSchemaOut,
    AnnotationSchemaPatch,
    SampleSetActionRequest,
    SampleSetCreate,
)
from app.api.v1.annotation_common import (
    ActionName,
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.schemas import PageOut
from app.application.annotation.commands import (
    NewAnnotationSampleSet,
    NewAnnotationSchema,
)
from app.core.errors import ValidationFailedError

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-schemas"])

_SAMPLE_SET_ACTIONS = frozenset({"preview", "snapshot", "consume", "cancel"})


@router.get("/annotation-schemas", response_model=PageOut[AnnotationSchemaOut])
def list_schemas(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AnnotationSchemaOut]:
    items = use_cases.schemas.list(
        scope,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.schema_id)
    return PageOut(
        items=[AnnotationSchemaOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/annotation-schemas", response_model=AnnotationSchemaOut, status_code=201)
def create_schema(
    payload: AnnotationSchemaCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationSchemaOut:
    schema = use_cases.schemas.create(
        scope,
        NewAnnotationSchema(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            annotation_levels=tuple(payload.annotation_levels),
            labels=tuple(payload.labels),
            compatibility=payload.compatibility,
            schema_lineage_id=payload.schema_lineage_id,
            schema_version=payload.schema_version,
            supersedes_id=payload.supersedes_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationSchemaOut.from_domain(schema)


@router.get(
    "/annotation-schemas/{schema_id}",
    response_model=AnnotationSchemaOut,
)
def get_schema(
    schema_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationSchemaOut:
    return AnnotationSchemaOut.from_domain(use_cases.schemas.get(scope, schema_id))


@router.patch(
    "/annotation-schemas/{schema_id}",
    response_model=AnnotationSchemaOut,
)
def update_schema(
    schema_id: UUID,
    payload: AnnotationSchemaPatch,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
) -> AnnotationSchemaOut:
    schema = use_cases.schemas.update(
        scope,
        schema_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=request_id(request),
    )
    return AnnotationSchemaOut.from_domain(schema)


@router.post(
    "/annotation-schemas/{schema_id}/actions/{action}",
    response_model=AnnotationSchemaOut,
)
def act_on_schema(
    schema_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationSchemaActionRequest | None = None,
) -> AnnotationSchemaOut:
    schema = use_cases.schemas.apply_action(
        scope,
        schema_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationSchemaOut.from_domain(schema)


@router.get(
    "/annotation-sample-sets",
    response_model=PageOut[AnnotationSampleSetOut],
)
def list_sample_sets(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AnnotationSampleSetOut]:
    items = use_cases.sample_sets.list(
        scope,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.sample_set_id)
    return PageOut(
        items=[AnnotationSampleSetOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/annotation-sample-sets",
    response_model=AnnotationSampleSetOut,
    status_code=201,
)
def create_sample_set(
    payload: SampleSetCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationSampleSetOut:
    sample_set = use_cases.sample_sets.create(
        scope,
        NewAnnotationSampleSet(
            code=payload.code,
            name=payload.name,
            strategy=payload.strategy,
            selection_rule_version=payload.selection_rule_version,
            filters=payload.filters,
            strata=tuple(payload.strata),
            dedup_key=payload.dedup_key,
            data_source=payload.data_source,
            warn_confirmed=payload.warn_confirmed,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationSampleSetOut.from_domain(sample_set)


@router.get(
    "/annotation-sample-sets/{sample_set_id}",
    response_model=AnnotationSampleSetOut,
)
def get_sample_set(
    sample_set_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationSampleSetOut:
    return AnnotationSampleSetOut.from_domain(use_cases.sample_sets.get(scope, sample_set_id))


@router.post(
    "/annotation-sample-sets/{sample_set_id}/actions/{action}",
    response_model=AnnotationSampleSetOut,
)
def act_on_sample_set(
    sample_set_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: SampleSetActionRequest | None = None,
) -> AnnotationSampleSetOut:
    if action not in _SAMPLE_SET_ACTIONS:
        raise ValidationFailedError("样本集不支持该动作")
    filters = payload.filters if payload is not None else None
    strata = payload.strata if payload is not None else None
    warn_confirmed = payload.warn_confirmed if payload is not None else False
    reason = payload.reason if payload is not None else None
    common = {
        "expected_version": expected_version,
        "idempotency_key": idempotency_key,
        "method": request.method,
        "path": request.url.path,
        "request_id": request_id(request),
    }
    if action == "preview":
        sample_set = use_cases.sample_sets.preview(
            scope,
            sample_set_id,
            filters=filters,
            strata=tuple(strata) if strata is not None else None,
            warn_confirmed=warn_confirmed,
            **common,
        )
    elif action == "snapshot":
        sample_set = use_cases.sample_sets.snapshot(scope, sample_set_id, **common)
    elif action == "consume":
        sample_set = use_cases.sample_sets.consume(scope, sample_set_id, **common)
    else:
        if not reason:
            raise ValidationFailedError("取消样本集必须填写原因")
        sample_set = use_cases.sample_sets.cancel(
            scope,
            sample_set_id,
            reason=reason,
            **common,
        )
    return AnnotationSampleSetOut.from_domain(sample_set)
