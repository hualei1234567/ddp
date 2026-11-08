"""IngestionJob API 依赖装配。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.ingestion import IngestionJobService
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository


def get_ingestion_job_use_cases(session: DbSession) -> IngestionJobService:
    return IngestionJobService(
        SqlIngestionJobRepository(session),
        SqlRawAssetRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
        idempotency=SqlIdempotencyRepository(session),
    )


IngestionJobUseCasesDep = Annotated[
    IngestionJobService,
    Depends(get_ingestion_job_use_cases),
]
