"""黄金集、抽检与 IAA 报告 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.application.annotation.ports import (
    GoldEvaluationRecord,
    GoldExposure,
    IAAReportRecord,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.gold_set import GoldEvaluation, GoldItem, GoldItemResult, GoldSet
from app.domain.annotation.quality_sample import QualitySample
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_quality import (
    GoldEvaluation as GoldEvaluationModel,
)
from app.infrastructure.db.models.annotation_quality import (
    GoldExposure as GoldExposureModel,
)
from app.infrastructure.db.models.annotation_quality import (
    GoldSet as GoldSetModel,
)
from app.infrastructure.db.models.annotation_quality import (
    IAAReport as IAAReportModel,
)
from app.infrastructure.db.models.annotation_quality import (
    QualitySample as QualitySampleModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    keyset_after,
    keyset_after_created_desc,
    raise_update_failure,
    scoped_select,
)


def _uuid_tuple(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list | tuple):
        return ()
    result: list[UUID] = []
    for item in value:
        try:
            result.append(UUID(str(item)))
        except ValueError:
            continue
    return tuple(result)


def _uuid_json(value: tuple[UUID, ...]) -> list[str]:
    return [str(item) for item in value]


def _gold_item_from_row(value: dict[str, object]) -> GoldItem:
    return GoldItem(
        gold_item_id=UUID(str(value["gold_item_id"])),
        episode_id=UUID(str(value["episode_id"])),
        expected_labels=tuple(value.get("expected_labels") or ()),
        difficulty=value.get("difficulty", "MEDIUM"),
        weight=value.get("weight", 1.0),
        active=value.get("active", True),
        episode_version=value.get("episode_version"),
        time_range=value.get("time_range"),
    )


class SqlGoldSetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, gold_set: GoldSet) -> GoldSet:
        scope.ensure(gold_set.project_id)
        ensure_initial_version(gold_set.version)
        now = datetime.now(UTC)
        self._session.add(
            GoldSetModel(
                id=gold_set.gold_set_id,
                project_id=gold_set.project_id,
                code=gold_set.code,
                name=gold_set.name,
                status=enum_value(gold_set.status),
                schema_id=gold_set.schema_id,
                schema_version=gold_set.schema_version,
                schema_sha256=gold_set.schema_sha256,
                blind=gold_set.blind,
                items=[item.answer_payload() for item in gold_set.items],
                exposure_policy=gold_set.exposure_policy.value,
                max_exposure_per_annotator=gold_set.max_exposure_per_annotator,
                rotation_policy=gold_set.rotation_policy.value,
                usage_policy=dict(gold_set.usage_policy),
                published_at=gold_set.published_at,
                retired_at=gold_set.retired_at,
                revoked_reason=gold_set.revoked_reason,
                version=gold_set.version,
                created_at=gold_set.created_at or now,
                updated_at=gold_set.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return gold_set

    def get(self, scope: ProjectScope, gold_set_id: UUID) -> GoldSet | None:
        row = self._session.scalar(
            select(GoldSetModel).where(
                GoldSetModel.id == gold_set_id,
                GoldSetModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> GoldSet | None:
        row = self._session.scalar(
            select(GoldSetModel).where(
                GoldSetModel.project_id == scope.project_id,
                GoldSetModel.code == code.strip(),
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[GoldSet]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(GoldSetModel, scope)
        if status is not None:
            statement = statement.where(GoldSetModel.status == status)
        if after_id is not None:
            statement = statement.where(GoldSetModel.id > after_id)
        statement = statement.order_by(GoldSetModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        gold_set: GoldSet,
        *,
        expected_version: int,
    ) -> GoldSet:
        scope.ensure(gold_set.project_id)
        ensure_expected_version(gold_set.version, expected_version)
        now = gold_set.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(GoldSetModel)
            .where(
                GoldSetModel.id == gold_set.gold_set_id,
                GoldSetModel.project_id == scope.project_id,
                GoldSetModel.version == expected_version,
            )
            .values(
                status=enum_value(gold_set.status),
                published_at=gold_set.published_at,
                retired_at=gold_set.retired_at,
                revoked_reason=gold_set.revoked_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                GoldSetModel,
                scope,
                gold_set.gold_set_id,
                "黄金集",
            )
        return replace(
            gold_set,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def add_exposures(
        self,
        scope: ProjectScope,
        exposures: tuple[GoldExposure, ...],
    ) -> None:
        for exposure in exposures:
            scope.ensure(exposure.project_id)
            self._session.add(
                GoldExposureModel(
                    id=exposure.exposure_id,
                    project_id=exposure.project_id,
                    gold_set_id=exposure.gold_set_id,
                    gold_item_id=exposure.gold_item_id,
                    annotator_id=exposure.annotator_id,
                    task_id=exposure.task_id,
                    rotation_marker=exposure.rotation_marker,
                    exposed_at=exposure.exposed_at,
                )
            )
        flush_or_duplicate(self._session)

    def count_exposures(
        self,
        scope: ProjectScope,
        *,
        gold_set_id: UUID,
        gold_item_id: UUID,
        annotator_id: UUID,
        task_id: UUID | None = None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(GoldExposureModel)
            .where(
                GoldExposureModel.project_id == scope.project_id,
                GoldExposureModel.gold_set_id == gold_set_id,
                GoldExposureModel.gold_item_id == gold_item_id,
                GoldExposureModel.annotator_id == annotator_id,
            )
        )
        if task_id is not None:
            statement = statement.where(GoldExposureModel.task_id == task_id)
        return int(self._session.scalar(statement) or 0)

    def list_exposures(
        self,
        scope: ProjectScope,
        *,
        gold_set_id: UUID,
        annotator_id: UUID,
    ) -> list[GoldExposure]:
        rows = self._session.scalars(
            scoped_select(GoldExposureModel, scope)
            .where(
                GoldExposureModel.gold_set_id == gold_set_id,
                GoldExposureModel.annotator_id == annotator_id,
            )
            .order_by(GoldExposureModel.exposed_at)
        )
        return [self._exposure_from_row(row) for row in rows]

    def add_evaluation(
        self,
        scope: ProjectScope,
        record: GoldEvaluationRecord,
    ) -> GoldEvaluationRecord:
        scope.ensure(record.project_id)
        self._session.add(
            GoldEvaluationModel(
                id=record.evaluation_id,
                project_id=record.project_id,
                gold_set_id=record.gold_set_id,
                task_id=record.task_id,
                submission_id=record.submission_id,
                evaluated_count=record.evaluation.evaluated_count,
                passed_count=record.evaluation.passed_count,
                accuracy=record.evaluation.accuracy,
                status=record.evaluation.status,
                results=[item.to_payload() for item in record.evaluation.results],
                computed_at=record.computed_at,
            )
        )
        flush_or_duplicate(self._session)
        return record

    def get_evaluation(
        self,
        scope: ProjectScope,
        evaluation_id: UUID,
    ) -> GoldEvaluationRecord | None:
        row = self._session.scalar(
            select(GoldEvaluationModel).where(
                GoldEvaluationModel.id == evaluation_id,
                GoldEvaluationModel.project_id == scope.project_id,
            )
        )
        if row is None:
            return None
        evaluation = GoldEvaluation(
            evaluated_count=row.evaluated_count,
            passed_count=row.passed_count,
            accuracy=float(row.accuracy) if row.accuracy is not None else None,
            status=row.status,
            results=tuple(GoldItemResult(**item) for item in (row.results or ())),
        )
        return GoldEvaluationRecord(
            evaluation_id=row.id,
            project_id=row.project_id,
            gold_set_id=row.gold_set_id,
            task_id=row.task_id,
            submission_id=row.submission_id,
            evaluation=evaluation,
            computed_at=as_utc(row.computed_at) or datetime.now(UTC),
        )

    def _from_row(self, row: GoldSetModel) -> GoldSet:
        return GoldSet(
            gold_set_id=row.id,
            project_id=row.project_id,
            code=row.code,
            name=row.name,
            status=row.status,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            schema_sha256=row.schema_sha256,
            blind=row.blind,
            items=tuple(_gold_item_from_row(item) for item in (row.items or ())),
            exposure_policy=row.exposure_policy,
            max_exposure_per_annotator=row.max_exposure_per_annotator,
            rotation_policy=row.rotation_policy,
            usage_policy=row.usage_policy or {},
            published_at=as_utc(row.published_at),
            retired_at=as_utc(row.retired_at),
            revoked_reason=row.revoked_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )

    def _exposure_from_row(self, row: GoldExposureModel) -> GoldExposure:
        return GoldExposure(
            exposure_id=row.id,
            project_id=row.project_id,
            gold_set_id=row.gold_set_id,
            gold_item_id=row.gold_item_id,
            annotator_id=row.annotator_id,
            task_id=row.task_id,
            rotation_marker=row.rotation_marker,
            exposed_at=as_utc(row.exposed_at) or datetime.now(UTC),
        )


class SqlQualitySampleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, sample: QualitySample) -> QualitySample:
        scope.ensure(sample.project_id)
        ensure_initial_version(sample.version)
        now = datetime.now(UTC)
        self._session.add(
            QualitySampleModel(
                id=sample.quality_sample_id,
                project_id=sample.project_id,
                task_id=sample.task_id,
                submission_id=sample.submission_id,
                annotator_id=sample.annotator_id,
                status=enum_value(sample.status),
                sampling_policy_version=sample.sampling_policy_version,
                sample_type=sample.sample_type.value,
                stratum=sample.stratum,
                sample_reason=sample.sample_reason,
                item_refs=_uuid_json(sample.item_refs),
                reviewer_id=sample.reviewer_id,
                review_id=sample.review_id,
                arbitration_id=sample.arbitration_id,
                due_at=sample.due_at,
                outcome=sample.outcome.value if sample.outcome else None,
                reason=sample.reason,
                closed_at=sample.closed_at,
                sample_metadata=dict(sample.metadata),
                version=sample.version,
                created_at=sample.created_at or now,
                updated_at=sample.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return sample

    def get(self, scope: ProjectScope, sample_id: UUID) -> QualitySample | None:
        row = self._session.scalar(
            select(QualitySampleModel).where(
                QualitySampleModel.id == sample_id,
                QualitySampleModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[QualitySample]:
        if limit < 1 or limit > 501:
            raise DomainValidationError("limit 必须在 1 到 501 之间")
        statement = scoped_select(QualitySampleModel, scope).where(
            QualitySampleModel.task_id == task_id
        )
        if status is not None:
            statement = statement.where(QualitySampleModel.status == status)
        after_clause = keyset_after(QualitySampleModel.id, after_id)
        if after_clause is not None:
            statement = statement.where(after_clause)
        statement = statement.order_by(QualitySampleModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        sample: QualitySample,
        *,
        expected_version: int,
    ) -> QualitySample:
        scope.ensure(sample.project_id)
        ensure_expected_version(sample.version, expected_version)
        now = sample.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(QualitySampleModel)
            .where(
                QualitySampleModel.id == sample.quality_sample_id,
                QualitySampleModel.project_id == scope.project_id,
                QualitySampleModel.version == expected_version,
            )
            .values(
                status=enum_value(sample.status),
                reviewer_id=sample.reviewer_id,
                review_id=sample.review_id,
                arbitration_id=sample.arbitration_id,
                outcome=sample.outcome.value if sample.outcome else None,
                reason=sample.reason,
                closed_at=sample.closed_at,
                sample_metadata=dict(sample.metadata),
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                QualitySampleModel,
                scope,
                sample.quality_sample_id,
                "质检抽检",
            )
        return replace(
            sample,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: QualitySampleModel) -> QualitySample:
        return QualitySample(
            quality_sample_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            submission_id=row.submission_id,
            annotator_id=row.annotator_id,
            sampling_policy_version=row.sampling_policy_version,
            sample_type=row.sample_type,
            sample_reason=row.sample_reason,
            status=row.status,
            stratum=row.stratum,
            item_refs=_uuid_tuple(row.item_refs),
            reviewer_id=row.reviewer_id,
            review_id=row.review_id,
            arbitration_id=row.arbitration_id,
            due_at=as_utc(row.due_at),
            outcome=row.outcome,
            reason=row.reason,
            closed_at=as_utc(row.closed_at),
            metadata=row.sample_metadata or {},
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlIAAReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, report: IAAReportRecord) -> IAAReportRecord:
        scope.ensure(report.project_id)
        ensure_initial_version(report.version)
        now = datetime.now(UTC)
        self._session.add(
            IAAReportModel(
                id=report.report_id,
                project_id=report.project_id,
                task_id=report.task_id,
                schema_id=report.schema_id,
                schema_version=report.schema_version,
                status=report.status,
                metric_definitions_version=report.metric_definitions_version,
                sample_item_count=report.sample_item_count,
                overlap_item_count=report.overlap_item_count,
                annotator_count=report.annotator_count,
                excluded_items=[dict(item) for item in report.excluded_items],
                metrics=[dict(item) for item in report.metrics],
                computed_at=report.computed_at,
                version=report.version,
                created_at=now,
                updated_at=now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return report

    def get(self, scope: ProjectScope, report_id: UUID) -> IAAReportRecord | None:
        row = self._session.scalar(
            select(IAAReportModel).where(
                IAAReportModel.id == report_id,
                IAAReportModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[IAAReportRecord]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(IAAReportModel, scope).where(IAAReportModel.task_id == task_id)
        after_clause = keyset_after_created_desc(
            IAAReportModel,
            IAAReportModel.created_at,
            IAAReportModel.id,
            after_id,
        )
        if after_clause is not None:
            statement = statement.where(after_clause)
        rows = self._session.scalars(
            statement.order_by(
                IAAReportModel.created_at.desc(),
                IAAReportModel.id.desc(),
            ).limit(limit)
        )
        return [self._from_row(row) for row in rows]

    def save(
        self,
        scope: ProjectScope,
        report: IAAReportRecord,
        *,
        expected_version: int,
    ) -> IAAReportRecord:
        scope.ensure(report.project_id)
        ensure_expected_version(report.version, expected_version)
        now = datetime.now(UTC)
        result = self._session.execute(
            update(IAAReportModel)
            .where(
                IAAReportModel.id == report.report_id,
                IAAReportModel.project_id == scope.project_id,
                IAAReportModel.version == expected_version,
            )
            .values(
                status=report.status,
                sample_item_count=report.sample_item_count,
                overlap_item_count=report.overlap_item_count,
                annotator_count=report.annotator_count,
                excluded_items=[dict(item) for item in report.excluded_items],
                metrics=[dict(item) for item in report.metrics],
                computed_at=report.computed_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                IAAReportModel,
                scope,
                report.report_id,
                "IAA 报告",
            )
        return replace(
            report,
            version=expected_version + 1,
            computed_at=report.computed_at,
        )

    def _from_row(self, row: IAAReportModel) -> IAAReportRecord:
        return IAAReportRecord(
            report_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            status=row.status,
            metric_definitions_version=row.metric_definitions_version,
            sample_item_count=row.sample_item_count,
            overlap_item_count=row.overlap_item_count,
            annotator_count=row.annotator_count,
            metrics=tuple(row.metrics or ()),
            excluded_items=tuple(row.excluded_items or ()),
            computed_at=as_utc(row.computed_at),
            version=row.version,
        )
