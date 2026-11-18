"""统一资产目录只读 SQL 适配器（N5-001）。

设计
----
跨五张**既有**权威表做 ``UNION ALL``，每支只选择目录需要的最小列
（``kind`` / ``id`` / ``display_name`` / ``version_no`` / ``updated_at``），
不建宽表、不做全量物化。

索引与下推
----------
检索词被领域规范化为 ``[prefix, upper)`` 半开区间；每支在 ``project_id``
等值条件下追加该区间 + ``startswith`` 精确复核。这样：

- 项目隔离先由 ``project_id`` 索引前缀收窄，客户端传入的 ``project_id`` 不参与；
- 前缀区间可命中既有 B-tree 索引（如 ``uq_raw_assets_project_code`` /
  ``uq_episodes_project_code`` / ``uq_datasets_project_code``）；
- 不出现无界的 ``%keyword%``，不会退化为全表扫描；
- 每支自带 ``ORDER BY ... LIMIT``，外层再统一排序/截断，单页查询规模被
  ``limit + 1`` 严格约束。

DatasetVersion 没有自己的 code：以父 Dataset 的 ``code`` 前缀匹配，显示名
组合为 ``<dataset code> v<version>``；媒体资产以 ``episode_key`` 前缀匹配。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Integer,
    Select,
    and_,
    cast,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from app.application.catalog.models import CatalogCursor, CatalogEntry
from app.domain.catalog.enums import AssetKind
from app.domain.catalog.errors import DomainValidationError
from app.domain.catalog.search import SearchPrefix
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.datasets import DatasetModel, DatasetVersionModel
from app.infrastructure.db.models.datasources import MediaAssetModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.repositories._collection_sql import as_utc

_MAX_LIMIT = 101


class SqlAssetCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(
        self,
        scope: ProjectScope,
        *,
        prefix: SearchPrefix,
        kinds: frozenset[AssetKind] | None,
        after: CatalogCursor | None,
        limit: int,
    ) -> Sequence[CatalogEntry]:
        if limit < 1 or limit > _MAX_LIMIT:
            raise DomainValidationError(f"limit 必须在 1 到 {_MAX_LIMIT} 之间")
        selected = kinds if kinds is not None else frozenset(AssetKind)
        branches = [
            branch
            for kind, builder in _BRANCHES
            if kind in selected
            for branch in (builder(scope.project_id, prefix, after, limit),)
        ]
        if not branches:
            return ()
        merged = union_all(*branches).subquery("asset_catalog")
        statement = (
            select(
                merged.c.kind,
                merged.c.asset_id,
                merged.c.display_name,
                merged.c.version_no,
                merged.c.updated_at,
            )
            .order_by(
                merged.c.updated_at.desc(),
                merged.c.kind.asc(),
                merged.c.asset_id.asc(),
            )
            .limit(limit)
        )
        return [self._from_row(row) for row in self._session.execute(statement).all()]

    @staticmethod
    def _from_row(row: Any) -> CatalogEntry:
        display_name = str(row.display_name)
        if row.version_no is not None:
            display_name = f"{display_name} v{int(row.version_no)}"
        return CatalogEntry(
            kind=AssetKind(row.kind),
            asset_id=row.asset_id,
            display_name=display_name,
            updated_at=_require_utc(row.updated_at),
        )


def _require_utc(value: datetime) -> datetime:
    normalized = as_utc(value)
    if normalized is None:  # pragma: no cover - updated_at 在全部来源表均非空
        raise ValueError("updated_at 不能为空")
    return normalized


def _prefix_filter(column: ColumnElement[str], prefix: SearchPrefix) -> ColumnElement[bool]:
    conditions = [
        column >= prefix.value,
        column.startswith(prefix.value, autoescape=True),
    ]
    if prefix.upper_bound is not None:
        conditions.append(column < prefix.upper_bound)
    return and_(*conditions)


def _cursor_filter(
    updated_at: ColumnElement[datetime],
    asset_id: ColumnElement[UUID],
    kind: AssetKind,
    after: CatalogCursor | None,
) -> ColumnElement[bool] | None:
    """按全局排序 ``(updated_at DESC, kind ASC, asset_id ASC)`` 生成键集条件。

    分支的 ``kind`` 是常量，因此三个比较分支可以化简，避免在 SQL 里再比较
    ``kind`` 列（该列是 SELECT 字面量，不能用于 WHERE）。
    """
    if after is None:
        return None
    if kind.value > after.kind.value:
        return updated_at <= after.updated_at
    if kind.value == after.kind.value:
        return or_(
            updated_at < after.updated_at,
            and_(updated_at == after.updated_at, asset_id > after.asset_id),
        )
    return updated_at < after.updated_at


def _ordered(stmt: Select[Any], updated_at: Any, asset_id: Any, limit: int) -> Select[Any]:
    """单支先排序截断，再包成普通 SELECT 供 UNION ALL 使用。

    直接把带 LIMIT 的 SELECT 送进 ``union_all``，部分数据库要求每支加括号；
    包一层子查询后编译为 ``SELECT ... FROM (SELECT ... ORDER BY ... LIMIT n)``，
    SQLite 与 PostgreSQL 都接受，且截断语义不变。
    """
    limited = stmt.order_by(updated_at.desc(), asset_id.asc()).limit(limit).subquery()
    return select(*limited.c)


def _raw_asset_branch(
    project_id: UUID,
    prefix: SearchPrefix,
    after: CatalogCursor | None,
    limit: int,
) -> Any:
    kind = AssetKind.RAW_ASSET
    statement = select(
        literal(kind.value).label("kind"),
        RawAssetModel.id.label("asset_id"),
        RawAssetModel.original_filename.label("display_name"),
        cast(null(), Integer).label("version_no"),
        RawAssetModel.updated_at.label("updated_at"),
    ).where(
        RawAssetModel.project_id == project_id,
        _prefix_filter(RawAssetModel.code, prefix),
    )
    condition = _cursor_filter(RawAssetModel.updated_at, RawAssetModel.id, kind, after)
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(statement, RawAssetModel.updated_at, RawAssetModel.id, limit)


def _episode_branch(
    project_id: UUID,
    prefix: SearchPrefix,
    after: CatalogCursor | None,
    limit: int,
) -> Any:
    kind = AssetKind.EPISODE
    statement = select(
        literal(kind.value).label("kind"),
        EpisodeModel.id.label("asset_id"),
        EpisodeModel.code.label("display_name"),
        cast(null(), Integer).label("version_no"),
        EpisodeModel.updated_at.label("updated_at"),
    ).where(
        EpisodeModel.project_id == project_id,
        _prefix_filter(EpisodeModel.code, prefix),
    )
    condition = _cursor_filter(EpisodeModel.updated_at, EpisodeModel.id, kind, after)
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(statement, EpisodeModel.updated_at, EpisodeModel.id, limit)


def _dataset_branch(
    project_id: UUID,
    prefix: SearchPrefix,
    after: CatalogCursor | None,
    limit: int,
) -> Any:
    kind = AssetKind.DATASET
    statement = select(
        literal(kind.value).label("kind"),
        DatasetModel.id.label("asset_id"),
        DatasetModel.name.label("display_name"),
        cast(null(), Integer).label("version_no"),
        DatasetModel.updated_at.label("updated_at"),
    ).where(
        DatasetModel.project_id == project_id,
        _prefix_filter(DatasetModel.code, prefix),
    )
    condition = _cursor_filter(DatasetModel.updated_at, DatasetModel.id, kind, after)
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(statement, DatasetModel.updated_at, DatasetModel.id, limit)


def _dataset_version_branch(
    project_id: UUID,
    prefix: SearchPrefix,
    after: CatalogCursor | None,
    limit: int,
) -> Any:
    kind = AssetKind.DATASET_VERSION
    statement = (
        select(
            literal(kind.value).label("kind"),
            DatasetVersionModel.id.label("asset_id"),
            DatasetModel.code.label("display_name"),
            DatasetVersionModel.dataset_version.label("version_no"),
            DatasetVersionModel.updated_at.label("updated_at"),
        )
        .join(
            DatasetModel,
            and_(
                DatasetModel.project_id == DatasetVersionModel.project_id,
                DatasetModel.id == DatasetVersionModel.dataset_id,
            ),
        )
        .where(
            DatasetVersionModel.project_id == project_id,
            _prefix_filter(DatasetModel.code, prefix),
        )
    )
    condition = _cursor_filter(
        DatasetVersionModel.updated_at,
        DatasetVersionModel.id,
        kind,
        after,
    )
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(
        statement,
        DatasetVersionModel.updated_at,
        DatasetVersionModel.id,
        limit,
    )


def _media_asset_branch(
    project_id: UUID,
    prefix: SearchPrefix,
    after: CatalogCursor | None,
    limit: int,
) -> Any:
    kind = AssetKind.MEDIA_ASSET
    statement = select(
        literal(kind.value).label("kind"),
        MediaAssetModel.id.label("asset_id"),
        MediaAssetModel.path.label("display_name"),
        cast(null(), Integer).label("version_no"),
        MediaAssetModel.updated_at.label("updated_at"),
    ).where(
        MediaAssetModel.project_id == project_id,
        _prefix_filter(MediaAssetModel.episode_key, prefix),
    )
    condition = _cursor_filter(MediaAssetModel.updated_at, MediaAssetModel.id, kind, after)
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(statement, MediaAssetModel.updated_at, MediaAssetModel.id, limit)


_BRANCHES = (
    (AssetKind.RAW_ASSET, _raw_asset_branch),
    (AssetKind.EPISODE, _episode_branch),
    (AssetKind.DATASET, _dataset_branch),
    (AssetKind.DATASET_VERSION, _dataset_version_branch),
    (AssetKind.MEDIA_ASSET, _media_asset_branch),
)


__all__ = ["SqlAssetCatalogRepository"]
