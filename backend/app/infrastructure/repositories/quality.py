"""质量运行 SQL 仓储。

所有查询强制项目作用域。

写入语义分两类：

- ``add`` 追加一次**已产出结论**的运行（连同全部 findings，同事务）；
- 生命周期（排队/执行/失败/重投）尚未开放写入：V31-030 只落地领域状态机与
  表结构，执行编排在 V31-031 的 Worker 里接入。

读取语义只有一种：**结论视图**。``get`` / ``list`` /
``latest_for_subjects`` 只认 ``lifecycle = 'SUCCEEDED'`` 的行。未完成的运行
没有结论，把它们放进结论视图会让 "status is NULL" 被调用方读成 "通过"。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualityRunLifecycle,
    QualitySubjectType,
)
from app.domain.quality.errors import DomainValidationError
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
        started_at=as_utc(row.started_at) if row.started_at is not None else None,
        finished_at=as_utc(row.finished_at) if row.finished_at is not None else None,
        created_at=as_utc(row.created_at),  # type: ignore[arg-type]
        created_by=row.created_by,
        lifecycle=row.lifecycle,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        error_code=row.error_code,
        error_summary=row.error_summary,
    )


class SqlQualityRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, run: QualityRun) -> QualityRun:
        """追加一次已产出结论的运行。

        未完成的运行没有 ``status`` / 起止时间，不能走本方法：它们由执行编排
        写入（V31-031）。这里显式拒绝，避免"半成品"绕过状态机进入结论视图。
        """
        scope.ensure(run.project_id)
        if run.lifecycle is not QualityRunLifecycle.SUCCEEDED:
            raise DomainValidationError(
                f"只能追加已产出结论的运行；{run.lifecycle.value} 由执行编排写入"
            )
        if run.status is None:
            raise DomainValidationError("已产出结论的运行必须有 status")
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
                lifecycle=run.lifecycle.value,
                attempt=run.attempt,
                max_attempts=run.max_attempts,
                error_code=run.error_code,
                error_summary=run.error_summary,
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
        """读一条**结论**；未完成的运行不属于结论视图（返回 ``None``）。"""
        row = self._session.scalar(
            select(QualityRunModel).where(
                QualityRunModel.id == run_id,
                QualityRunModel.project_id == scope.project_id,
                QualityRunModel.lifecycle == QualityRunLifecycle.SUCCEEDED.value,
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
            QualityRunModel.lifecycle == QualityRunLifecycle.SUCCEEDED.value,
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

    def latest_for_subjects(
        self,
        scope: ProjectScope,
        *,
        subject_type: QualitySubjectType,
        subject_ids: Sequence[UUID],
    ) -> tuple[QualityRun, ...]:
        """每个对象取最新一次**结论**；一次查询完成，避免逐条 N+1。

        排序口径与 :meth:`list` 一致：``created_at`` 倒序、``id`` 倒序。
        项目过滤在子查询与外层查询各做一次，跨项目 ID 不会命中。未完成的
        运行不参与排名——"最新一次还没算完"不能顶掉"上一次的结论"。
        """
        if not subject_ids:
            return ()
        ranked = (
            select(
                QualityRunModel.id.label("run_id"),
                func.row_number()
                .over(
                    partition_by=QualityRunModel.subject_id,
                    order_by=(
                        QualityRunModel.created_at.desc(),
                        QualityRunModel.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                QualityRunModel.project_id == scope.project_id,
                QualityRunModel.subject_type == subject_type.value,
                QualityRunModel.subject_id.in_(list(subject_ids)),
                QualityRunModel.lifecycle == QualityRunLifecycle.SUCCEEDED.value,
            )
            .subquery()
        )
        rows = self._session.scalars(
            select(QualityRunModel)
            .join(ranked, QualityRunModel.id == ranked.c.run_id)
            .where(
                QualityRunModel.project_id == scope.project_id,
                ranked.c.rank == 1,
            )
        )
        return tuple(_run(row) for row in rows)

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
