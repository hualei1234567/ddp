"""ExportJob HTTP 路由：创建、列表、详情、动作、产物清单与下载。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.dataset_response_models import (
    ExportArtifactDownloadOut,
    ExportArtifactManifestOut,
    ExportJobOut,
)
from app.api.v1.export_dependencies import (
    ExpectedVersion,
    ExportUseCasesDep,
    IdempotencyKey,
    ReadScope,
    RequiredIdempotencyKey,
    WriteScope,
    request_id,
)
from app.api.v1.export_schemas import (
    ExportActionRequest,
    ExportJobCreate,
    export_job_out,
)
from app.api.v1.schemas import PageOut
from app.api.v1.training_dependencies import ExportDownloadScope, ExportGrantScope
from app.application.datasets.commands import NewExportJob
from app.domain.datasets.enums import ExportFormat, ExportJobStatus
from app.domain.datasets.errors import DomainValidationError

router = APIRouter(prefix="/projects/{project_id}/exports", tags=["exports"])


@router.get("", response_model=PageOut[ExportJobOut])
def list_export_jobs(
    scope: ReadScope,
    use_cases: ExportUseCasesDep,
    dataset_version_id: UUID | None = None,
    status: str | None = None,
    format: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.list(
        scope,
        dataset_version_id=dataset_version_id,
        status=_status_filter(status),
        format=_format_filter(format),
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.export_job_id)
    return {
        "items": [export_job_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("", response_model=ExportJobOut, status_code=202)
def create_export_job(
    payload: ExportJobCreate,
    request: Request,
    scope: ExportGrantScope,
    use_cases: ExportUseCasesDep,
    idempotency_key: RequiredIdempotencyKey,
) -> dict[str, Any]:
    job = use_cases.create(
        scope,
        NewExportJob(
            dataset_version_id=payload.dataset_version_id,
            format=payload.format,
            format_version=payload.format_version,
            exporter_id=payload.exporter_id,
            exporter_version=payload.exporter_version,
            mapping_version=payload.mapping_version,
            config=payload.config,
            timeout_seconds=payload.timeout_seconds,
            max_attempts=payload.max_attempts,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return export_job_out(job)


@router.get("/{export_job_id}", response_model=ExportJobOut)
def get_export_job(
    export_job_id: UUID,
    scope: ReadScope,
    use_cases: ExportUseCasesDep,
) -> dict[str, Any]:
    return export_job_out(use_cases.get(scope, export_job_id))


@router.post("/{export_job_id}/actions/{action}", response_model=ExportJobOut)
def act_on_export_job(
    export_job_id: UUID,
    action: str,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: ExportUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: ExportActionRequest | None = None,
) -> dict[str, Any]:
    job = use_cases.apply_action(
        scope,
        export_job_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    response.status_code = 202 if action == "retry" else 200
    return export_job_out(job)


@router.get("/{export_job_id}/artifacts", response_model=ExportArtifactManifestOut)
def get_export_artifacts(
    export_job_id: UUID,
    scope: ExportDownloadScope,
    use_cases: ExportUseCasesDep,
) -> dict[str, Any]:
    return use_cases.artifact_manifest(scope, export_job_id)


@router.get(
    "/{export_job_id}/artifacts/{artifact_path:path}/download",
    response_model=ExportArtifactDownloadOut,
)
def download_export_artifact(
    export_job_id: UUID,
    artifact_path: str,
    request: Request,
    scope: ExportDownloadScope,
    use_cases: ExportUseCasesDep,
) -> dict[str, Any]:
    return use_cases.artifact_download(
        scope,
        export_job_id,
        artifact_path,
        request_id=request_id(request),
    )


def _status_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ExportJobStatus(value).value
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _format_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ExportFormat(value).value
    except ValueError as exc:
        raise DomainValidationError("format 取值非法") from exc
