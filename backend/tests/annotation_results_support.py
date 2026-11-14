"""标注结果域测试夹具：真实 SQLite 约束下的完整任务链路。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.annotation.arbitrations import AnnotationArbitrationUseCases
from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.application.annotation.batches import AnnotationBatchUseCases
from app.application.annotation.commands import (
    NewAnnotationAssignment,
    NewAnnotationBatch,
    NewAnnotationSampleSet,
    NewAnnotationSchema,
    NewAnnotationSubmission,
    NewAnnotationTask,
)
from app.application.annotation.gold_sets import GoldSetUseCases
from app.application.annotation.iaa import IAAUseCases
from app.application.annotation.quality_samples import QualitySampleUseCases
from app.application.annotation.reviews import AnnotationReviewUseCases
from app.application.annotation.sample_sets import AnnotationSampleSetUseCases
from app.application.annotation.schemas import AnnotationSchemaUseCases
from app.application.annotation.submissions import AnnotationSubmissionUseCases
from app.application.annotation.tasks import AnnotationTaskUseCases
from app.domain.annotation import AnnotationSampleSet, SampleEpisodeRef
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.repositories.annotation_assignments import (
    SqlAnnotationAssignmentRepository,
)
from app.infrastructure.repositories.annotation_audit import (
    SqlAnnotationAuditRepository,
    SqlAnnotationIdempotencyRepository,
    SqlAnnotationOutboxRepository,
)
from app.infrastructure.repositories.annotation_batches import SqlAnnotationBatchRepository
from app.infrastructure.repositories.annotation_directory import (
    SqlEpisodeDirectory,
    SqlMemberDirectory,
)
from app.infrastructure.repositories.annotation_quality import (
    SqlGoldSetRepository,
    SqlIAAReportRepository,
    SqlQualitySampleRepository,
)
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationArbitrationRepository,
    SqlAnnotationRecordRepository,
    SqlAnnotationReviewRepository,
    SqlAnnotationSubmissionRepository,
)
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import SqlAnnotationSchemaRepository
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from sqlalchemy.orm import Session

# 夹具时间必须锚定真实时钟：领用租约与到期判断用 datetime.now(UTC)，
# 写死日期会让 lease_expires_at 在当天之后立即判为过期（CI 于 2026-11-14 暴露）。
# 截断到整秒，避免不同调用间微秒漂移影响快照类断言。
NOW = datetime.now(UTC).replace(microsecond=0)
SHA = "sha256:" + "a" * 64
EPISODE_START = 1_000
EPISODE_END = 2_000


class SessionUow:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()


@dataclass(frozen=True)
class SeedIds:
    project_a: UUID
    project_b: UUID
    user_a: UUID
    user_b: UUID
    user_c: UUID
    episode_a: UUID
    episode_a2: UUID
    episode_b: UUID


@dataclass
class AnnotationStacks:
    session: Session
    ids: SeedIds
    scope_a: ProjectScope
    scope_b: ProjectScope
    scope_c: ProjectScope
    schemas_repo: SqlAnnotationSchemaRepository
    sample_sets_repo: SqlAnnotationSampleSetRepository
    tasks_repo: SqlAnnotationTaskRepository
    batches_repo: SqlAnnotationBatchRepository
    assignments_repo: SqlAnnotationAssignmentRepository
    submissions_repo: SqlAnnotationSubmissionRepository
    records_repo: SqlAnnotationRecordRepository
    reviews_repo: SqlAnnotationReviewRepository
    arbitrations_repo: SqlAnnotationArbitrationRepository
    gold_sets_repo: SqlGoldSetRepository
    quality_repo: SqlQualitySampleRepository
    iaa_repo: SqlIAAReportRepository
    audit_repo: SqlAnnotationAuditRepository
    outbox_repo: SqlAnnotationOutboxRepository
    idempotency_repo: SqlAnnotationIdempotencyRepository
    uow: SessionUow
    schemas: AnnotationSchemaUseCases
    sample_sets: AnnotationSampleSetUseCases
    tasks: AnnotationTaskUseCases
    batches: AnnotationBatchUseCases
    assignments: AnnotationAssignmentUseCases
    submissions: AnnotationSubmissionUseCases
    reviews: AnnotationReviewUseCases
    arbitrations: AnnotationArbitrationUseCases
    gold_sets: GoldSetUseCases
    quality_samples: QualitySampleUseCases
    iaa: IAAUseCases
    schema_id: UUID
    task_id: UUID
    batch_id: UUID
    assignment_a: UUID
    assignment_b: UUID


class AllowManagedAnswers:
    """测试用访问策略：项目 A 的管理者（用户 B）可看答案。"""

    def __init__(self, manager_id: UUID) -> None:
        self._manager_id = manager_id

    def can_view_answers(self, scope: ProjectScope, gold_set_id: UUID) -> bool:
        return scope.actor_id == self._manager_id


def seed_identity(session: Session) -> SeedIds:
    ids = SeedIds(
        project_a=uuid4(),
        project_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
        user_c=uuid4(),
        episode_a=uuid4(),
        episode_a2=uuid4(),
        episode_b=uuid4(),
    )
    session.add_all(
        [
            Project(id=ids.project_a, code="project-a", name="项目 A"),
            Project(id=ids.project_b, code="project-b", name="项目 B"),
            User(id=ids.user_a, display_name="甲"),
            User(id=ids.user_b, display_name="乙"),
            User(id=ids.user_c, display_name="丙"),
        ]
    )
    session.flush()
    session.add_all(
        [
            ProjectMember(
                project_id=ids.project_a,
                user_id=ids.user_a,
                role=ProjectRole.EDITOR.value,
            ),
            ProjectMember(
                project_id=ids.project_a,
                user_id=ids.user_b,
                role=ProjectRole.OWNER.value,
            ),
            ProjectMember(
                project_id=ids.project_a,
                user_id=ids.user_c,
                role=ProjectRole.EDITOR.value,
            ),
            ProjectMember(
                project_id=ids.project_b,
                user_id=ids.user_b,
                role=ProjectRole.OWNER.value,
            ),
        ]
    )
    for project_id, user_id, episode_id, suffix in (
        (ids.project_a, ids.user_a, ids.episode_a, "a"),
        (ids.project_b, ids.user_b, ids.episode_b, "b"),
    ):
        session.add(
            EpisodeModel(
                id=episode_id,
                project_id=project_id,
                episode_lineage_id=uuid4(),
                episode_version=1,
                code=f"EP-{suffix}",
                status="PENDING",
                quality_status="NOT_CHECKED",
                annotation_status="NOT_STARTED",
                start_time_ns=EPISODE_START,
                end_time_ns=EPISODE_END,
                duration_ns=EPISODE_END - EPISODE_START,
                scene_labels=["kitchen"],
                time_domain="UTC",
                time_base={},
                canonical_schema_version="ddp.canonical/1.0.0-draft",
                pipeline_id="parser",
                pipeline_version="1.0.0",
                segmentation_rule_version="seg-v1",
                calibration_snapshot=[],
                manifest_key=f"canonical/{project_id}/{episode_id}/1/manifest.json",
                manifest_sha256=SHA,
                frame_count=10,
                attempt=1,
                max_attempts=3,
                created_at=NOW,
                updated_at=NOW,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.flush()
        from sqlalchemy import update

        for status, quality in (
            ("PROCESSING", "NOT_CHECKED"),
            ("READY", "NOT_CHECKED"),
            ("QUALITY_CHECKING", "PASS"),
            ("ACCEPTED", "PASS"),
        ):
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status=status, quality_status=quality)
            )
    session.commit()
    return ids


def build_stacks(session: Session, ids: SeedIds) -> AnnotationStacks:
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_a, actor_id=ids.user_b)
    scope_c = ProjectScope(ids.project_a, actor_id=ids.user_c)
    schemas_repo = SqlAnnotationSchemaRepository(session)
    sample_sets_repo = SqlAnnotationSampleSetRepository(session)
    tasks_repo = SqlAnnotationTaskRepository(session)
    batches_repo = SqlAnnotationBatchRepository(session)
    assignments_repo = SqlAnnotationAssignmentRepository(session)
    submissions_repo = SqlAnnotationSubmissionRepository(session)
    records_repo = SqlAnnotationRecordRepository(session)
    reviews_repo = SqlAnnotationReviewRepository(session)
    arbitrations_repo = SqlAnnotationArbitrationRepository(session)
    gold_sets_repo = SqlGoldSetRepository(session)
    quality_repo = SqlQualitySampleRepository(session)
    iaa_repo = SqlIAAReportRepository(session)
    audit_repo = SqlAnnotationAuditRepository(session)
    outbox_repo = SqlAnnotationOutboxRepository(session)
    idempotency_repo = SqlAnnotationIdempotencyRepository(session)
    uow = SessionUow(session)
    schemas = AnnotationSchemaUseCases(
        schemas_repo,
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    sample_sets = AnnotationSampleSetUseCases(
        sample_sets_repo,
        tasks_repo,
        SqlEpisodeDirectory(session),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    tasks = AnnotationTaskUseCases(
        tasks_repo,
        sample_sets_repo,
        schemas_repo,
        batches_repo,
        assignments_repo,
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    batches = AnnotationBatchUseCases(
        tasks_repo,
        batches_repo,
        assignments_repo,
        sample_sets_repo,
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    assignments = AnnotationAssignmentUseCases(
        tasks_repo,
        batches_repo,
        assignments_repo,
        SqlMemberDirectory(session),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    submissions = AnnotationSubmissionUseCases(
        submissions_repo,
        records_repo,
        assignments_repo,
        batches_repo,
        tasks_repo,
        reviews_repo,
        arbitrations_repo,
        SqlMemberDirectory(session),
        sample_sets_repo,
        schemas_repo,
        SqlEpisodeDirectory(session),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    reviews = AnnotationReviewUseCases(
        reviews_repo,
        submissions_repo,
        records_repo,
        tasks_repo,
        SqlMemberDirectory(session),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    arbitrations = AnnotationArbitrationUseCases(
        arbitrations_repo,
        submissions_repo,
        records_repo,
        reviews_repo,
        SqlMemberDirectory(session),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    gold_sets = GoldSetUseCases(
        gold_sets_repo,
        schemas_repo,
        submissions_repo,
        records_repo,
        tasks_repo,
        SqlMemberDirectory(session),
        AllowManagedAnswers(ids.user_b),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    quality_samples = QualitySampleUseCases(
        quality_repo,
        submissions_repo,
        records_repo,
        tasks_repo,
        SqlMemberDirectory(session),
        audit_repo,
        outbox_repo,
        idempotency_repo,
        uow,
    )
    iaa = IAAUseCases(
        iaa_repo,
        records_repo,
        submissions_repo,
        tasks_repo,
        schemas_repo,
        audit_repo,
        outbox_repo,
        uow,
    )
    return AnnotationStacks(
        session=session,
        ids=ids,
        scope_a=scope_a,
        scope_b=scope_b,
        scope_c=scope_c,
        schemas_repo=schemas_repo,
        sample_sets_repo=sample_sets_repo,
        tasks_repo=tasks_repo,
        batches_repo=batches_repo,
        assignments_repo=assignments_repo,
        submissions_repo=submissions_repo,
        records_repo=records_repo,
        reviews_repo=reviews_repo,
        arbitrations_repo=arbitrations_repo,
        gold_sets_repo=gold_sets_repo,
        quality_repo=quality_repo,
        iaa_repo=iaa_repo,
        audit_repo=audit_repo,
        outbox_repo=outbox_repo,
        idempotency_repo=idempotency_repo,
        uow=uow,
        schemas=schemas,
        sample_sets=sample_sets,
        tasks=tasks,
        batches=batches,
        assignments=assignments,
        submissions=submissions,
        reviews=reviews,
        arbitrations=arbitrations,
        gold_sets=gold_sets,
        quality_samples=quality_samples,
        iaa=iaa,
        schema_id=UUID(int=0),
        task_id=UUID(int=0),
        batch_id=UUID(int=0),
        assignment_a=UUID(int=0),
        assignment_b=UUID(int=0),
    )


def prepare_ready_task(
    stacks: AnnotationStacks, *, two_annotators: bool = True
) -> AnnotationStacks:
    ids = stacks.ids
    schema = stacks.schemas.create(
        stacks.scope_a,
        NewAnnotationSchema(
            code=f"schema-{uuid4().hex[:8]}",
            name="动作标签 Schema",
            annotation_levels=("EPISODE",),
            labels=(
                {
                    "label_key": "action",
                    "label_type": "ENUM",
                    "required": True,
                    "level": "EPISODE",
                    "value_spec": {"enum_values": ["PICK", "PLACE"]},
                },
                {
                    "label_key": "segment",
                    "label_type": "TIME_RANGE",
                    "required": False,
                    "level": "EPISODE",
                    "value_spec": {"min_duration_ns": 1},
                },
            ),
        ),
        path="/projects/p/annotation-schemas",
    )
    schema = stacks.schemas.apply_action(
        stacks.scope_a,
        schema.schema_id,
        "submit-review",
        expected_version=schema.version,
        path=f"/projects/p/annotation-schemas/{schema.schema_id}/actions/submit-review",
    )
    schema = stacks.schemas.apply_action(
        stacks.scope_b,
        schema.schema_id,
        "publish",
        expected_version=schema.version,
        path=f"/projects/p/annotation-schemas/{schema.schema_id}/actions/publish",
    )
    sample_set = stacks.sample_sets.create(
        stacks.scope_a,
        NewAnnotationSampleSet(code=f"sample-{uuid4().hex[:8]}", filters={}),
        path="/projects/p/annotation-sample-sets",
    )
    preview = stacks.sample_sets.preview(
        stacks.scope_a,
        sample_set.sample_set_id,
        expected_version=sample_set.version,
        path=f"/projects/p/annotation-sample-sets/{sample_set.sample_set_id}/actions/preview",
    )
    snapshot = stacks.sample_sets.snapshot(
        stacks.scope_a,
        sample_set.sample_set_id,
        expected_version=preview.version,
        path=f"/projects/p/annotation-sample-sets/{sample_set.sample_set_id}/actions/snapshot",
    )
    task = stacks.tasks.create(
        stacks.scope_a,
        NewAnnotationTask(
            code=f"task-{uuid4().hex[:8]}",
            name="任务",
            sample_set_id=snapshot.sample_set_id,
            schema_id=schema.schema_id,
            target_item_count=1,
            target_episode_count=1,
            due_at=NOW + timedelta(days=1),
        ),
        path="/projects/p/annotation-tasks",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "submit-review",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/submit-review",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_b,
        task.task_id,
        "approve",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/approve",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "ready",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/ready",
    )
    batch = stacks.batches.create(
        stacks.scope_a,
        task.task_id,
        NewAnnotationBatch(
            planned_item_count=1,
            item_refs=(ids.episode_a,),
            code=f"batch-{uuid4().hex[:8]}",
        ),
        path=f"/projects/p/annotation-tasks/{task.task_id}/annotation-batches",
    )
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "ready",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/ready",
    )
    assignment_a = stacks.assignments.create(
        stacks.scope_a,
        batch.batch_id,
        NewAnnotationAssignment(
            assignee_id=ids.user_a,
            planned_item_count=1,
            planned_minutes=30,
            lease_expires_at=NOW + timedelta(hours=4),
        ),
        path=f"/projects/p/annotation-batches/{batch.batch_id}/annotation-assignments",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "assign",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/assign",
    )
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "assign",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/assign",
    )
    assignment_a = stacks.assignments.apply_action(
        stacks.scope_a,
        assignment_a.assignment_id,
        "claim",
        expected_version=assignment_a.version,
        path="/projects/p/annotation-assignments/a/actions/claim",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "start",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/start",
    )
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "start",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/start",
    )
    assignment_a = stacks.assignments.apply_action(
        stacks.scope_a,
        assignment_a.assignment_id,
        "start",
        expected_version=assignment_a.version,
        path="/projects/p/annotation-assignments/a/actions/start",
    )
    assignment_b = assignment_a
    if two_annotators:
        assignment_b = stacks.assignments.create(
            stacks.scope_b,
            batch.batch_id,
            NewAnnotationAssignment(
                assignee_id=ids.user_b,
                planned_item_count=1,
                planned_minutes=30,
                lease_expires_at=NOW + timedelta(hours=4),
            ),
            path=f"/projects/p/annotation-batches/{batch.batch_id}/annotation-assignments",
        )
        assignment_b = stacks.assignments.apply_action(
            stacks.scope_b,
            assignment_b.assignment_id,
            "claim",
            expected_version=assignment_b.version,
            path="/projects/p/annotation-assignments/b/actions/claim",
        )
        assignment_b = stacks.assignments.apply_action(
            stacks.scope_b,
            assignment_b.assignment_id,
            "start",
            expected_version=assignment_b.version,
            path="/projects/p/annotation-assignments/b/actions/start",
        )
    stacks.schema_id = schema.schema_id
    stacks.task_id = task.task_id
    stacks.batch_id = batch.batch_id
    stacks.assignment_a = assignment_a.assignment_id
    stacks.assignment_b = assignment_b.assignment_id
    return stacks


def record_payload(
    *,
    episode_id: UUID,
    action: str = "PICK",
    segment: tuple[int, int] | None = (1_100, 1_500),
) -> dict[str, object]:
    labels: list[dict[str, object]] = [
        {
            "label_key": "action",
            "value": action,
            "source": "HUMAN",
            "label_status": "CONFIRMED",
        }
    ]
    if segment is not None:
        labels.append(
            {
                "label_key": "segment",
                "value": {
                    "start_time_ns": segment[0],
                    "end_time_ns": segment[1],
                    "time_domain": "UTC",
                },
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        )
    return {
        "episode_id": episode_id,
        "episode_version": 1,
        "labels": labels,
    }


def make_submission(
    stacks: AnnotationStacks,
    scope: ProjectScope,
    assignment_id: UUID,
    *,
    action: str = "PICK",
    segment: tuple[int, int] | None = (1_100, 1_500),
):
    submission = stacks.submissions.create_draft(
        scope,
        NewAnnotationSubmission(
            assignment_id=assignment_id,
            records=(
                record_payload(episode_id=stacks.ids.episode_a, action=action, segment=segment),
            ),
        ),
        path="/projects/p/annotation-submissions",
    )
    return stacks.submissions.apply_action(
        scope,
        submission.submission_id,
        "submit",
        expected_version=submission.version,
        path=f"/projects/p/annotation-submissions/{submission.submission_id}/actions/submit",
    )


def prepare_snapshot_set(stacks: AnnotationStacks) -> AnnotationSampleSet:
    sample_set = AnnotationSampleSet(
        sample_set_id=uuid4(),
        project_id=stacks.ids.project_a,
        code=f"direct-{uuid4().hex[:8]}",
    )
    ref = SampleEpisodeRef(
        project_id=stacks.ids.project_a,
        sample_set_id=sample_set.sample_set_id,
        episode_id=stacks.ids.episode_a,
        episode_lineage_id=uuid4(),
        episode_version=1,
        selected_reason="测试",
    )
    stacks.sample_sets_repo.add(stacks.scope_a, sample_set)
    preview = sample_set.preview(
        episode_refs=(ref,),
        candidate_count=1,
        excluded_count=0,
        deduplicated_count=0,
        now=NOW,
    )
    stacks.sample_sets_repo.replace_refs(
        stacks.scope_a, sample_set.sample_set_id, preview.episode_refs
    )
    saved = stacks.sample_sets_repo.save(stacks.scope_a, preview, expected_version=1)
    snapshot = saved.snapshot(now=NOW)
    return stacks.sample_sets_repo.save(stacks.scope_a, snapshot, expected_version=2)
