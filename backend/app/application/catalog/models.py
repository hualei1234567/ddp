"""统一资产目录读模型。

目录条目是**只读投影**：不落库、不参与状态机，也不反向修改任何权威实体。
每个字段都来自权威表的既有列，避免演变成第二份业务真相。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.catalog.enums import AssetKind


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """跨资源检索的一条命中；``asset_id`` 是该类型权威表的主键。"""

    kind: AssetKind
    asset_id: UUID
    display_name: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CatalogCursor:
    """复合键集游标：``(updated_at DESC, kind ASC, asset_id ASC)``。"""

    updated_at: datetime
    kind: AssetKind
    asset_id: UUID


__all__ = ["CatalogCursor", "CatalogEntry"]
