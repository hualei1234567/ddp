"""采集会话 HTTP 路由（N9-001）。

路由只做协议适配：解析路径/请求体/``If-Match``/``Idempotency-Key``，调用
应用用例，再把领域实体序列化为契约响应。权限与项目隔离由服务端依赖强制。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.deps import get_project_scope, get_write_scope, request_id
from app.api.v1.capture_session_schemas import (
    CaptureSessionActionRequest,
    CaptureSessionCreate,
    CaptureSessionOut,
)
from app.api.v1.collection_dependencies import CollectionUseCasesDep
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import PageOut
from app.application.collection import NewCaptureSession
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["collection"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]


@router.get("/capture-sessions", response_model=PageOut[CaptureSessionOut])
def list_capture_sessions(
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    task_id: UUID | None = None,
    batch_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[CaptureSessionOut]:
    page = use_cases.sessions.list_sessions(
        scope,
        status=status,
        task_id=task_id,
        batch_id=batch_id,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[CaptureSessionOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/capture-sessions", response_model=CaptureSessionOut, status_code=201)
def create_capture_session(
    payload: CaptureSessionCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> CaptureSessionOut:
    session = use_cases.sessions.create_session(
        scope,
        NewCaptureSession(**payload.model_dump()),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return CaptureSessionOut.from_domain(session)


@router.get("/capture-sessions/{session_id}", response_model=CaptureSessionOut)
def get_capture_session(
    session_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> CaptureSessionOut:
    return CaptureSessionOut.from_domain(use_cases.sessions.get_session(scope, session_id))


@router.post(
    "/capture-sessions/{session_id}/actions/{action}",
    response_model=CaptureSessionOut,
)
def act_on_capture_session(
    session_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: CaptureSessionActionRequest | None = None,
) -> CaptureSessionOut:
    session = use_cases.sessions.apply_action(
        scope,
        session_id,
        action,
        expected_version=expected_version,
        payload=_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return CaptureSessionOut.from_domain(session)


def _payload(payload: CaptureSessionActionRequest | None) -> dict[str, object]:
    if payload is None:
        return {}
    return payload.model_dump(exclude_unset=True)
