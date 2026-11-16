"""Episode/血缘/RawAsset 下载 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.api.v1.upload_dependencies import ObjectStorageDep, SettingsDep
from app.application.episodes import EpisodeService, LineageService
from app.application.episodes.readiness import EpisodeReadinessService
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


def build_episode_service(
    session: DbSession,
    *,
    with_idempotency: bool = True,
) -> EpisodeService:
    """装配 ``EpisodeService``；需要它的模块统一调用本函数。

    ``episode_dependencies`` 与数据源导入的物化端点都要这个服务。抽出来
    只装配一次，避免两处接线各自演化——那会变成"同一服务两套依赖"。
    """
    return EpisodeService(
        SqlEpisodeRepository(session),
        SqlEpisodeRawAssetRepository(session),
        SqlLineageRepository(session),
        SqlRawAssetRepository(session),
        SqlIngestionJobRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
        idempotency=SqlIdempotencyRepository(session) if with_idempotency else None,
    )


def build_readiness_service(session: DbSession) -> EpisodeReadinessService:
    """装配自动就绪用例；与 ``build_episode_service`` 同理只接线一次。

    导入物化端点要用它（创建 Episode 后立刻尝试就绪），因此和
    ``build_episode_service`` 一样按请求装配，不跨请求共享。
    """
    return EpisodeReadinessService(
        SqlEpisodeRepository(session),
        SqlEpisodeRawAssetRepository(session),
        SqlRawAssetRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
    )


def get_episode_use_cases(
    session: DbSession,
    settings: SettingsDep,
    storage: ObjectStorageDep,
) -> EpisodeUseCases:
    lineage = SqlLineageRepository(session)
    raw_assets = SqlRawAssetRepository(session)
    audit = SqlAuditRepository(session)
    outbox = SqlOutboxRepository(session)
    return EpisodeUseCases(
        episodes=build_episode_service(session),
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
