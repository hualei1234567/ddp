"""上传会话链只读投影的 SQLAlchemy 适配器。

只读：本类不写任何表，也不参与事务边界。两个查询共用同一行形状，区别只在
过滤维度（按资产 / 按会话）。

所有连接条件都同时带 ``project_id``，跨项目行在 JOIN 阶段就被排除，不依赖
调用方额外过滤。已 ``REVOKED`` 的链接不产边：撤销的使用关系不是"会话登记了
该资产"的有效事实，把撤销行投影成边会让血缘图声称一个已不成立的归属。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, and_, select
from sqlalchemy.orm import Session

from app.application.episodes.session_provenance import SessionLink
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.ingestion import (
    RawAssetLinkModel,
    UploadSessionModel,
)
from app.infrastructure.repositories._collection_sql import as_utc

# 只投影"未撤销"的使用关系；撤销语义见模块文档。
_ACTIVE_LINK_STATUS = "REVOKED"


class SqlSessionProvenanceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_links_by_assets(
        self,
        scope: ProjectScope,
        asset_ids: Sequence[UUID],
        *,
        limit: int = 100,
    ) -> list[SessionLink]:
        ids = tuple(dict.fromkeys(asset_ids))
        if not ids:
            return []
        stmt = self._link_query().where(
            RawAssetLinkModel.project_id == scope.project_id,
            RawAssetLinkModel.asset_id.in_(ids),
        )
        return self._fetch(stmt, limit)

    def list_links_by_sessions(
        self,
        scope: ProjectScope,
        session_ids: Sequence[UUID],
        *,
        limit: int = 100,
    ) -> list[SessionLink]:
        ids = tuple(dict.fromkeys(session_ids))
        if not ids:
            return []
        stmt = self._link_query().where(
            RawAssetLinkModel.project_id == scope.project_id,
            RawAssetLinkModel.upload_session_id.in_(ids),
        )
        return self._fetch(stmt, limit)

    def session_exists(self, scope: ProjectScope, session_id: UUID) -> bool:
        row = self._session.scalar(
            select(UploadSessionModel.id).where(
                UploadSessionModel.project_id == scope.project_id,
                UploadSessionModel.id == session_id,
            )
        )
        return row is not None

    def _link_query(self) -> Select[tuple[object, ...]]:
        link = RawAssetLinkModel
        session = UploadSessionModel
        return (
            select(
                link.project_id,
                link.asset_id,
                link.upload_session_id,
                link.id.label("link_id"),
                link.link_status,
                link.created_by,
                link.created_at,
                session.status.label("session_status"),
                session.original_filename.label("session_original_filename"),
                session.capture_session_id,
            )
            .join(
                session,
                and_(
                    session.project_id == link.project_id,
                    session.id == link.upload_session_id,
                ),
            )
            .where(
                link.upload_session_id.is_not(None),
                link.link_status != _ACTIVE_LINK_STATUS,
            )
            .order_by(link.created_at, link.id)
        )

    def _fetch(self, stmt: Select[tuple[object, ...]], limit: int) -> list[SessionLink]:
        rows = self._session.execute(stmt.limit(limit))
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: Row[Any]) -> SessionLink:
        mapping = row._mapping
        upload_session_id = mapping["upload_session_id"]
        if upload_session_id is None:
            # 查询已过滤空值；走到这里说明过滤条件被改坏了，不能静默构造。
            raise ValueError("raw_asset_links.upload_session_id 不应为空")
        return SessionLink(
            project_id=mapping["project_id"],
            asset_id=mapping["asset_id"],
            upload_session_id=upload_session_id,
            link_id=mapping["link_id"],
            link_status=mapping["link_status"],
            session_status=mapping["session_status"],
            session_original_filename=mapping["session_original_filename"],
            capture_session_id=mapping["capture_session_id"],
            created_by=mapping["created_by"],
            created_at=as_utc(mapping["created_at"]),
        )


__all__ = ["SqlSessionProvenanceRepository"]
