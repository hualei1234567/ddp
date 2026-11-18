"""统一资产目录 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.catalog import AssetCatalogUseCases
from app.infrastructure.repositories.asset_catalog import SqlAssetCatalogRepository


@dataclass(frozen=True, slots=True)
class AssetCatalogServices:
    catalog: AssetCatalogUseCases


def get_asset_catalog_services(session: DbSession) -> AssetCatalogServices:
    return AssetCatalogServices(
        catalog=AssetCatalogUseCases(SqlAssetCatalogRepository(session)),
    )


AssetCatalogServicesDep = Annotated[
    AssetCatalogServices,
    Depends(get_asset_catalog_services),
]

__all__ = ["AssetCatalogServices", "AssetCatalogServicesDep"]
