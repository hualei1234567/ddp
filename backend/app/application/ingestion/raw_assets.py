"""RawAsset 查询用例：项目范围、列表筛选与详情。"""

from __future__ import annotations

from uuid import UUID

from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.ingestion.ports import RawAssetRepository
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetStatus
from app.domain.ingestion.errors import DomainValidationError, ResourceNotFoundError
from app.domain.ingestion.models import RawAsset


class RawAssetUseCases:
    def __init__(self, assets: RawAssetRepository) -> None:
        self._assets = assets

    def list_assets(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        collection_task_id: UUID | None = None,
        format: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[RawAsset]:
        bounded = normalize_limit(limit)
        items = self._assets.list(
            scope,
            status=_asset_status(status),
            collection_task_id=collection_task_id,
            format=format,
            after_id=decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, id_of=lambda item: item.asset_id)

    def get_asset(self, scope: ProjectScope, asset_id: UUID) -> RawAsset:
        asset = self._assets.get(scope, asset_id)
        if asset is None:
            raise ResourceNotFoundError("原始资产", asset_id)
        return asset


def _asset_status(value: str | None) -> RawAssetStatus | None:
    if value is None:
        return None
    try:
        return RawAssetStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc
