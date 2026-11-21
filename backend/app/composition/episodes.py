"""Episode 用例的生产装配（HTTP 与 Worker 共用）。

装配函数属于组合根，不属于 HTTP 适配层：Worker 也需要 ``EpisodeService`` 与
``EpisodeReadinessService``（导入成功后自动物化 Episode），如果这些函数留在
``app/api/v1``，Worker 就得反向依赖 API 模块——那正是分层要禁止的方向。

只放"把端口接到具体适配器"的代码；任何业务判断都不在这里。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.application.episodes import EpisodeService
from app.application.episodes.readiness import EpisodeReadinessService
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.ingestion_directory import SqlCollectionDirectory
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository


def build_episode_service(
    session: Session,
    *,
    with_idempotency: bool = True,
) -> EpisodeService:
    """装配 ``EpisodeService``；需要它的模块统一调用本函数。

    API 端点与数据源导入的物化用例都要这个服务。抽出来只装配一次，避免
    两处接线各自演化——那会变成"同一服务两套依赖"。
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
        # 同一个 readiness 服务同时供 HTTP ready 的门禁使用：两条路径共用
        # 一套判定，避免"自动就绪被拒、手工 ready 放行"的口径分裂。
        readiness=build_readiness_service(session),
        # 会话归属校验复用上传链路同一个存在性适配器，不另写一份项目过滤。
        capture_sessions=SqlCollectionDirectory(session),
    )


def build_readiness_service(session: Session) -> EpisodeReadinessService:
    """装配自动就绪用例；与 ``build_episode_service`` 同理只接线一次。

    导入物化（HTTP 端点与 Worker）要用它，因此和 ``build_episode_service``
    一样按会话装配，不跨请求/事件共享。
    """
    return EpisodeReadinessService(
        SqlEpisodeRepository(session),
        SqlEpisodeRawAssetRepository(session),
        SqlRawAssetRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
    )


__all__ = ["build_episode_service", "build_readiness_service"]
