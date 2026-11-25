"""回流案例 SQLAlchemy 仓储（V52-030）。

三层幂等的最后一层在这里：并发双写撞唯一键 ``(source, case_ref)`` 时，
``IntegrityError`` **必须**翻译成 ``SKIPPED_DUPLICATE`` 判定，绝不让异常
冒泡成"可重试失败"（MEM-INC-001：回调重试会被无限放大）。

应用层在判定前用 ``known_refs`` / ``hourly_counts`` 做批量预查询，避免在
循环里逐条 SELECT（蓝图 N5 的回流风暴场景：单批最多 200 条）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.annotation.backflow import (
    BackflowCase,
    BackflowDecisionKind,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_backflow_case import AnnotationBackflowCase as _Model

_STATUS_ACCEPTED = ("RECEIVED", "CONVERTED")


class SqlBackflowRepository:
    """``annotation_backflow_cases`` 的读写仓储（不提交，由 UoW 统一提交）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def known_refs(
        self, scope: ProjectScope, sources: tuple[str, ...]
    ) -> frozenset[tuple[str, str]]:
        """批量查询项目内已受理的 ``(source, case_ref)`` 键集（去重第二层）。"""
        scope.ensure(scope.project_id)
        if not sources:
            return frozenset()
        rows = self._session.execute(
            select(_Model.source, _Model.case_ref).where(
                _Model.project_id == scope.project_id,
                _Model.source.in_(sources),
                _Model.status.in_(_STATUS_ACCEPTED),
            )
        ).all()
        return frozenset((row.source, row.case_ref) for row in rows)

    def hourly_counts(
        self,
        scope: ProjectScope,
        sources: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """统计各 source 最近 1 小时内已受理数（限流窗口基数）。"""
        scope.ensure(scope.project_id)
        if not sources:
            return {}
        moment = now or datetime.now(UTC)
        window_start = moment - timedelta(hours=1)
        rows = self._session.execute(
            select(_Model.source, func.count())
            .where(
                _Model.project_id == scope.project_id,
                _Model.source.in_(sources),
                _Model.status.in_(_STATUS_ACCEPTED),
                _Model.received_at >= window_start,
                _Model.received_at <= moment,
            )
            .group_by(_Model.source)
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    def add_accepted(self, scope: ProjectScope, cases: tuple[BackflowCase, ...]) -> None:
        """写入受理的案例行（CREATE_TASK 判定）；唯一键冲突不在此处理。"""
        for case in cases:
            scope.ensure(case.project_id)
            self._session.add(
                _Model(
                    id=case.case_id,
                    project_id=case.project_id,
                    source=case.source,
                    case_ref=case.case_ref,
                    capability=case.capability,
                    failure_summary=case.failure_summary,
                    failure_count=case.failure_count,
                    suggested_priority=case.suggested_priority.value,
                    payload=dict(case.payload),
                    status=case.status.value,
                    decision_reason=case.decision_reason,
                    task_id=case.task_id,
                    received_at=case.received_at,
                    created_at=case.created_at or datetime.now(UTC),
                )
            )

    def add_skipped(
        self,
        scope: ProjectScope,
        cases: tuple[BackflowCase, ...],
        kind: BackflowDecisionKind,
        reason: str,
    ) -> None:
        """写入被跳过/限流的案例行（留痕审计：运营能看到被拒了什么）。"""
        for case in cases:
            scope.ensure(case.project_id)
            self._session.add(
                _Model(
                    id=case.case_id,
                    project_id=case.project_id,
                    source=case.source,
                    case_ref=case.case_ref,
                    capability=case.capability,
                    failure_summary=case.failure_summary,
                    failure_count=case.failure_count,
                    suggested_priority=case.suggested_priority.value,
                    payload=dict(case.payload),
                    status=(
                        "SKIPPED_DUPLICATE"
                        if kind is BackflowDecisionKind.DUPLICATE_SKIPPED
                        else "RATE_LIMITED"
                    ),
                    decision_reason=reason,
                    task_id=None,
                    received_at=case.received_at,
                    created_at=case.created_at or datetime.now(UTC),
                )
            )

    def mark_converted(self, scope: ProjectScope, case_id: UUID, task_id: UUID) -> None:
        """回填任务引用并置 ``CONVERTED``（转换成功后调用）。"""
        scope.ensure(scope.project_id)
        row = self._session.get(_Model, case_id)
        if row is None or row.project_id != scope.project_id:
            return
        row.task_id = task_id
        row.status = "CONVERTED"
        row.decision_reason = "已转换为标注任务"

    def list_cases(self, scope: ProjectScope, *, limit: int = 50) -> tuple[BackflowCase, ...]:
        """按接收时间倒序列出回流案例（运营视图）。"""
        scope.ensure(scope.project_id)
        rows = self._session.scalars(
            select(_Model)
            .where(_Model.project_id == scope.project_id)
            .order_by(_Model.received_at.desc(), _Model.id)
            .limit(limit)
        ).all()
        return tuple(_to_domain(row) for row in rows)


def _as_utc(value: datetime) -> datetime:
    """读库时间统一成 UTC aware。

    SQLite 的 ``DateTime(timezone=True)`` 实际不保存时区，读回是 naive——
    这是 MEM-INC-021 的同族坑（JSONB 回读 ISO 字符串亦然）。领域层要求
    aware，因此在**仓储边界**补齐，而不是放宽领域校验。
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_domain(row: _Model) -> BackflowCase:
    """ORM → 领域实体（只读视图；payload 为 JSON 快照）。"""
    return BackflowCase(
        case_id=row.id,
        project_id=row.project_id,
        source=row.source,
        case_ref=row.case_ref,
        capability=row.capability,
        payload=dict(row.payload or {}),
        received_at=_as_utc(row.received_at),
        status=row.status,
        failure_summary=row.failure_summary,
        failure_count=row.failure_count,
        suggested_priority=row.suggested_priority,
        task_id=row.task_id,
        decision_reason=row.decision_reason,
        created_at=_as_utc(row.created_at) if row.created_at else None,
    )


__all__ = ["SqlBackflowRepository", "IntegrityError"]
