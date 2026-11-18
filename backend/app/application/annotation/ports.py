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

from app.application.audit import OutboxEvent
from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.annotation.ai_suggestion import AiSuggestion
from app.domain.annotation.arbitration import AnnotationArbitration
from app.domain.annotation.assignment import AnnotationAssignment
from app.domain.annotation.batch import AnnotationBatch
from app.domain.annotation.gold_set import GoldEvaluation, GoldSet
from app.domain.annotation.quality_check import QualityCheckRun
from app.domain.annotation.quality_sample import QualitySample
from app.domain.annotation.review import AnnotationReview
from app.domain.annotation.sample_set import AnnotationSampleSet, SampleEpisodeRef
from app.domain.annotation.schema import AnnotationSchema
from app.domain.annotation.submission import AnnotationRecord, AnnotationSubmission
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class LineageRecorder(Protocol):
    """标注域需要的**血缘写边能力**（只暴露写，不暴露遍历）。

    与 ``app.application.datasets.ports.LineageRecorder`` 同构：节点必须先登记
    才能写边（复合外键兜底），边写入幂等。标注域只关心
    ``Episode --ANNOTATED_BY--> ANNOTATION`` 与
    ``Episode --EVALUATED_BY--> QUALITY_RUN`` 两类边，不引入通用图操作。
    """

    def ensure_node(
        self,
        scope: ProjectScope,
        *,
        node_type: object,
        node_id: UUID,
        now: datetime,
    ) -> object: ...

    def ensure_edge(
        self,
        scope: ProjectScope,
        *,
        from_node_type: object,
        from_node_id: UUID,
        to_node_type: object,
        to_node_id: UUID,
        relation: object,
        metadata: Mapping[str, object],
        now: datetime,
    ) -> bool: ...


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
        overdue: bool | None = None,
        now: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationTask]: ...

    def count_overdue(self, now: datetime) -> int:
        """跨项目统计逾期任务数；只读，供巡检 worker 观测。"""
        ...

    def save(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        *,
        expected_version: int,
    ) -> AnnotationTask: ...

    def update_progress(
        self,
        scope: ProjectScope,
        task_id: UUID,
        progress: dict[str, int],
    ) -> None:
        """写入派生的进度；不推进乐观锁版本。"""
        ...

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

    def update_counts(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        counts: dict[str, int],
    ) -> None:
        """写入派生的提交/通过/驳回计数；不推进乐观锁版本。"""
        ...

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

    def exclusion_summary(
        self,
        scope: ProjectScope,
        *,
        statuses: tuple[str, ...],
        quality_statuses: tuple[str, ...],
        include_ids: tuple[UUID, ...] = (),
        exclude_ids: tuple[UUID, ...] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
    ) -> Mapping[str, int]:
        """按门禁原因统计未进入候选池的 Episode 数量。"""
        ...

    def episode_bounds(
        self,
        scope: ProjectScope,
        *,
        episode_ids: tuple[UUID, ...],
    ) -> Mapping[UUID, tuple[int, int]]: ...

    def apply_annotation_status(
        self,
        scope: ProjectScope,
        *,
        episode_ids: tuple[UUID, ...],
        status: str,
        now: datetime,
    ) -> int:
        """把 Episode 的 ``annotation_status`` 投影更新为给定状态。

        返回实际更新的行数。**只推进、不回退**：已 ``APPROVED`` 的 Episode
        不会被后续驳回退回（同一 Episode 可能有多个标注提交，先批准的那份
        就不该被后来的驳回覆盖）。

        为什么投影写在标注域：``annotation_status`` 的唯一权威来源是标注
        流程，Episode 只是它的读模型。让 Episode 域自己推断"我该是什么标注
        状态"等于把同一规则实现两遍。
        """
        ...

    def list_by_ids(
        self,
        scope: ProjectScope,
        *,
        episode_ids: tuple[UUID, ...],
    ) -> list[EpisodeCandidate]:
        """按 ID 取 Episode 投影，**不套用任何门禁过滤**。

        用于校验显式点名（``include_episode_ids``）的 Episode：这类 Episode 若
        被门禁挡下，诊断不能依赖"它没出现在候选池里"这种间接证据，必须拿到
        它的真实状态再交给 ``domain.annotation.selection.ineligibility_reason``
        判定，避免门禁规则出现第二份实现。
        """
        ...


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
        after_id: UUID | None = None,
        visible_only: bool = False,
        limit: int = 100,
    ) -> list[AnnotationSubmission]: ...

    def list_for_assignment(
        self,
        scope: ProjectScope,
        *,
        assignment_id: UUID,
    ) -> list[AnnotationSubmission]: ...

    def list_for_batch(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
    ) -> list[AnnotationSubmission]:
        """批次下全部提交（进度投影按批次汇总计数用）。"""
        ...

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

    def get(self, scope: ProjectScope, annotation_id: UUID) -> AnnotationRecord | None: ...

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AnnotationRecord]: ...

    def list_for_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        limit: int = 2000,
    ) -> list[AnnotationRecord]:
        """该 Episode 的标注记录（跨提交/跨任务），有界只读查询。

        统一质量引擎按 Episode 汇总标注规则；``limit`` 是硬上限，调用方
        多取一条即可判断是否超限，避免 HTTP 线程处理无界记录集。
        """
        ...

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


class AnnotationAiSuggestionRepository(Protocol):
    """AI 预标注建议仓储；与标注记录物理分离，项目隔离由 ``ProjectScope`` 强制。"""

    def add_many(
        self,
        scope: ProjectScope,
        suggestions: tuple[AiSuggestion, ...],
    ) -> None: ...

    def get(self, scope: ProjectScope, suggestion_id: UUID) -> AiSuggestion | None: ...

    def list_for_annotation(
        self,
        scope: ProjectScope,
        annotation_id: UUID,
    ) -> list[AiSuggestion]: ...

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AiSuggestion]: ...

    def save(
        self,
        scope: ProjectScope,
        suggestion: AiSuggestion,
        *,
        expected_version: int,
    ) -> AiSuggestion: ...


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
        after_id: UUID | None = None,
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
        after_id: UUID | None = None,
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

    def user_role(self, scope: ProjectScope, user_id: UUID) -> str | None: ...


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


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


# 幂等记录类型与仓储协议由 application 顶层统一声明，避免四个域各写一份。
# 这里重新导出，保持本域既有的 ``from app.application.<domain>.ports import
# IdempotencyRecord`` 调用点不变。
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository


class QualityCheckRepository(Protocol):
    """质量检查执行记录仓储：只追加，不更新、不删除。

    查询一律按 ``project_id`` 过滤；``get``/``list_for_submission`` 跨项目返回
    空结果，由用例映射成 404。
    """

    def add(
        self,
        scope: ProjectScope,
        record: QualityCheckRun,
    ) -> QualityCheckRun: ...

    def get(self, scope: ProjectScope, run_id: UUID) -> QualityCheckRun | None: ...

    def list_for_submission(
        self,
        scope: ProjectScope,
        *,
        submission_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityCheckRun]: ...
