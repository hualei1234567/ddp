"""标注用例集成测试（SQLite 真实约束与事务）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.application.annotation.batches import AnnotationBatchUseCases
from app.application.annotation.commands import (
    NewAnnotationAssignment,
    NewAnnotationBatch,
    NewAnnotationSampleSet,
    NewAnnotationSchema,
    NewAnnotationTask,
)
from app.application.annotation.sample_sets import AnnotationSampleSetUseCases
from app.application.annotation.schemas import AnnotationSchemaUseCases
from app.application.annotation.tasks import AnnotationTaskUseCases
from app.domain.annotation import ForbiddenOperationError, WorkloadStatus
from app.domain.annotation.workload import CapacityPolicy
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
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
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import SqlAnnotationSchemaRepository
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

# 用例时间基线必须跟随"当前时间"：领用租约与到期校验按真实时钟判断，
# 写死过去日期会让整条链路被判为过期（2026-11-14 暴露）。
NOW = datetime.now(UTC).replace(microsecond=0)
SHA = "sha256:" + "a" * 64


class _SessionUow:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()


class _Stacks:
    def __init__(
        self,
        session: Session,
        project_id: UUID,
        other_project_id: UUID,
        user_a: UUID,
        user_b: UUID,
    ) -> None:
        self.schemas_repo = SqlAnnotationSchemaRepository(session)
        self.sample_sets_repo = SqlAnnotationSampleSetRepository(session)
        self.tasks_repo = SqlAnnotationTaskRepository(session)
        self.batches_repo = SqlAnnotationBatchRepository(session)
        self.assignments_repo = SqlAnnotationAssignmentRepository(session)
        self.audit_repo = SqlAnnotationAuditRepository(session)
        self.outbox_repo = SqlAnnotationOutboxRepository(session)
        self.idempotency_repo = SqlAnnotationIdempotencyRepository(session)
        self.uow = _SessionUow(session)
        self.scope_a = ProjectScope(project_id, actor_id=user_a)
        self.scope_b = ProjectScope(project_id, actor_id=user_b)
        self.scope_other = ProjectScope(other_project_id, actor_id=user_b)
        self.schemas = AnnotationSchemaUseCases(
            self.schemas_repo,
            self.audit_repo,
            self.outbox_repo,
            self.idempotency_repo,
            self.uow,
        )
        self.sample_sets = AnnotationSampleSetUseCases(
            self.sample_sets_repo,
            self.tasks_repo,
            SqlEpisodeDirectory(session),
            self.audit_repo,
            self.outbox_repo,
            self.idempotency_repo,
            self.uow,
        )
        self.tasks = AnnotationTaskUseCases(
            self.tasks_repo,
            self.sample_sets_repo,
            self.schemas_repo,
            self.batches_repo,
            self.assignments_repo,
            self.audit_repo,
            self.outbox_repo,
            self.idempotency_repo,
            self.uow,
        )
        self.batches = AnnotationBatchUseCases(
            self.tasks_repo,
            self.batches_repo,
            self.assignments_repo,
            self.sample_sets_repo,
            self.audit_repo,
            self.outbox_repo,
            self.idempotency_repo,
            self.uow,
        )
        self.assignments = AnnotationAssignmentUseCases(
            self.tasks_repo,
            self.batches_repo,
            self.assignments_repo,
            SqlMemberDirectory(session),
            self.audit_repo,
            self.outbox_repo,
            self.idempotency_repo,
            self.uow,
        )


@pytest.fixture()
def annotation_app() -> Iterator[tuple[sessionmaker[Session], _Stacks, UUID]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    episode_id = uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=user_a, display_name="甲"),
                User(id=user_b, display_name="乙"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(
                    project_id=project_a,
                    user_id=user_a,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    project_id=project_a,
                    user_id=user_b,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=project_b,
                    user_id=user_b,
                    role=ProjectRole.OWNER.value,
                ),
            ]
        )
        session.add(
            EpisodeModel(
                id=episode_id,
                project_id=project_a,
                episode_lineage_id=uuid4(),
                episode_version=1,
                code="EP-A-1",
                status="PENDING",
                quality_status="NOT_CHECKED",
                annotation_status="NOT_STARTED",
                scene_labels=["kitchen"],
                start_time_ns=1_000,
                end_time_ns=2_000,
                duration_ns=1_000,
                time_domain="UTC",
                time_base={},
                canonical_schema_version="ddp.canonical/1.0.0-draft",
                pipeline_id="parser",
                pipeline_version="1.0.0",
                segmentation_rule_version="seg-v1",
                calibration_snapshot=[{"sensor_id": str(uuid4())}],
                manifest_key=f"canonical/{project_a}/{episode_id}/1/manifest.json",
                manifest_sha256=SHA,
                frame_count=10,
                attempt=1,
                max_attempts=3,
                created_at=NOW,
                updated_at=NOW,
                created_by=user_a,
                updated_by=user_a,
            )
        )
        session.flush()
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
    with factory() as session:
        stacks = _Stacks(session, project_a, project_b, user_a, user_b)
        yield factory, stacks, episode_id
    engine.dispose()


def _schema_command() -> NewAnnotationSchema:
    return NewAnnotationSchema(
        code="schema-1",
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
        ),
    )


def test_full_annotation_flow_and_workload(
    annotation_app: tuple[sessionmaker[Session], _Stacks, UUID],
) -> None:
    factory, stacks, episode_id = annotation_app
    schema = stacks.schemas.create(
        stacks.scope_a,
        _schema_command(),
        path="/projects/p/annotation-schemas",
    )
    schema = stacks.schemas.apply_action(
        stacks.scope_a,
        schema.schema_id,
        "submit-review",
        expected_version=schema.version,
        path=f"/projects/p/annotation-schemas/{schema.schema_id}/actions/submit-review",
    )
    published = stacks.schemas.apply_action(
        stacks.scope_b,
        schema.schema_id,
        "publish",
        expected_version=schema.version,
        path=f"/projects/p/annotation-schemas/{schema.schema_id}/actions/publish",
    )
    assert published.status.value == "PUBLISHED"

    sample_set = stacks.sample_sets.create(
        stacks.scope_a,
        NewAnnotationSampleSet(code="sample-1", filters={"scene_labels": ["kitchen"]}),
        path="/projects/p/annotation-sample-sets",
    )
    preview = stacks.sample_sets.preview(
        stacks.scope_a,
        sample_set.sample_set_id,
        expected_version=sample_set.version,
        path=f"/projects/p/annotation-sample-sets/{sample_set.sample_set_id}/actions/preview",
    )
    assert preview.selected_count == 1
    snapshot = stacks.sample_sets.snapshot(
        stacks.scope_a,
        sample_set.sample_set_id,
        expected_version=preview.version,
        path=f"/projects/p/annotation-sample-sets/{sample_set.sample_set_id}/actions/snapshot",
    )
    assert snapshot.snapshot_sha256 is not None

    task = stacks.tasks.create(
        stacks.scope_a,
        NewAnnotationTask(
            code="task-1",
            name="任务一",
            sample_set_id=snapshot.sample_set_id,
            schema_id=published.schema_id,
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
    assert task.status.value == "APPROVED"
    assert stacks.sample_sets.get(stacks.scope_a, snapshot.sample_set_id).status.value == "CONSUMED"

    batch = stacks.batches.create(
        stacks.scope_a,
        task.task_id,
        NewAnnotationBatch(
            planned_item_count=1,
            item_refs=(episode_id,),
            code="B-1",
        ),
        path=f"/projects/p/annotation-tasks/{task.task_id}/annotation-batches",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "ready",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/ready",
    )
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "ready",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/ready",
    )
    assignment = stacks.assignments.create(
        stacks.scope_a,
        batch.batch_id,
        NewAnnotationAssignment(
            assignee_id=stacks.scope_a.actor_id,
            planned_item_count=1,
            planned_minutes=30,
            lease_expires_at=NOW + timedelta(hours=4),
        ),
        capacity=CapacityPolicy(max_open_items=2, max_planned_minutes=120),
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
    assignment = stacks.assignments.apply_action(
        stacks.scope_a,
        assignment.assignment_id,
        "claim",
        expected_version=assignment.version,
        capacity=CapacityPolicy(max_open_items=2, max_planned_minutes=120),
        path=f"/projects/p/annotation-assignments/{assignment.assignment_id}/actions/claim",
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
    assignment = stacks.assignments.apply_action(
        stacks.scope_a,
        assignment.assignment_id,
        "start",
        expected_version=assignment.version,
        path=f"/projects/p/annotation-assignments/{assignment.assignment_id}/actions/start",
    )
    assignment = stacks.assignments.apply_action(
        stacks.scope_a,
        assignment.assignment_id,
        "submit",
        expected_version=assignment.version,
        submitted_item_count=1,
        path=f"/projects/p/annotation-assignments/{assignment.assignment_id}/actions/submit",
    )
    assert assignment.status.value == "SUBMITTED"
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "submit",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/submit",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "submit",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/submit",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "system-review",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/system-review",
    )
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "system-review",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/system-review",
    )
    batch = stacks.batches.apply_action(
        stacks.scope_a,
        batch.batch_id,
        "accept",
        expected_version=batch.version,
        path=f"/projects/p/annotation-batches/{batch.batch_id}/actions/accept",
    )
    assignment = stacks.assignments.apply_action(
        stacks.scope_a,
        assignment.assignment_id,
        "complete",
        expected_version=assignment.version,
        actual_minutes=25,
        approved_item_count=1,
        path=f"/projects/p/annotation-assignments/{assignment.assignment_id}/actions/complete",
    )
    assert assignment.status.value == "COMPLETED"
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "accept",
        expected_version=task.version,
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/accept",
    )
    task = stacks.tasks.apply_action(
        stacks.scope_a,
        task.task_id,
        "complete",
        expected_version=task.version,
        payload={"waiver_reason": "测试环境允许一次例外"},
        path=f"/projects/p/annotation-tasks/{task.task_id}/actions/complete",
    )
    assert task.status.value == "COMPLETED"

    workload = stacks.assignments.workload(
        stacks.scope_a,
        assignee_id=stacks.scope_a.actor_id,
        period_start=NOW - timedelta(days=1),
        period_end=NOW + timedelta(days=1),
    )
    assert workload.assigned_item_count == 1
    assert workload.claimed_item_count == 1
    assert workload.submitted_item_count == 1
    assert workload.approved_item_count == 1
    assert workload.total_minutes == 25
    assert workload.status is WorkloadStatus.NORMAL

    with factory() as session:
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        outbox_count = session.scalar(select(func.count()).select_from(OutboxEvent))
        assert audit_count and audit_count > 0
        assert outbox_count and outbox_count > 0


def test_self_approval_and_project_isolation(
    annotation_app: tuple[sessionmaker[Session], _Stacks, UUID],
) -> None:
    _factory, stacks, _episode_id = annotation_app
    schema = stacks.schemas.create(
        stacks.scope_a,
        _schema_command(),
        path="/projects/p/annotation-schemas",
    )
    pending = stacks.schemas.apply_action(
        stacks.scope_a,
        schema.schema_id,
        "submit-review",
        expected_version=schema.version,
        path=f"/projects/p/annotation-schemas/{schema.schema_id}/actions/submit-review",
    )
    with pytest.raises(ForbiddenOperationError):
        stacks.schemas.apply_action(
            stacks.scope_a,
            pending.schema_id,
            "publish",
            expected_version=pending.version,
            path=f"/projects/p/annotation-schemas/{pending.schema_id}/actions/publish",
        )
    assert stacks.schemas.list(stacks.scope_other) == []
