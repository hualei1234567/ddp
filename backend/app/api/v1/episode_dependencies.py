"""Episode/血缘/RawAsset 下载 API 依赖装配。

``build_episode_service`` / ``build_readiness_service`` 的实现在组合根
``app.composition.episodes``（Worker 也要用，不能让 Worker 反向依赖 API 层）；
本模块继续 re-export，既有调用点无需改 import。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.api.v1.upload_dependencies import ObjectStorageDep, SettingsDep
from app.application.episodes import EpisodeService, LineageGraphService, LineageService
from app.application.episodes.provenance import SourceProvenanceResolver
from app.application.episodes.session_provenance import SessionProvenanceResolver
from app.application.ingestion import RawAssetDownloadUseCases, RawAssetUseCases
from app.composition.episodes import (
    build_episode_service as build_episode_service,
)
from app.composition.episodes import (
    build_readiness_service as build_readiness_service,
)
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.datasource_provenance import (
    SqlSourceProvenanceRepository,
)
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.infrastructure.repositories.session_provenance import (
    SqlSessionProvenanceRepository,
)
from app.infrastructure.repositories.video_frames import SqlVideoFrameRepository


@dataclass(frozen=True, slots=True)
class EpisodeUseCases:
    episodes: EpisodeService
    lineage: LineageService
    lineage_graph: LineageGraphService
    assets: RawAssetUseCases
    downloads: RawAssetDownloadUseCases
    # 上传会话是只读投影节点：图查询以它为根之前，必须先用权威仓储确认
    # "这个会话存在且属于当前项目"，否则跨项目探测会变成存在性探针。
    sessions: SqlSessionProvenanceRepository | None = None
    video_frames: SqlVideoFrameRepository | None = None


def build_lineage_service(session: DbSession) -> LineageService:
    """装配血缘用例（含数据源链只读投影）。

    血缘端点与数据源血缘端点共用本函数，避免两处接线各自演化。
    投影依赖两个只读适配器：数据源链
    （``SqlSourceProvenanceRepository``）与资产来源类型
    （``SqlRawAssetRepository``），都不落库。
    """
    return LineageService(
        SqlLineageRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
        SourceProvenanceResolver(
            SqlSourceProvenanceRepository(session),
            SqlRawAssetRepository(session),
        ),
        SessionProvenanceResolver(SqlSessionProvenanceRepository(session)),
    )


def build_lineage_graph_service(session: DbSession) -> LineageGraphService:
    """装配通用图查询用例（N5-003）。

    与 :func:`build_lineage_service` 共用同一套只读适配器；图查询本身不写
    血缘表，因此不需要审计/outbox/事务端口。
    """
    return LineageGraphService(
        SqlLineageRepository(session),
        SourceProvenanceResolver(
            SqlSourceProvenanceRepository(session),
            SqlRawAssetRepository(session),
        ),
        SessionProvenanceResolver(SqlSessionProvenanceRepository(session)),
    )


def get_episode_use_cases(
    session: DbSession,
    settings: SettingsDep,
    storage: ObjectStorageDep,
) -> EpisodeUseCases:
    raw_assets = SqlRawAssetRepository(session)
    audit = SqlAuditRepository(session)
    return EpisodeUseCases(
        episodes=build_episode_service(session),
        lineage=build_lineage_service(session),
        lineage_graph=build_lineage_graph_service(session),
        assets=RawAssetUseCases(raw_assets),
        downloads=RawAssetDownloadUseCases(
            raw_assets,
            storage,
            audit,
            session,
            url_ttl_seconds=settings.download_url_ttl_seconds,
        ),
        sessions=SqlSessionProvenanceRepository(session),
        video_frames=SqlVideoFrameRepository(session),
    )


EpisodeUseCasesDep = Annotated[EpisodeUseCases, Depends(get_episode_use_cases)]
