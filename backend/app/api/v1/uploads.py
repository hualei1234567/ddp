"""上传会话、分片与 RawAsset HTTP 路由。

路由只做协议适配：路径/请求体/If-Match/Idempotency-Key → 应用用例，
再把领域结果序列化为契约响应。权限与项目隔离由服务端依赖强制执行。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.governance_dependencies import EnforceObjectStorageUploadQuota
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import PageOut
from app.api.v1.upload_dependencies import UploadUseCasesDep
from app.api.v1.upload_schemas import (
    AbortUploadRequest,
    CompleteUploadRequest,
    CompleteUploadResponse,
    RawAssetOut,
    UploadPartAckOut,
    UploadPartAckRequest,
    UploadPartListOut,
    UploadPartUrlOut,
    UploadPartUrlRequest,
    UploadSessionCreate,
    UploadSessionOut,
)
from app.application.ingestion import (
    AbortUploadSessionCommand,
    AckUploadPartCommand,
    CompleteUploadCommand,
    CreateUploadSessionCommand,
)
from app.application.ingestion import (
    CompletePartSpec as CompletePartCommand,
)
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["uploads"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
PartNumber = Annotated[int, Path(ge=1, le=10000)]


@router.post(
    "/upload-sessions",
    response_model=UploadSessionOut,
    status_code=201,
)
def create_upload_session(
    payload: UploadSessionCreate,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: UploadUseCasesDep,
    _quota_guard: EnforceObjectStorageUploadQuota,
    idempotency_key: IdempotencyKey = None,
) -> UploadSessionOut:
    created = use_cases.uploads.create_session(
        scope,
        CreateUploadSessionCommand(**payload.model_dump()),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    response.headers["Idempotent-Replayed"] = "true" if created.replayed else "false"
    response.headers["ETag"] = f'"{created.session.version}"'
    return UploadSessionOut.from_session(created.session)


@router.get(
    "/upload-sessions/{upload_session_id}",
    response_model=UploadSessionOut,
)
def get_upload_session(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    scope: ReadScope,
    use_cases: UploadUseCasesDep,
) -> UploadSessionOut:
    detail = use_cases.uploads.get_session(
        scope,
        upload_session_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = f'"{detail.session.version}"'
    return UploadSessionOut.from_detail(detail)


@router.get(
    "/upload-sessions/{upload_session_id}/parts",
    response_model=UploadPartListOut,
)
def list_upload_parts(
    upload_session_id: UUID,
    request: Request,
    scope: ReadScope,
    use_cases: UploadUseCasesDep,
) -> UploadPartListOut:
    detail = use_cases.uploads.list_parts(
        scope,
        upload_session_id,
        request_id=_request_id(request),
    )
    return UploadPartListOut.from_detail(detail)


@router.post(
    "/upload-sessions/{upload_session_id}/parts/{part_number}/url",
    response_model=UploadPartUrlOut,
)
def create_upload_part_url(
    upload_session_id: UUID,
    part_number: PartNumber,
    request: Request,
    scope: WriteScope,
    use_cases: UploadUseCasesDep,
    payload: UploadPartUrlRequest | None = None,
) -> UploadPartUrlOut:
    signed = use_cases.uploads.create_part_url(
        scope,
        upload_session_id,
        part_number,
        sha256=payload.sha256 if payload is not None else None,
        request_id=_request_id(request),
    )
    return UploadPartUrlOut(
        part_number=part_number,
        method="PUT",
        url=signed.url,
        headers=dict(signed.headers),
        expires_at=signed.expires_at,
    )


@router.post(
    "/upload-sessions/{upload_session_id}/parts/{part_number}/ack",
    response_model=UploadPartAckOut,
)
def ack_upload_part(
    upload_session_id: UUID,
    part_number: PartNumber,
    payload: UploadPartAckRequest,
    request: Request,
    scope: WriteScope,
    use_cases: UploadUseCasesDep,
) -> UploadPartAckOut:
    result = use_cases.uploads.ack_part(
        scope,
        upload_session_id,
        part_number,
        AckUploadPartCommand(**payload.model_dump()),
        request_id=_request_id(request),
    )
    return UploadPartAckOut.from_result(result)


@router.post(
    "/upload-sessions/{upload_session_id}/complete",
    response_model=CompleteUploadResponse,
    status_code=202,
)
def complete_upload_session(
    upload_session_id: UUID,
    payload: CompleteUploadRequest,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: UploadUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> CompleteUploadResponse:
    result = use_cases.uploads.complete(
        scope,
        upload_session_id,
        CompleteUploadCommand(
            expected_sha256=payload.expected_sha256,
            expected_size_bytes=payload.expected_size_bytes,
            parts=tuple(CompletePartCommand(**part.model_dump()) for part in payload.parts),
            metadata=payload.metadata,
        ),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    response.headers["Idempotent-Replayed"] = "true" if result.replayed else "false"
    response.headers["ETag"] = f'"{result.session.version}"'
    return CompleteUploadResponse.from_result(result)


@router.post(
    "/upload-sessions/{upload_session_id}/abort",
    response_model=UploadSessionOut,
)
def abort_upload_session(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: UploadUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AbortUploadRequest | None = None,
) -> UploadSessionOut:
    session = use_cases.uploads.abort(
        scope,
        upload_session_id,
        AbortUploadSessionCommand(reason=payload.reason if payload is not None else None),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = f'"{session.version}"'
    return UploadSessionOut.from_session(session)


@router.get("/raw-assets", response_model=PageOut[RawAssetOut])
def list_raw_assets(
    scope: ReadScope,
    use_cases: UploadUseCasesDep,
    status: str | None = None,
    collection_task_id: UUID | None = None,
    format: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[RawAssetOut]:
    page = use_cases.assets.list_assets(
        scope,
        status=status,
        collection_task_id=collection_task_id,
        format=format,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[RawAssetOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/raw-assets/{asset_id}", response_model=RawAssetOut)
def get_raw_asset(
    asset_id: UUID,
    scope: ReadScope,
    use_cases: UploadUseCasesDep,
) -> RawAssetOut:
    return RawAssetOut.from_domain(use_cases.assets.get_asset(scope, asset_id))


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
