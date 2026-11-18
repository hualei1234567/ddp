"""统一资产目录 HTTP 响应模型。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.application.catalog.models import CatalogEntry
from app.domain.catalog.enums import AssetKind


class AssetCatalogEntryOut(BaseModel):
    """跨资源目录条目：只暴露定位与展示所需的最小字段。"""

    kind: AssetKind
    id: UUID
    display_name: str
    updated_at: datetime

    @classmethod
    def from_domain(cls, entry: CatalogEntry) -> AssetCatalogEntryOut:
        return cls(
            kind=entry.kind,
            id=entry.asset_id,
            display_name=entry.display_name,
            updated_at=entry.updated_at,
        )


__all__ = ["AssetCatalogEntryOut"]
