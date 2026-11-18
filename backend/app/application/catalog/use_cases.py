"""统一资产目录只读检索用例（N5-001）。

范围
----
跨越 ``raw_assets`` / ``episodes`` / ``datasets`` / ``dataset_versions`` /
``data_source_media_assets`` 五张**既有**权威表按标识前缀检索，返回统一最小
条目。不建宽表、不新增表、不引入搜索引擎，也不写任何一行。

分页
----
复用采集域的 ``Page`` 与 ``normalize_limit``（同一套 1..100 上限），但排序键
是复合的 ``(updated_at DESC, kind ASC, id ASC)``，因此游标由本域编解码，见
``cursor.py``。
"""

from __future__ import annotations

from app.application.catalog.cursor import cursor_for, decode_cursor, encode_cursor
from app.application.catalog.models import CatalogEntry
from app.application.catalog.ports import AssetCatalogRepository
from app.application.collection.pagination import Page, normalize_limit
from app.domain.catalog.enums import AssetKind
from app.domain.catalog.errors import DomainValidationError
from app.domain.catalog.search import SearchPrefix
from app.domain.collection.errors import (
    DomainValidationError as PaginationValidationError,
)
from app.domain.identity.models import ProjectScope


class AssetCatalogUseCases:
    def __init__(self, catalog: AssetCatalogRepository) -> None:
        self._catalog = catalog

    def search(
        self,
        scope: ProjectScope,
        *,
        q: str,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[CatalogEntry]:
        try:
            bounded = normalize_limit(limit)
        except PaginationValidationError as exc:
            # 把分页工具的错误收敛到本域错误类型，保证 HTTP 层映射到 422
            # 而不是落到未处理异常；路由层已用 Query(ge/le) 先行拦截。
            raise DomainValidationError(str(exc)) from exc
        prefix = SearchPrefix.parse(q)
        after = decode_cursor(cursor)
        items = list(
            self._catalog.search(
                scope,
                prefix=prefix,
                kinds=_kind_filter(kind),
                after=after,
                limit=bounded + 1,
            )
        )
        if len(items) <= bounded:
            return Page(items=items, next_cursor=None)
        visible = items[:bounded]
        return Page(items=visible, next_cursor=encode_cursor(cursor_for(visible[-1])))


def _kind_filter(value: str | None) -> frozenset[AssetKind] | None:
    if value is None:
        return None
    try:
        return frozenset({AssetKind(value)})
    except ValueError as exc:
        raise DomainValidationError("kind 取值非法") from exc


__all__ = ["AssetCatalogUseCases"]
