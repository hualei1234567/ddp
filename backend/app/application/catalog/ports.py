"""资产目录仓储端口。

实现方只需保证两件事：项目隔离由传入的 ``scope`` 强制（不信任调用方拼接
条件），以及前缀条件必须下推为 SQL（不得先取一页再在内存过滤）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.application.catalog.models import CatalogCursor, CatalogEntry
from app.domain.catalog.enums import AssetKind
from app.domain.catalog.search import SearchPrefix
from app.domain.identity.models import ProjectScope


class AssetCatalogRepository(Protocol):
    def search(
        self,
        scope: ProjectScope,
        *,
        prefix: SearchPrefix,
        kinds: frozenset[AssetKind] | None,
        after: CatalogCursor | None,
        limit: int,
    ) -> Sequence[CatalogEntry]:
        """按前缀检索多类资源，返回按 ``(updated_at DESC, kind, id)`` 排序的一页。

        ``limit`` 由应用层传入 ``limit + 1`` 用于判断是否还有下一页；实现不得
        放大该上限，也不得省略项目条件。
        """


__all__ = ["AssetCatalogRepository"]
