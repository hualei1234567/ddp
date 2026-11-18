"""统一资产目录领域（N5-001）。

只包含纯 Python 规则：资产类型枚举、检索词前缀校验与游标排序所需的稳定顺序。
不依赖 Web、ORM、队列或云 SDK。
"""

from app.domain.catalog.enums import AssetKind
from app.domain.catalog.errors import CatalogDomainError, DomainValidationError
from app.domain.catalog.search import SearchPrefix

__all__ = [
    "AssetKind",
    "CatalogDomainError",
    "DomainValidationError",
    "SearchPrefix",
]
