"""提交、记录、复核与仲裁 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.application.annotation.ports import RecordObservation
from app.domain.annotation.arbitration import AnnotationArbitration, ArbitrationVote
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.review import AnnotationReview
from app.domain.annotation.submission import AnnotationRecord, AnnotationSubmission
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_results import (
    AnnotationArbitration as AnnotationArbitrationModel,
)
from app.infrastructure.db.models.annotation_results import (
    AnnotationRecord as AnnotationRecordModel,
)
from app.infrastructure.db.models.annotation_results import (
    AnnotationReview as AnnotationReviewModel,
)
from app.infrastructure.db.models.annotation_results import (
    AnnotationSubmission as AnnotationSubmissionModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    keyset_after,
    raise_update_failure,
    scoped_select,
    visible_submission_clause,
)


def _uuid_list(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list | tuple):
        return ()
    result: list[UUID] = []
    for item in value:
        try:
            result.append(UUID(str(item)))
        except ValueError:
            continue
    return tuple(result)


def _uuid_tuple_json(value: tuple[UUID, ...]) -> list[str]:
    return [str(item) for item in value]


def _vote_from_payload(value: object) -> ArbitrationVote:
    data = dict(value) if isinstance(value, dict) else {}
    arbiter_id = data.get("arbiter_id")
    if isinstance(arbiter_id, str):
        data["arbiter_id"] = UUID(arbiter_id)
    voted_at = data.get("voted_at")
    if isinstance(voted_at, str):
        data["voted_at"] = datetime.fromisoformat(voted_at)
    return ArbitrationVote(**data)


class SqlAnnotationSubmissionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> AnnotationSubmission:
        scope.ensure(submission.project_id)
        ensure_initial_version(submission.version)
        now = datetime.now(UTC)
        self._session.add(
            AnnotationSubmissionModel(
                id=submission.submission_id,
                project_id=submission.project_id,
                task_id=submission.task_id,
                batch_id=submission.batch_id,
                assignment_id=submission.assignment_id,
                annotator_id=submission.annotator_id,
                submission_version=submission.submission_version,
                status=enum_value(submission.status),
                schema_id=submission.schema_id,
                schema_version=submission.schema_version,
                schema_sha256=submission.schema_sha256,
                item_count=submission.item_count,
                records_digest=submission.records_digest,
                data_source=submission.data_source.value,
                record_refs=_uuid_tuple_json(submission.record_refs),
                review_id=submission.review_id,
                arbitration_id=submission.arbitration_id,
                supersedes_id=submission.supersedes_id,
                submitted_at=submission.submitted_at,
                decided_at=submission.decided_at,
                reason=submission.reason,
                version=submission.version,
                created_at=submission.created_at or now,
                updated_at=submission.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return submission

    def get(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> AnnotationSubmission | None:
        row = self._session.scalar(
            select(AnnotationSubmissionModel).where(
                AnnotationSubmissionModel.id == submission_id,
                AnnotationSubmissionModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        statuses: tuple[str, ...] | None = None,
        after_id: UUID | None = None,
        visible_only: bool = False,
        limit: int = 100,
    ) -> list[AnnotationSubmission]:
        """按任务列出提交（键集分页）。

        ``visible_only=True`` 时把「他人 DRAFT 不可见」下推到 SQL：分页必须在
        过滤之后取 ``limit+1``，否则他人草稿会占掉名额，出现页内条数不足
        却仍有下一页。
        """
        if limit < 1 or limit > 501:
            raise DomainValidationError("limit 必须在 1 到 501 之间")
        statement = scoped_select(AnnotationSubmissionModel, scope).where(
            AnnotationSubmissionModel.task_id == task_id
        )
        if statuses:
            statement = statement.where(AnnotationSubmissionModel.status.in_(statuses))
        if visible_only:
            statement = statement.where(
                visible_submission_clause(
                    AnnotationSubmissionModel.status,
                    AnnotationSubmissionModel.annotator_id,
                    scope.actor_id,
                )
            )
        after_clause = keyset_after(AnnotationSubmissionModel.id, after_id)
        if after_clause is not None:
            statement = statement.where(after_clause)
        statement = statement.order_by(AnnotationSubmissionModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def list_for_batch(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
    ) -> list[AnnotationSubmission]:
        rows = self._session.scalars(
            scoped_select(AnnotationSubmissionModel, scope)
            .where(AnnotationSubmissionModel.batch_id == batch_id)
            .order_by(AnnotationSubmissionModel.id)
        )
        return [self._from_row(row) for row in rows]

    def list_for_assignment(
        self,
        scope: ProjectScope,
        *,
        assignment_id: UUID,
    ) -> list[AnnotationSubmission]:
        rows = self._session.scalars(
            scoped_select(AnnotationSubmissionModel, scope)
            .where(AnnotationSubmissionModel.assignment_id == assignment_id)
            .order_by(AnnotationSubmissionModel.submission_version)
        )
        return [self._from_row(row) for row in rows]

    def latest_for_assignment(
        self,
        scope: ProjectScope,
        *,
        assignment_id: UUID,
    ) -> AnnotationSubmission | None:
        row = self._session.scalar(
            scoped_select(AnnotationSubmissionModel, scope)
            .where(AnnotationSubmissionModel.assignment_id == assignment_id)
            .order_by(AnnotationSubmissionModel.submission_version.desc())
            .limit(1)
        )
        return self._from_row(row) if row is not None else None

    def next_version(self, scope: ProjectScope, assignment_id: UUID) -> int:
        latest = self._session.scalar(
            select(func.max(AnnotationSubmissionModel.submission_version)).where(
                AnnotationSubmissionModel.project_id == scope.project_id,
                AnnotationSubmissionModel.assignment_id == assignment_id,
            )
        )
        return int(latest or 0) + 1

    def save(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        expected_version: int,
    ) -> AnnotationSubmission:
        scope.ensure(submission.project_id)
        ensure_expected_version(submission.version, expected_version)
        now = submission.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationSubmissionModel)
            .where(
                AnnotationSubmissionModel.id == submission.submission_id,
                AnnotationSubmissionModel.project_id == scope.project_id,
                AnnotationSubmissionModel.version == expected_version,
            )
            .values(
                status=enum_value(submission.status),
                review_id=submission.review_id,
                arbitration_id=submission.arbitration_id,
                submitted_at=submission.submitted_at,
                decided_at=submission.decided_at,
                reason=submission.reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationSubmissionModel,
                scope,
                submission.submission_id,
                "标注提交",
            )
        return replace(
            submission,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: AnnotationSubmissionModel) -> AnnotationSubmission:
        return AnnotationSubmission(
            submission_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            batch_id=row.batch_id,
            assignment_id=row.assignment_id,
            annotator_id=row.annotator_id,
            submission_version=row.submission_version,
            status=row.status,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            schema_sha256=row.schema_sha256,
            item_count=row.item_count,
            records_digest=row.records_digest,
            data_source=row.data_source,
            record_refs=_uuid_list(row.record_refs),
            review_id=row.review_id,
            arbitration_id=row.arbitration_id,
            supersedes_id=row.supersedes_id,
            submitted_at=as_utc(row.submitted_at),
            decided_at=as_utc(row.decided_at),
            reason=row.reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlAnnotationRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(
        self,
        scope: ProjectScope,
        records: tuple[AnnotationRecord, ...],
    ) -> None:
        now = datetime.now(UTC)
        for record in records:
            scope.ensure(record.project_id)
            ensure_initial_version(record.version)
            self._session.add(
                AnnotationRecordModel(
                    id=record.annotation_id,
                    project_id=record.project_id,
                    submission_id=record.submission_id,
                    task_id=record.task_id,
                    episode_id=record.episode_id,
                    episode_version=record.episode_version,
                    annotation_lineage_id=record.annotation_lineage_id,
                    annotation_version=record.annotation_version,
                    schema_id=record.schema_id,
                    schema_version=record.schema_version,
                    status=enum_value(record.status),
                    time_range=dict(record.time_range) if record.time_range else None,
                    labels=[item.to_payload() for item in record.labels],
                    evidence_refs=[item.to_payload() for item in record.evidence_refs],
                    record_sha256=record.record_sha256,
                    supersedes_id=record.supersedes_id,
                    approved_by=record.approved_by,
                    approved_at=record.approved_at,
                    version=record.version,
                    created_at=record.created_at or now,
                    updated_at=record.updated_at or now,
                    created_by=scope.actor_id,
                    updated_by=scope.actor_id,
                )
            )
        flush_or_duplicate(self._session)

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationRecord]:
        rows = self._session.scalars(
            scoped_select(AnnotationRecordModel, scope)
            .where(AnnotationRecordModel.submission_id == submission_id)
            .order_by(AnnotationRecordModel.id)
        )
        return [self._from_row(row) for row in rows]

    def list_for_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        statuses: frozenset[str] | None = None,
        limit: int = 2000,
    ) -> list[AnnotationRecord]:
        statement = scoped_select(AnnotationRecordModel, scope).where(
            AnnotationRecordModel.episode_id == episode_id
        )
        if statuses is not None:
            # 质量聚合只应看当前有效的标注事实；DRAFT/REJECTED/SUPERSEDED
            # 既不代表已提交结论，也不该在 finding 里暴露他人的草稿 ID。
            statement = statement.where(AnnotationRecordModel.status.in_(statuses))
        rows = self._session.scalars(statement.order_by(AnnotationRecordModel.id).limit(limit))
        return [self._from_row(row) for row in rows]

    def get(self, scope: ProjectScope, annotation_id: UUID) -> AnnotationRecord | None:
        """按 ID 取记录：项目过滤由 ``scoped_select`` 强制，跨项目返回 ``None``。"""
        row = self._session.scalar(
            scoped_select(AnnotationRecordModel, scope).where(
                AnnotationRecordModel.id == annotation_id
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        statuses: tuple[str, ...],
    ) -> list[RecordObservation]:
        statement = (
            select(AnnotationRecordModel, AnnotationSubmissionModel.annotator_id)
            .join(
                AnnotationSubmissionModel,
                (AnnotationRecordModel.project_id == AnnotationSubmissionModel.project_id)
                & (AnnotationRecordModel.submission_id == AnnotationSubmissionModel.id),
            )
            .where(
                AnnotationRecordModel.project_id == scope.project_id,
                AnnotationRecordModel.task_id == task_id,
                AnnotationRecordModel.status.in_(statuses),
            )
            .order_by(AnnotationRecordModel.episode_id, AnnotationRecordModel.id)
        )
        return [
            RecordObservation(
                record=self._from_row(row[0]),
                submission_id=row[0].submission_id,
                annotator_id=row[1],
            )
            for row in self._session.execute(statement)
        ]

    def save(
        self,
        scope: ProjectScope,
        record: AnnotationRecord,
        *,
        expected_version: int,
    ) -> AnnotationRecord:
        scope.ensure(record.project_id)
        ensure_expected_version(record.version, expected_version)
        now = record.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationRecordModel)
            .where(
                AnnotationRecordModel.id == record.annotation_id,
                AnnotationRecordModel.project_id == scope.project_id,
                AnnotationRecordModel.version == expected_version,
            )
            .values(
                status=enum_value(record.status),
                approved_by=record.approved_by,
                approved_at=record.approved_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationRecordModel,
                scope,
                record.annotation_id,
                "标注记录",
            )
        return replace(
            record,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: AnnotationRecordModel) -> AnnotationRecord:
        return AnnotationRecord(
            annotation_id=row.id,
            annotation_lineage_id=row.annotation_lineage_id,
            annotation_version=row.annotation_version,
            project_id=row.project_id,
            submission_id=row.submission_id,
            episode_id=row.episode_id,
            episode_version=row.episode_version,
            task_id=row.task_id,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            status=row.status,
            time_range=row.time_range,
            labels=tuple(row.labels or ()),
            evidence_refs=tuple(row.evidence_refs or ()),
            record_sha256=row.record_sha256,
            supersedes_id=row.supersedes_id,
            approved_by=row.approved_by,
            approved_at=as_utc(row.approved_at),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlAnnotationReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, review: AnnotationReview) -> AnnotationReview:
        scope.ensure(review.project_id)
        ensure_initial_version(review.version)
        now = datetime.now(UTC)
        self._session.add(
            AnnotationReviewModel(
                id=review.review_id,
                project_id=review.project_id,
                submission_id=review.submission_id,
                task_id=review.task_id,
                reviewer_id=review.reviewer_id,
                review_round=review.review_round,
                status=enum_value(review.status),
                decision=review.decision.value if review.decision else None,
                issues=[item.to_payload() for item in review.issues],
                checklist=[item.to_payload() for item in review.checklist],
                review_duration_sec=review.review_duration_sec,
                reason=review.reason,
                decided_at=review.decided_at,
                version=review.version,
                created_at=review.created_at or now,
                updated_at=review.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return review

    def get(self, scope: ProjectScope, review_id: UUID) -> AnnotationReview | None:
        row = self._session.scalar(
            select(AnnotationReviewModel).where(
                AnnotationReviewModel.id == review_id,
                AnnotationReviewModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationReview]:
        rows = self._session.scalars(
            scoped_select(AnnotationReviewModel, scope)
            .where(AnnotationReviewModel.submission_id == submission_id)
            .order_by(AnnotationReviewModel.review_round)
        )
        return [self._from_row(row) for row in rows]

    def first_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> AnnotationReview | None:
        row = self._session.scalar(
            scoped_select(AnnotationReviewModel, scope)
            .where(AnnotationReviewModel.submission_id == submission_id)
            .order_by(AnnotationReviewModel.review_round)
            .limit(1)
        )
        return self._from_row(row) if row is not None else None

    def next_round(self, scope: ProjectScope, submission_id: UUID) -> int:
        latest = self._session.scalar(
            select(func.max(AnnotationReviewModel.review_round)).where(
                AnnotationReviewModel.project_id == scope.project_id,
                AnnotationReviewModel.submission_id == submission_id,
            )
        )
        return int(latest or 0) + 1

    def save(
        self,
        scope: ProjectScope,
        review: AnnotationReview,
        *,
        expected_version: int,
    ) -> AnnotationReview:
        scope.ensure(review.project_id)
        ensure_expected_version(review.version, expected_version)
        now = review.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationReviewModel)
            .where(
                AnnotationReviewModel.id == review.review_id,
                AnnotationReviewModel.project_id == scope.project_id,
                AnnotationReviewModel.version == expected_version,
            )
            .values(
                status=enum_value(review.status),
                decision=review.decision.value if review.decision else None,
                issues=[item.to_payload() for item in review.issues],
                checklist=[item.to_payload() for item in review.checklist],
                review_duration_sec=review.review_duration_sec,
                reason=review.reason,
                decided_at=review.decided_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationReviewModel,
                scope,
                review.review_id,
                "标注复核",
            )
        return replace(
            review,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: AnnotationReviewModel) -> AnnotationReview:
        return AnnotationReview(
            review_id=row.id,
            project_id=row.project_id,
            submission_id=row.submission_id,
            task_id=row.task_id,
            reviewer_id=row.reviewer_id,
            review_round=row.review_round,
            status=row.status,
            decision=row.decision,
            issues=tuple(row.issues or ()),
            checklist=tuple(row.checklist or ()),
            review_duration_sec=row.review_duration_sec,
            reason=row.reason,
            decided_at=as_utc(row.decided_at),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlAnnotationArbitrationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        arbitration: AnnotationArbitration,
    ) -> AnnotationArbitration:
        scope.ensure(arbitration.project_id)
        ensure_initial_version(arbitration.version)
        now = datetime.now(UTC)
        self._session.add(
            AnnotationArbitrationModel(
                id=arbitration.arbitration_id,
                project_id=arbitration.project_id,
                task_id=arbitration.task_id,
                submission_id=arbitration.submission_id,
                review_id=arbitration.review_id,
                arbiter_ids=[str(item) for item in arbitration.arbiter_ids],
                quorum=arbitration.quorum,
                status=enum_value(arbitration.status),
                trigger=arbitration.trigger.value,
                votes=[item.to_payload() for item in arbitration.votes],
                final_decision=(
                    arbitration.final_decision.value
                    if arbitration.final_decision is not None
                    else None
                ),
                reason=arbitration.reason,
                resolved_at=arbitration.resolved_at,
                version=arbitration.version,
                created_at=arbitration.created_at or now,
                updated_at=arbitration.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return arbitration

    def get(
        self,
        scope: ProjectScope,
        arbitration_id: UUID,
    ) -> AnnotationArbitration | None:
        row = self._session.scalar(
            select(AnnotationArbitrationModel).where(
                AnnotationArbitrationModel.id == arbitration_id,
                AnnotationArbitrationModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationArbitration]:
        rows = self._session.scalars(
            scoped_select(AnnotationArbitrationModel, scope)
            .where(AnnotationArbitrationModel.submission_id == submission_id)
            .order_by(AnnotationArbitrationModel.id)
        )
        return [self._from_row(row) for row in rows]

    def save(
        self,
        scope: ProjectScope,
        arbitration: AnnotationArbitration,
        *,
        expected_version: int,
    ) -> AnnotationArbitration:
        scope.ensure(arbitration.project_id)
        ensure_expected_version(arbitration.version, expected_version)
        now = arbitration.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationArbitrationModel)
            .where(
                AnnotationArbitrationModel.id == arbitration.arbitration_id,
                AnnotationArbitrationModel.project_id == scope.project_id,
                AnnotationArbitrationModel.version == expected_version,
            )
            .values(
                status=enum_value(arbitration.status),
                votes=[item.to_payload() for item in arbitration.votes],
                final_decision=(
                    arbitration.final_decision.value
                    if arbitration.final_decision is not None
                    else None
                ),
                reason=arbitration.reason,
                resolved_at=arbitration.resolved_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationArbitrationModel,
                scope,
                arbitration.arbitration_id,
                "标注仲裁",
            )
        return replace(
            arbitration,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: AnnotationArbitrationModel) -> AnnotationArbitration:
        return AnnotationArbitration(
            arbitration_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            submission_id=row.submission_id,
            review_id=row.review_id,
            arbiter_ids=_uuid_list(row.arbiter_ids),
            quorum=row.quorum,
            trigger=row.trigger,
            status=row.status,
            votes=tuple(_vote_from_payload(item) for item in (row.votes or ())),
            final_decision=row.final_decision,
            reason=row.reason,
            resolved_at=as_utc(row.resolved_at),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
