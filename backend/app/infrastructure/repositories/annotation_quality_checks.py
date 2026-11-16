"""质量检查执行记录 SQLAlchemy 仓储（只追加）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.quality_check import QualityCheckRun, QualityFinding
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_quality_rules import (
    AnnotationQualityFinding as FindingModel,
)
from app.infrastructure.db.models.annotation_quality_rules import (
    AnnotationQualityRun as RunModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    enum_value,
    keyset_after_created_desc,
    scoped_select,
)


class SqlQualityCheckRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, record: QualityCheckRun) -> QualityCheckRun:
        scope.ensure(record.project_id)
        self._session.add(
            RunModel(
                id=record.run_id,
                project_id=record.project_id,
                submission_id=record.submission_id,
                rule_set_version=record.rule_set_version,
                status=enum_value(record.status),
                error_count=record.error_count,
                warn_count=record.warn_count,
                started_at=record.started_at,
                finished_at=record.finished_at,
                created_at=record.created_at or datetime.now(UTC),
                created_by=scope.actor_id,
            )
        )
        # 必须先 flush 父行再插明细：两张表之间没有 ORM relationship，
        # SQLAlchemy 的 unit-of-work 不知道插入依赖，会把两条 INSERT 排成
        # 同一批甚至子表先。PostgreSQL 会直接报外键冲突；SQLite 只在
        # ``PRAGMA foreign_keys=ON`` 时报。显式两步是最省心的确定性写法。
        self._session.flush()
        self._session.add_all(
            [
                FindingModel(
                    id=uuid4(),
                    project_id=record.project_id,
                    run_id=record.run_id,
                    annotation_id=item.annotation_id,
                    rule_id=item.rule_id,
                    rule_version=item.rule_version,
                    severity=enum_value(item.severity),
                    passed=item.passed,
                    message=item.message,
                )
                for item in record.findings
            ]
        )
        self._session.flush()
        return record

    def get(self, scope: ProjectScope, run_id: UUID) -> QualityCheckRun | None:
        row = self._session.scalar(
            select(RunModel).where(
                RunModel.id == run_id,
                RunModel.project_id == scope.project_id,
            )
        )
        if row is None:
            return None
        return self._from_row(row)

    def list_for_submission(
        self,
        scope: ProjectScope,
        *,
        submission_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityCheckRun]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(RunModel, scope).where(RunModel.submission_id == submission_id)
        after_clause = keyset_after_created_desc(
            RunModel,
            RunModel.created_at,
            RunModel.id,
            after_id,
        )
        if after_clause is not None:
            statement = statement.where(after_clause)
        rows = self._session.scalars(
            statement.order_by(RunModel.created_at.desc(), RunModel.id.desc()).limit(limit)
        )
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: RunModel) -> QualityCheckRun:
        findings = self._session.scalars(
            select(FindingModel)
            .where(
                FindingModel.project_id == row.project_id,
                FindingModel.run_id == row.id,
            )
            .order_by(FindingModel.annotation_id, FindingModel.rule_id)
        )
        return QualityCheckRun(
            run_id=row.id,
            project_id=row.project_id,
            submission_id=row.submission_id,
            rule_set_version=row.rule_set_version,
            status=row.status,
            error_count=row.error_count,
            warn_count=row.warn_count,
            started_at=as_utc(row.started_at) or row.started_at,
            finished_at=as_utc(row.finished_at) or row.finished_at,
            findings=tuple(
                QualityFinding(
                    annotation_id=item.annotation_id,
                    rule_id=item.rule_id,
                    rule_version=item.rule_version,
                    severity=item.severity,
                    passed=item.passed,
                    message=item.message,
                )
                for item in findings
            ),
            created_at=as_utc(row.created_at),
            created_by=row.created_by,
        )


def delete_run(scope: ProjectScope, session: Session, run_id: UUID) -> int:
    """删除一次检查（含明细）。仅供测试与运维补偿使用，HTTP 不暴露。

    明细用 ``project_id`` + ``run_id`` 同时过滤：即便传入别的项目的 run_id，
    删除范围也会同时被两侧条件限制为 0 行。
    """
    findings = session.execute(
        delete(FindingModel).where(
            FindingModel.project_id == scope.project_id,
            FindingModel.run_id == run_id,
        )
    )
    session.execute(
        delete(RunModel).where(
            RunModel.project_id == scope.project_id,
            RunModel.id == run_id,
        )
    )
    return int(findings.rowcount or 0)
