"""ExportJob API 依赖装配。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.api.v1.dataset_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.preconditions import require_idempotency_key
from app.api.v1.upload_dependencies import ObjectStorageDep, SettingsDep
from app.application.datasets.exports import ExportJobUseCases
from app.application.datasets.quality_gate import EpisodeQualityGate
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetIdempotencyRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.dataset_repositories import (
    SqlDatasetItemRepository,
    SqlDatasetRepository,
    SqlDatasetVersionRepository,
)
from app.infrastructure.repositories.export_jobs import SqlExportJobRepository
from app.infrastructure.repositories.quality import SqlQualityRunRepository

RequiredIdempotencyKey = Annotated[str, Depends(require_idempotency_key)]


def get_export_use_cases(
    session: DbSession,
    settings: SettingsDep,
    storage: ObjectStorageDep,
) -> ExportJobUseCases:
    return ExportJobUseCases(
        datasets=SqlDatasetRepository(session),
        versions=SqlDatasetVersionRepository(session),
        items=SqlDatasetItemRepository(session),
        jobs=SqlExportJobRepository(session),
        storage=storage,
        quality_gate=EpisodeQualityGate(SqlQualityRunRepository(session)),
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        idempotency=SqlDatasetIdempotencyRepository(session),
        uow=session,
        output_prefix_root=settings.export_storage_prefix,
        default_timeout_seconds=settings.export_default_timeout_seconds,
        max_timeout_seconds=settings.export_max_timeout_seconds,
        default_max_attempts=settings.export_default_max_attempts,
        max_attempts_limit=settings.export_max_attempts_limit,
        artifact_chunk_size_bytes=settings.export_artifact_chunk_size_bytes,
        download_url_ttl_seconds=settings.download_url_ttl_seconds,
    )


ExportUseCasesDep = Annotated[ExportJobUseCases, Depends(get_export_use_cases)]

__all__ = [
    "ExpectedVersion",
    "ExportUseCasesDep",
    "IdempotencyKey",
    "ReadScope",
    "RequiredIdempotencyKey",
    "WriteScope",
    "request_id",
]
