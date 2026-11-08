"""标注应用层端口。

所有仓储方法必须接收服务端构造的 :class:`ProjectScope`；按 ID 查询必须带
项目过滤，跨项目资源返回 ``None`` 并由用例统一映射为 404。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.annotation.arbitration import AnnotationArbitration
from app.domain.annotation.assignment import AnnotationAssignment
from app.domain.annotation.batch import AnnotationBatch
from app.domain.annotation.gold_set import GoldEvaluation, GoldSet
from app.domain.annotation.quality_sample import QualitySample
from app.domain.annotation.review import AnnotationReview
from app.domain.annotation.sample_set import AnnotationSampleSet, SampleEpisodeRef
from app.domain.annotation.schema import AnnotationSchema
from app.domain.annotation.submission import AnnotationRecord, AnnotationSubmission
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class EpisodeCandidate:
    """样本选择所需的 Episode 只读投影。"""

    episode_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    status: str
    quality_status: str
    manifest_sha256: str | None
    scene_labels: tuple[str, ...]
    robot_id: UUID | None
    device_ids: tuple[UUID, ...]
    sensor_ids: tuple[UUID, ...]
    captured_at: datetime | None
    has_lineage: bool = False
    calibration_count: int = 0


@dataclass(frozen=True, slots=True)
class WorkloadAggregate:
    assigned_item_count: int = 0
    claimed_item_count: int = 0
    submitted_item_count: int = 0
    approved_item_count: int = 0
    rework_item_count: int = 0
    total_minutes: int = 0


class AnnotationSchemaRepository(Protocol):
    def add(self, scope: ProjectScope, schema: AnnotationSchema) -> AnnotationSchema: ...

    def get(self, scope: ProjectScope, schema_id: UUID) -> AnnotationSchema | None: ...

    def get_by_lineage_version(
        self,
        scope: ProjectScope,
        *,
        schema_lineage_id: UUID,
        schema_version: int,
    ) -> AnnotationSchema | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationSchema]: ...

    def save(
        self,
        scope: ProjectScope,
        schema: AnnotationSchema,
        *,
        expected_version: int,
    ) -> AnnotationSchema: ...


class AnnotationSampleSetRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        sample_set: AnnotationSampleSet,
    ) -> AnnotationSampleSet: ...

    def get(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
    ) -> AnnotationSampleSet | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> AnnotationSampleSet | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationSampleSet]: ...

    def save(
        self,
        scope: ProjectScope,
        sample_set: AnnotationSampleSet,
        *,
        expected_version: int,
    ) -> AnnotationSampleSet: ...

    def replace_refs(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        refs: tuple[SampleEpisodeRef, ...],
    ) -> None: ...

    def list_refs(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
    ) -> list[SampleEpisodeRef]: ...


class AnnotationTaskRepository(Protocol):
    def add(self, scope: ProjectScope, task: AnnotationTask) -> AnnotationTask: ...

    def get(self, scope: ProjectScope, task_id: UUID) -> AnnotationTask | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> AnnotationTask | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationTask]: ...

    def save(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        *,
        expected_version: int,
    ) -> AnnotationTask: ...

    def list_for_sample_set(
        self,
        scope: ProjectScope,
        *,
        sample_set_id: UUID,
        active_only: bool = True,
    ) -> list[AnnotationTask]: ...

    def count_active_for_sample_set(
        self,
        scope: ProjectScope,
        *,
        sample_set_id: UUID,
    ) -> int: ...


class AnnotationBatchRepository(Protocol):
    def add(self, scope: ProjectScope, batch: AnnotationBatch) -> AnnotationBatch: ...

    def get(self, scope: ProjectScope, batch_id: UUID) -> AnnotationBatch | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationBatch]: ...

    def list_non_terminal(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
    ) -> list[AnnotationBatch]: ...

    def next_sequence_no(self, scope: ProjectScope, task_id: UUID) -> int: ...

    def sum_planned_item_count(self, scope: ProjectScope, task_id: UUID) -> int: ...

    def save(
        self,
        scope: ProjectScope,
        batch: AnnotationBatch,
        *,
        expected_version: int,
    ) -> AnnotationBatch: ...


class AnnotationAssignmentRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        assignment: AnnotationAssignment,
    ) -> AnnotationAssignment: ...

    def get(
        self,
        scope: ProjectScope,
        assignment_id: UUID,
    ) -> AnnotationAssignment | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        assignee_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AnnotationAssignment]: ...

    def save(
        self,
        scope: ProjectScope,
        assignment: AnnotationAssignment,
        *,
        expected_version: int,
    ) -> AnnotationAssignment: ...

    def list_for_batch(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
    ) -> list[AnnotationAssignment]: ...

    def list_active_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
    ) -> list[AnnotationAssignment]: ...

    def has_conflicting_role(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
        assignee_id: UUID,
        assignment_role: str,
    ) -> bool: ...

    def sum_open_planned_minutes(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        exclude_assignment_id: UUID | None = None,
    ) -> int: ...

    def sum_open_planned_items(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        exclude_assignment_id: UUID | None = None,
    ) -> int: ...

    def add_event(
        self,
        scope: ProjectScope,
        *,
        event_id: UUID,
        assignment: AnnotationAssignment,
        action: str,
        from_status: str | None,
        actor_id: UUID,
        reason: str | None,
        minutes: int,
        occurred_at: datetime,
    ) -> None: ...

    def aggregate_workload(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        period_start: datetime,
        period_end: datetime,
    ) -> WorkloadAggregate: ...


class EpisodeDirectory(Protocol):
    def list_candidates(
        self,
        scope: ProjectScope,
        *,
        statuses: tuple[str, ...],
        quality_statuses: tuple[str, ...],
        include_ids: tuple[UUID, ...] = (),
        exclude_ids: tuple[UUID, ...] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 500,
    ) -> list[EpisodeCandidate]: ...

    def episode_bounds(
        self,
        scope: ProjectScope,
        *,
        episode_ids: tuple[UUID, ...],
    ) -> Mapping[UUID, tuple[int, int]]: ...


@dataclass(frozen=True, slots=True)
class RecordObservation:
    """IAA / 黄金集评估使用的只读投影。"""

    record: AnnotationRecord
    submission_id: UUID
    annotator_id: UUID


@dataclass(frozen=True, slots=True)
class GoldExposure:
    exposure_id: UUID
    project_id: UUID
    gold_set_id: UUID
    gold_item_id: UUID
    annotator_id: UUID
    task_id: UUID
    rotation_marker: str
    exposed_at: datetime


@dataclass(frozen=True, slots=True)
class GoldEvaluationRecord:
    evaluation_id: UUID
    project_id: UUID
    gold_set_id: UUID
    task_id: UUID
    submission_id: UUID
    evaluation: GoldEvaluation
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class IAAReportRecord:
    report_id: UUID
    project_id: UUID
    task_id: UUID
    schema_id: UUID
    schema_version: int
    status: str
    metric_definitions_version: str
    sample_item_count: int
    overlap_item_count: int
    annotator_count: int
    metrics: tuple[Mapping[str, object], ...]
    excluded_items: tuple[Mapping[str, object], ...]
    computed_at: datetime | None
    version: int = 1


class AnnotationSubmissionRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> AnnotationSubmission: ...

    def get(self, scope: ProjectScope, submission_id: UUID) -> AnnotationSubmission | None: ...

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[AnnotationSubmission]: ...

    def list_for_assignment(
        self,
        scope: ProjectScope,
        *,
        assignment_id: UUID,
    ) -> list[AnnotationSubmission]: ...

    def latest_for_assignment(
        self,
        scope: ProjectScope,
        *,
        assignment_id: UUID,
    ) -> AnnotationSubmission | None: ...

    def next_version(self, scope: ProjectScope, assignment_id: UUID) -> int: ...

    def save(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        *,
        expected_version: int,
    ) -> AnnotationSubmission: ...


class AnnotationRecordRepository(Protocol):
    def add_many(
        self,
        scope: ProjectScope,
        records: tuple[AnnotationRecord, ...],
    ) -> None: ...

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationRecord]: ...

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        statuses: tuple[str, ...],
    ) -> list[RecordObservation]: ...

    def save(
        self,
        scope: ProjectScope,
        record: AnnotationRecord,
        *,
        expected_version: int,
    ) -> AnnotationRecord: ...


class AnnotationReviewRepository(Protocol):
    def add(self, scope: ProjectScope, review: AnnotationReview) -> AnnotationReview: ...

    def get(self, scope: ProjectScope, review_id: UUID) -> AnnotationReview | None: ...

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationReview]: ...

    def first_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> AnnotationReview | None: ...

    def next_round(self, scope: ProjectScope, submission_id: UUID) -> int: ...

    def save(
        self,
        scope: ProjectScope,
        review: AnnotationReview,
        *,
        expected_version: int,
    ) -> AnnotationReview: ...


class AnnotationArbitrationRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        arbitration: AnnotationArbitration,
    ) -> AnnotationArbitration: ...

    def get(
        self,
        scope: ProjectScope,
        arbitration_id: UUID,
    ) -> AnnotationArbitration | None: ...

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationArbitration]: ...

    def save(
        self,
        scope: ProjectScope,
        arbitration: AnnotationArbitration,
        *,
        expected_version: int,
    ) -> AnnotationArbitration: ...


class GoldSetRepository(Protocol):
    def add(self, scope: ProjectScope, gold_set: GoldSet) -> GoldSet: ...

    def get(self, scope: ProjectScope, gold_set_id: UUID) -> GoldSet | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> GoldSet | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[GoldSet]: ...

    def save(
        self,
        scope: ProjectScope,
        gold_set: GoldSet,
        *,
        expected_version: int,
    ) -> GoldSet: ...

    def add_exposures(
        self,
        scope: ProjectScope,
        exposures: tuple[GoldExposure, ...],
    ) -> None: ...

    def count_exposures(
        self,
        scope: ProjectScope,
        *,
        gold_set_id: UUID,
        gold_item_id: UUID,
        annotator_id: UUID,
        task_id: UUID | None = None,
    ) -> int: ...

    def list_exposures(
        self,
        scope: ProjectScope,
        *,
        gold_set_id: UUID,
        annotator_id: UUID,
    ) -> list[GoldExposure]: ...

    def add_evaluation(
        self,
        scope: ProjectScope,
        record: GoldEvaluationRecord,
    ) -> GoldEvaluationRecord: ...

    def get_evaluation(
        self,
        scope: ProjectScope,
        evaluation_id: UUID,
    ) -> GoldEvaluationRecord | None: ...


class QualitySampleRepository(Protocol):
    def add(self, scope: ProjectScope, sample: QualitySample) -> QualitySample: ...

    def get(self, scope: ProjectScope, sample_id: UUID) -> QualitySample | None: ...

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: str | None = None,
        limit: int = 100,
    ) -> list[QualitySample]: ...

    def save(
        self,
        scope: ProjectScope,
        sample: QualitySample,
        *,
        expected_version: int,
    ) -> QualitySample: ...


class IAAReportRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        report: IAAReportRecord,
    ) -> IAAReportRecord: ...

    def get(self, scope: ProjectScope, report_id: UUID) -> IAAReportRecord | None: ...

    def list_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        limit: int = 20,
    ) -> list[IAAReportRecord]: ...

    def save(
        self,
        scope: ProjectScope,
        report: IAAReportRecord,
        *,
        expected_version: int,
    ) -> IAAReportRecord: ...


class GoldSetAccessPolicy(Protocol):
    def can_view_answers(self, scope: ProjectScope, gold_set_id: UUID) -> bool: ...


class MemberDirectory(Protocol):
    def user_is_member(self, scope: ProjectScope, user_id: UUID) -> bool: ...


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    details: Mapping[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: UUID
    project_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: Mapping[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    project_id: UUID
    user_id: UUID
    key: str
    method: str
    path: str
    request_hash: str
    response_status: int
    response_ref: str | None
    created_at: datetime
    expires_at: datetime


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


class IdempotencyRepository(Protocol):
    def get(self, scope: ProjectScope, key: str) -> IdempotencyRecord | None: ...

    def save(self, scope: ProjectScope, record: IdempotencyRecord) -> None: ...

    def delete(self, scope: ProjectScope, key: str) -> None: ...
