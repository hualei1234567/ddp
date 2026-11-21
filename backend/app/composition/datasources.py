"""数据源导入 → Episode 物化的生产装配。

端点（HTTP）与 Worker（导入成功事件）都走同一条物化链路：如果各自接线，
``build_episode_service`` 的协作者一改就会漂移出两套行为。本模块只做组合，
不含业务规则。
"""

from __future__ import annotations

from typing import cast

from sqlalchemy.orm import Session

from app.application.datasources.episode_materialization import ImportEpisodeMaterializer
from app.application.datasources.ports import (
    DataSourceImportRepository,
    MediaAssetRepository,
)
from app.composition.episodes import build_episode_service, build_readiness_service
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import SqlEpisodeRepository


def build_episode_materializer(session: Session) -> ImportEpisodeMaterializer:
    """导入产物 -> Episode 的物化用例。

    Episode 侧的 8 个协作者由 ``build_episode_service`` 统一装配，不在这里
    重抄一遍——重抄就多了一处会漂移的接线。
    """
    return ImportEpisodeMaterializer(
        imports=cast(DataSourceImportRepository, SqlDataSourceImportRepository(session)),
        assets=cast(MediaAssetRepository, SqlMediaAssetRepository(session)),
        episodes=build_episode_service(session),
        episode_repository=SqlEpisodeRepository(session),
        # 创建后立刻尝试自动就绪（Canonical Manifest 生成器），
        # 否则 Episode 永远停在 PENDING 等人手工提交 Manifest。
        readiness=build_readiness_service(session),
    )


__all__ = ["build_episode_materializer"]
