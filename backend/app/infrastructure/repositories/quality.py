"""质量运行 SQL 仓储。

所有查询强制项目作用域；运行与 finding 都只追加，不提供更新/删除。
``add`` 在同一事务内写入运行与全部 finding：任一条失败整体回滚，不会留下
"有运行、没结论"的半成品。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySubjectType,
)
from app.domain.quality.models import QualityFinding, QualityRun
from app.infrastructure.db.models.quality import (
    QualityFindingModel,
    QualityRunModel,
)
from app.infrastructure.repositories._sql_support import as_utc, keyset_after_desc


def _finding(row: QualityFindingModel) -> QualityFinding:
    return QualityFinding(
        finding_id=row.id,
        project_id=row.project_id,
        run_id=row.run_id,
        rule_id=row.rule_id,
        rule_version=row.rule_version,
        category=row.category,
        severity=row.severity,
        status=row.status,
        message=row.message,
        evidence=dict(row.evidence) if row.evidence is not None else None,
        score=float(row.score) if row.score is not None else None,
        subject_ref=row.subject_ref,
        created_at=as_utc(row.created_at),  # type: ignore[arg-type]
    )


def _run(row: QualityRunModel) -> QualityRun:
    return QualityRun(
        run_id=row.id,
        project_id=row.project_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        rule_set_version=row.rule_set_version,
        status=row.status,
        score=float(row.score) if row.score is not None else None,
        finding_count=row.finding_count,
        error_count=row.error_count,
        warn_count=row.warn_count,
        category_summary={
            str(key): dict(value) for key, value in dict(row.category_summary).items()
        },
        started_at=as_utc(row.started_at),  # type: ignore[arg-type]
        finished_at=as_utc(row.finished_at),  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),  # type: ignore[arg-type]
        created_by=row.created_by,
    )


class SqlQualityRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, run: QualityRun) -> QualityRun:
        scope.ensure(run.project_id)
        self._session.add(
            QualityRunModel(
                id=run.run_id,
                project_id=run.project_id,
                subject_type=run.subject_type.value,
                subject_id=run.subject_id,
                rule_set_version=run.rule_set_version,
                status=run.status.value,
                score=run.score,
                finding_count=run.finding_count,
                error_count=run.error_count,
                warn_count=run.warn_count,
                category_summary={key: dict(value) for key, value in run.category_summary.items()},
                started_at=run.started_at,
                finished_at=run.finished_at,
                created_at=run.created_at,
                created_by=run.created_by,
            )
        )
        self._session.flush()
        for finding in run.findings:
            scope.ensure(finding.project_id)
            self._session.add(
                QualityFindingModel(
                    id=finding.finding_id,
                    project_id=finding.project_id,
                    run_id=finding.run_id,
                    category=finding.category.value,
                    rule_id=finding.rule_id,
                    rule_version=finding.rule_version,
                    severity=finding.severity.value,
                    status=finding.status.value,
                    subject_ref=finding.subject_ref,
                    message=finding.message,
                    evidence=dict(finding.evidence) if finding.evidence is not None else None,
                    score=finding.score,
                    created_at=finding.created_at,
                )
            )
        self._session.flush()
        return run

    def get(self, scope: ProjectScope, run_id: UUID) -> QualityRun | None:
        row = self._session.scalar(
            select(QualityRunModel).where(
                QualityRunModel.id == run_id,
                QualityRunModel.project_id == scope.project_id,
            )
        )
        return _run(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        subject_type: QualitySubjectType,
        subject_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityRun]:
        statement = select(QualityRunModel).where(
            QualityRunModel.project_id == scope.project_id,
            QualityRunModel.subject_type == subject_type.value,
            QualityRunModel.subject_id == subject_id,
        )
        after_clause = keyset_after_desc(
            QualityRunModel,
            QualityRunModel.created_at,
            QualityRunModel.id,
            after_id,
            project_id=scope.project_id,
        )
        if after_clause is not None:
            statement = statement.where(after_clause)
        rows = self._session.scalars(
            statement.order_by(
                QualityRunModel.created_at.desc(),
                QualityRunModel.id.desc(),
            ).limit(limit)
        )
        return [_run(row) for row in rows]

    def list_findings(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        category: QualityCategory | None = None,
        status: QualityFindingStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityFinding]:
        statement = select(QualityFindingModel).where(
            QualityFindingModel.project_id == scope.project_id,
            QualityFindingModel.run_id == run_id,
        )
        if category is not None:
            statement = statement.where(QualityFindingModel.category == category.value)
        if status is not None:
            statement = statement.where(QualityFindingModel.status == status.value)
        if after_id is not None:
            statement = statement.where(QualityFindingModel.id > after_id)
        rows = self._session.scalars(statement.order_by(QualityFindingModel.id).limit(limit)).all()
        return [_finding(row) for row in rows]


def _as_datetime(value: object) -> datetime | None:
    """保留给未来投影使用；当前全部时间字段都走 ``as_utc``。"""
    return value if isinstance(value, datetime) else None


__all__ = ["SqlQualityRunRepository"]
