"""采集会话产物只读仓储（N9-008）。

跨 ``raw_assets`` 与 ``episodes`` 两张**既有**权威表做 ``UNION ALL``，
每支只选择列表需要的最小列（``kind`` / ``artifact_id`` / ``code`` /
``status`` / ``created_at``），不建宽表、不做全量物化。

项目隔离与索引下推
------------------
- 每支都先按 ``project_id`` 等值收窄，再按 ``capture_session_id`` 等值过滤，
  命中迁移 0068 新建的 ``(project_id, capture_session_id)`` 索引；
- 键集游标在每支内部下推，单页查询规模被 ``limit + 1`` 严格约束；
- 客户端传入的 ``project_id`` 只用于定位，服务端从 ``ProjectScope`` 取权威值。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from app.domain.collection.artifacts import (
    ArtifactCursor,
    CaptureSessionArtifact,
    CaptureSessionArtifactKind,
)
from app.domain.collection.errors import DomainValidationError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.repositories._collection_sql import as_utc

_MAX_LIMIT = 101


class SqlCaptureSessionArtifactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(
        self,
        scope: ProjectScope,
        *,
        session_id: UUID,
        after: ArtifactCursor | None = None,
        limit: int = 20,
    ) -> Sequence[CaptureSessionArtifact]:
        if limit < 1 or limit > _MAX_LIMIT:
            raise DomainValidationError(f"limit 必须在 1 到 {_MAX_LIMIT} 之间")
        branches = [
            _raw_asset_branch(scope.project_id, session_id, after, limit),
            _episode_branch(scope.project_id, session_id, after, limit),
        ]
        merged = union_all(*branches).subquery("capture_session_artifacts")
        statement = (
            select(
                merged.c.kind,
                merged.c.artifact_id,
                merged.c.code,
                merged.c.status,
                merged.c.created_at,
            )
            .order_by(
                merged.c.created_at.desc(),
                merged.c.kind.asc(),
                merged.c.artifact_id.asc(),
            )
            .limit(limit)
        )
        return [self._from_row(row) for row in self._session.execute(statement).all()]

    @staticmethod
    def _from_row(row: Any) -> CaptureSessionArtifact:
        created_at = as_utc(row.created_at)
        if created_at is None:  # pragma: no cover - created_at 在两张来源表均非空
            raise ValueError("created_at 不能为空")
        return CaptureSessionArtifact(
            kind=CaptureSessionArtifactKind(row.kind),
            artifact_id=row.artifact_id,
            code=str(row.code),
            status=str(row.status),
            created_at=created_at,
        )


def _cursor_filter(
    created_at: ColumnElement[datetime],
    artifact_id: ColumnElement[UUID],
    kind: CaptureSessionArtifactKind,
    after: ArtifactCursor | None,
) -> ColumnElement[bool] | None:
    """按全局排序 ``(created_at DESC, kind ASC, artifact_id ASC)`` 生成键集条件。

    分支的 ``kind`` 是常量，因此三个比较分支可以化简，避免在 SQL 里再比较
    该列（它是 SELECT 字面量，不能用于 WHERE）。
    """
    if after is None:
        return None
    if kind.value > after.kind.value:
        return created_at <= after.created_at
    if kind.value == after.kind.value:
        return or_(
            created_at < after.created_at,
            and_(created_at == after.created_at, artifact_id > after.artifact_id),
        )
    return created_at < after.created_at


def _ordered(stmt: Select[Any], created_at: Any, artifact_id: Any, limit: int) -> Select[Any]:
    """单支先排序截断，再包成普通 SELECT 供 UNION ALL 使用。

    直接把带 LIMIT 的 SELECT 送进 ``union_all``，部分数据库要求每支加括号；
    包一层子查询后编译为 ``SELECT ... FROM (SELECT ... ORDER BY ... LIMIT n)``，
    SQLite 与 PostgreSQL 都接受，且截断语义不变。
    """
    limited = stmt.order_by(created_at.desc(), artifact_id.asc()).limit(limit).subquery()
    return select(*limited.c)


def _raw_asset_branch(
    project_id: UUID,
    session_id: UUID,
    after: ArtifactCursor | None,
    limit: int,
) -> Any:
    kind = CaptureSessionArtifactKind.RAW_ASSET
    statement = select(
        literal(kind.value).label("kind"),
        RawAssetModel.id.label("artifact_id"),
        RawAssetModel.code.label("code"),
        RawAssetModel.status.label("status"),
        RawAssetModel.created_at.label("created_at"),
    ).where(
        RawAssetModel.project_id == project_id,
        RawAssetModel.capture_session_id == session_id,
    )
    condition = _cursor_filter(RawAssetModel.created_at, RawAssetModel.id, kind, after)
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(statement, RawAssetModel.created_at, RawAssetModel.id, limit)


def _episode_branch(
    project_id: UUID,
    session_id: UUID,
    after: ArtifactCursor | None,
    limit: int,
) -> Any:
    kind = CaptureSessionArtifactKind.EPISODE
    statement = select(
        literal(kind.value).label("kind"),
        EpisodeModel.id.label("artifact_id"),
        EpisodeModel.code.label("code"),
        EpisodeModel.status.label("status"),
        EpisodeModel.created_at.label("created_at"),
    ).where(
        EpisodeModel.project_id == project_id,
        EpisodeModel.capture_session_id == session_id,
    )
    condition = _cursor_filter(EpisodeModel.created_at, EpisodeModel.id, kind, after)
    if condition is not None:
        statement = statement.where(condition)
    return _ordered(statement, EpisodeModel.created_at, EpisodeModel.id, limit)


__all__ = ["SqlCaptureSessionArtifactRepository"]
