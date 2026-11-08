"""Episode/血缘/RawAsset 下载 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.api.v1.upload_dependencies import ObjectStorageDep, SettingsDep
from app.application.episodes import EpisodeService, LineageService
from app.application.ingestion import RawAssetDownloadUseCases, RawAssetUseCases
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository


@dataclass(frozen=True, slots=True)
class EpisodeUseCases:
    episodes: EpisodeService
    lineage: LineageService
    assets: RawAssetUseCases
    downloads: RawAssetDownloadUseCases


def get_episode_use_cases(
    session: DbSession,
    settings: SettingsDep,
    storage: ObjectStorageDep,
) -> EpisodeUseCases:
    episodes = SqlEpisodeRepository(session)
    episode_assets = SqlEpisodeRawAssetRepository(session)
    lineage = SqlLineageRepository(session)
    raw_assets = SqlRawAssetRepository(session)
    jobs = SqlIngestionJobRepository(session)
    audit = SqlAuditRepository(session)
    outbox = SqlOutboxRepository(session)
    idempotency = SqlIdempotencyRepository(session)
    return EpisodeUseCases(
        episodes=EpisodeService(
            episodes,
            episode_assets,
            lineage,
            raw_assets,
            jobs,
            audit,
            outbox,
            session,
            idempotency=idempotency,
        ),
        lineage=LineageService(lineage, audit, outbox, session),
        assets=RawAssetUseCases(raw_assets),
        downloads=RawAssetDownloadUseCases(
            raw_assets,
            storage,
            audit,
            session,
            url_ttl_seconds=settings.download_url_ttl_seconds,
        ),
    )


EpisodeUseCasesDep = Annotated[EpisodeUseCases, Depends(get_episode_use_cases)]
