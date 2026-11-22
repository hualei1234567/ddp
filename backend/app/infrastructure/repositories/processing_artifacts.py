"""``ProcessingArtifact`` SQL 仓储（V31-016）。

所有查询强制项目作用域；跨项目读取返回空列表（与 ``GET`` 的口径一致，
不泄露存在性），写入时 ``scope.ensure`` 直接拒绝。

产物的生命周期由运行承载：``run_id`` 外键 ``ON DELETE CASCADE``，删除
运行即删除其产物记录。本仓储不提供删除用例——运行是产物的边界。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifact
from app.infrastructure.db.models.processing import ProcessingArtifactModel


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _artifact(row: ProcessingArtifactModel) -> ProcessingArtifact:
    return ProcessingArtifact(
        artifact_id=row.id,
        project_id=row.project_id,
        run_id=row.run_id,
        stage=row.stage,
        attempt=row.attempt,
        kind=row.kind,
        object_key=row.object_key,
        schema_version=row.schema_version,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        checksum_sha256=row.checksum_sha256,
        source_ref=row.source_ref,
        created_at=_as_utc(row.created_at),
        created_by=row.created_by,
    )


class SqlProcessingArtifactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, artifact: ProcessingArtifact) -> ProcessingArtifact:
        """登记一条产物；唯一键冲突由数据库拒绝（调用方须幂等）。"""
        scope.ensure(artifact.project_id)
        self._session.add(
            ProcessingArtifactModel(
                id=artifact.artifact_id,
                project_id=artifact.project_id,
                run_id=artifact.run_id,
                stage=artifact.stage,
                attempt=artifact.attempt,
                kind=artifact.kind,
                object_key=artifact.object_key,
                schema_version=artifact.schema_version,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                checksum_sha256=artifact.checksum_sha256,
                source_ref=artifact.source_ref,
                created_at=artifact.created_at,
                created_by=artifact.created_by,
            )
        )
        self._session.flush()
        return artifact

    def list(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        stage: str | None = None,
        attempt: int | None = None,
        kind: str | None = None,
    ) -> list[ProcessingArtifact]:
        """列出某次运行的产物；可按阶段/尝试/类型收窄。

        排序 ``(created_at, id)``：同一毫秒内多次写入用主键兜底，游标稳定。
        """
        stmt = select(ProcessingArtifactModel).where(
            ProcessingArtifactModel.project_id == scope.project_id,
            ProcessingArtifactModel.run_id == run_id,
        )
        if stage is not None:
            stmt = stmt.where(ProcessingArtifactModel.stage == stage)
        if attempt is not None:
            stmt = stmt.where(ProcessingArtifactModel.attempt == attempt)
        if kind is not None:
            stmt = stmt.where(ProcessingArtifactModel.kind == kind)
        rows = self._session.scalars(
            stmt.order_by(ProcessingArtifactModel.created_at, ProcessingArtifactModel.id)
        ).all()
        return [_artifact(row) for row in rows]

    def get(self, scope: ProjectScope, artifact_id: UUID) -> ProcessingArtifact | None:
        """按主键读取；跨项目与不存在都返回 ``None``。"""
        row = self._session.get(ProcessingArtifactModel, artifact_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _artifact(row)


__all__ = ["SqlProcessingArtifactRepository"]
