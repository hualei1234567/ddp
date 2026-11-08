"""IngestionJob HTTP 路由。

路由只做协议适配：路径/请求体/If-Match/Idempotency-Key → 应用用例，
再把领域实体序列化为契约响应。权限与项目隔离由服务端依赖强制执行。
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.ingestion_job_dependencies import IngestionJobUseCasesDep
from app.api.v1.ingestion_job_schemas import (
    IngestionJobActionRequest,
    IngestionJobCreate,
    IngestionJobOut,
)
from app.api.v1.preconditions import require_expected_version, require_idempotency_key
from app.api.v1.schemas import PageOut
from app.application.ingestion import CreateIngestionJobCommand, JobActionCommand
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["ingestion-jobs"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]


@router.post(
    "/ingestion-jobs",
    response_model=IngestionJobOut,
    status_code=201,
)
def create_ingestion_job(
    payload: IngestionJobCreate,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: IngestionJobUseCasesDep,
    idempotency_key: IdempotencyKey,
) -> IngestionJobOut:
    job = use_cases.create(
        scope,
        CreateIngestionJobCommand(**payload.model_dump()),
        request_id=_request_id(request),
        http_idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
    )
    response.headers["Idempotent-Replayed"] = "true" if use_cases.last_replayed else "false"
    response.headers["ETag"] = f'"{job.version}"'
    return IngestionJobOut.from_domain(job)


@router.get("/ingestion-jobs", response_model=PageOut[IngestionJobOut])
def list_ingestion_jobs(
    scope: ReadScope,
    use_cases: IngestionJobUseCasesDep,
    status: str | None = None,
    raw_asset_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[IngestionJobOut]:
    page = use_cases.list_jobs(
        scope,
        status=status,
        raw_asset_id=raw_asset_id,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[IngestionJobOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobOut)
def get_ingestion_job(
    job_id: UUID,
    response: Response,
    scope: ReadScope,
    use_cases: IngestionJobUseCasesDep,
) -> IngestionJobOut:
    job = use_cases.get(scope, job_id)
    response.headers["ETag"] = f'"{job.version}"'
    return IngestionJobOut.from_domain(job)


@router.post(
    "/ingestion-jobs/{job_id}/actions/{action}",
    response_model=IngestionJobOut,
)
def act_on_ingestion_job(
    job_id: UUID,
    action: Literal["cancel", "retry"],
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: IngestionJobUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    payload: IngestionJobActionRequest | None = None,
) -> IngestionJobOut:
    job = use_cases.apply_action(
        scope,
        job_id,
        JobActionCommand(
            action=action,
            expected_version=expected_version,
            reason=payload.reason if payload is not None else None,
        ),
        request_id=_request_id(request),
        http_idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
    )
    response.headers["Idempotent-Replayed"] = "true" if use_cases.last_replayed else "false"
    response.headers["ETag"] = f'"{job.version}"'
    return IngestionJobOut.from_domain(job)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
