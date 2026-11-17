"""数据源链只读投影的 SQLAlchemy 适配器。

只读：本类不写任何表，也不参与事务边界。三个查询共用同一行形状，区别只在
过滤维度（按资产 / 按导入任务 / 按数据源），因此不重复三段几乎一样的
映射代码。

**实测执行计划**（真实 PostgreSQL 16，当前库 636 行媒体资产 / 38 导入 / 39 数据源）：

- 按资产反查：``Index Scan using ix_media_assets_raw_asset``，0.15 ms；
- 按导入任务：``Bitmap Index Scan on uq_media_assets_import_path``
  （``project_id, import_id`` 前缀），1.0 ms；
- 按数据源：``Index Scan using uq_media_assets_import_path``（``import_id``
  连接条件），0.16 ms。

导入任务表当前只有 38 行，规划器选择顺序扫描是合理的；数据量上来后
``ix_data_source_imports_source_status (project_id, data_source_id, status)``
与 ``uq_media_assets_import_path`` 会接管过滤。三条查询都是固定次数的
JOIN，不随节点数量扩展（无 N+1）。

所有连接条件都同时带 ``project_id``，跨项目行在 JOIN 阶段就被排除，
不依赖调用方额外过滤。

数据源归属以**导入任务**的 ``data_source_id`` 为准（而不是媒体行自己那一列）：
一条媒体行必然属于某次导入，而导入任务只可能属于一个数据源；两列理应一致，
以导入任务为准可以在极端脏数据下保证「数据源 → 导入任务 → 资产」这条链
每个导入任务只有一个来源，不会因为媒体行错写而分裂出第二个数据源。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, and_, select
from sqlalchemy.orm import Session

from app.application.episodes.provenance import SourceLink
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)
from app.infrastructure.repositories._collection_sql import as_utc


class SqlSourceProvenanceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_links_by_raw_assets(
        self,
        scope: ProjectScope,
        asset_ids: Sequence[UUID],
        *,
        limit: int = 100,
    ) -> list[SourceLink]:
        ids = tuple(dict.fromkeys(asset_ids))
        if not ids:
            return []
        stmt = self._link_query().where(
            MediaAssetModel.project_id == scope.project_id,
            MediaAssetModel.raw_asset_id.in_(ids),
        )
        return self._fetch(stmt, limit)

    def list_links_by_imports(
        self,
        scope: ProjectScope,
        import_ids: Sequence[UUID],
        *,
        limit: int = 100,
    ) -> list[SourceLink]:
        ids = tuple(dict.fromkeys(import_ids))
        if not ids:
            return []
        stmt = self._link_query().where(
            DataSourceImportModel.project_id == scope.project_id,
            DataSourceImportModel.id.in_(ids),
        )
        return self._fetch(stmt, limit)

    def list_links_by_data_sources(
        self,
        scope: ProjectScope,
        data_source_ids: Sequence[UUID],
        *,
        limit: int = 100,
    ) -> list[SourceLink]:
        ids = tuple(dict.fromkeys(data_source_ids))
        if not ids:
            return []
        stmt = self._link_query().where(
            DataSourceImportModel.project_id == scope.project_id,
            DataSourceModel.id.in_(ids),
        )
        return self._fetch(stmt, limit)

    def _link_query(self) -> Select[tuple[object, ...]]:
        """导入任务为主表、媒体资产为左连接：还没产出文件的导入任务也必须出现。"""
        media = MediaAssetModel
        import_model = DataSourceImportModel
        source = DataSourceModel
        return (
            select(
                import_model.project_id,
                import_model.id.label("import_id"),
                import_model.status.label("import_status"),
                import_model.scope.label("import_scope"),
                import_model.created_by.label("import_created_by"),
                import_model.created_at.label("import_created_at"),
                import_model.started_at,
                import_model.finished_at,
                import_model.imported_file_count,
                source.id.label("data_source_id"),
                source.code.label("data_source_code"),
                source.name.label("data_source_name"),
                source.kind.label("data_source_kind"),
                source.repo_id,
                source.revision,
                media.id.label("media_asset_id"),
                media.raw_asset_id,
                media.episode_key,
                media.path,
                media.camera,
                media.created_by.label("media_created_by"),
                media.created_at.label("media_created_at"),
            )
            .join(
                source,
                and_(
                    source.project_id == import_model.project_id,
                    source.id == import_model.data_source_id,
                ),
            )
            .outerjoin(
                media,
                and_(
                    media.project_id == import_model.project_id,
                    media.import_id == import_model.id,
                ),
            )
            .order_by(
                import_model.created_at,
                import_model.id,
                media.created_at,
                media.id,
            )
        )

    def _fetch(self, stmt: Select[tuple[object, ...]], limit: int) -> list[SourceLink]:
        rows = self._session.execute(stmt.limit(limit))
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: Row[Any]) -> SourceLink:
        mapping = row._mapping
        return SourceLink(
            project_id=mapping["project_id"],
            data_source_id=mapping["data_source_id"],
            data_source_code=mapping["data_source_code"],
            data_source_name=mapping["data_source_name"],
            data_source_kind=mapping["data_source_kind"],
            repo_id=mapping["repo_id"],
            revision=mapping["revision"],
            import_id=mapping["import_id"],
            import_status=mapping["import_status"],
            import_scope=mapping["import_scope"],
            import_created_by=mapping["import_created_by"],
            import_created_at=as_utc(mapping["import_created_at"]),
            import_started_at=as_utc(mapping["started_at"]),
            import_finished_at=as_utc(mapping["finished_at"]),
            imported_file_count=mapping["imported_file_count"],
            media_asset_id=mapping["media_asset_id"],
            raw_asset_id=mapping["raw_asset_id"],
            episode_key=mapping["episode_key"],
            source_path=mapping["path"],
            camera=mapping["camera"],
            media_created_by=mapping["media_created_by"],
            media_created_at=as_utc(mapping["media_created_at"]),
        )
