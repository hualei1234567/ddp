"""跨模块只读校验：片段引用的 RawAsset 是否存在。

只做存在性查询，不导入其他领域内部实现，也不返回业务字段。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models.ingestion import RawAssetModel


class SqlRawAssetLookup:
    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, project_id: UUID, raw_asset_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(RawAssetModel.id).where(
                    RawAssetModel.id == raw_asset_id,
                    RawAssetModel.project_id == project_id,
                )
            )
            is not None
        )
