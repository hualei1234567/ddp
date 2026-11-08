"""标注仓储与 SQLite 触发器测试（真实约束）。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.annotation import (
    AnnotationAssignment,
    AnnotationBatch,
    AnnotationSampleSet,
    AnnotationSchema,
    AnnotationTask,
    LabelDefinition,
    SampleEpisodeRef,
)
from app.domain.annotation.errors import DuplicateResourceError
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSampleEpisode,
)
from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSampleSet as AnnotationSampleSetModel,
)
from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSchema as AnnotationSchemaModel,
)
from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment as AnnotationAssignmentModel,
)
from app.infrastructure.db.models.annotation_work import (
    AnnotationBatch as AnnotationBatchModel,
)
from app.infrastructure.db.models.annotation_work import (
    AnnotationTask as AnnotationTaskModel,
)
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.annotation_assignments import (
    SqlAnnotationAssignmentRepository,
)
from app.infrastructure.repositories.annotation_batches import SqlAnnotationBatchRepository
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import SqlAnnotationSchemaRepository
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


@dataclass(frozen=True)
class _Ids:
    project_a: UUID
    project_b: UUID
    user_a: UUID
    user_b: UUID
    user_c: UUID
    episode_a: UUID
    episode_b: UUID


@pytest.fixture()
def annotation_db() -> Iterator[tuple[sessionmaker[Session], _Ids]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = _Ids(
        project_a=uuid4(),
        project_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
        user_c=uuid4(),
        episode_a=uuid4(),
        episode_b=uuid4(),
    )
    with factory() as session:
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
                    project_id=ids.project_b,
                    user_id=ids.user_b,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=ids.project_b,
                    user_id=ids.user_c,
                    role=ProjectRole.EDITOR.value,
                ),
            ]
        )
        for project_id, user_id, episode_id, suffix in (
            (ids.project_a, ids.user_a, ids.episode_a, "a"),
            (ids.project_b, ids.user_c, ids.episode_b, "b"),
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
                    start_time_ns=1_000,
                    end_time_ns=2_000,
                    duration_ns=1_000,
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
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _scope(project_id: UUID, actor_id: UUID) -> ProjectScope:
    return ProjectScope(project_id, actor_id=actor_id)


def _label() -> LabelDefinition:
    return LabelDefinition(
        label_key="action",
        label_type="ENUM",
        required=True,
        level="EPISODE",
        value_spec={"enum_values": ["PICK"]},
    )


def _add_schema(session: Session, scope: ProjectScope) -> AnnotationSchema:
    repository = SqlAnnotationSchemaRepository(session)
    schema = AnnotationSchema(
        schema_id=uuid4(),
        project_id=scope.project_id,
        code=f"schema-{uuid4().hex[:8]}",
        name="标签 Schema",
        schema_lineage_id=uuid4(),
        annotation_levels=("EPISODE",),
        labels=(_label(),),
    )
    stored = repository.add(scope, schema)
    pending = stored.apply_action("submit-review", now=NOW)
    pending_saved = repository.save(scope, pending, expected_version=stored.version)
    published = pending_saved.apply_action("publish", now=NOW)
    return repository.save(scope, published, expected_version=pending_saved.version)


def _add_snapshot(session: Session, scope: ProjectScope, episode_id: UUID) -> AnnotationSampleSet:
    repository = SqlAnnotationSampleSetRepository(session)
    episode = session.get(EpisodeModel, episode_id)
    if episode is None:
        raise AssertionError("测试 Episode 未找到")
    sample_set = AnnotationSampleSet(
        sample_set_id=uuid4(),
        project_id=scope.project_id,
        code=f"sample-{uuid4().hex[:8]}",
        filters={},
    )
    repository.add(scope, sample_set)
    ref = SampleEpisodeRef(
        project_id=scope.project_id,
        sample_set_id=sample_set.sample_set_id,
        episode_id=episode_id,
        episode_lineage_id=episode.episode_lineage_id,
        episode_version=episode.episode_version,
        selected_reason="满足门禁",
    )
    preview = sample_set.preview(
        episode_refs=(ref,),
        candidate_count=1,
        excluded_count=0,
        deduplicated_count=0,
        now=NOW,
    )
    repository.replace_refs(scope, sample_set.sample_set_id, preview.episode_refs)
    saved = repository.save(scope, preview, expected_version=1)
    snapshot = saved.snapshot(now=NOW)
    return repository.save(scope, snapshot, expected_version=2)


def _add_task(
    session: Session,
    scope: ProjectScope,
    schema: AnnotationSchema,
    sample_set: AnnotationSampleSet,
) -> AnnotationTask:
    repository = SqlAnnotationTaskRepository(session)
    task = AnnotationTask(
        task_id=uuid4(),
        project_id=scope.project_id,
        code=f"task-{uuid4().hex[:8]}",
        name="任务",
        sample_set_id=sample_set.sample_set_id,
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        schema_sha256=schema.schema_sha256 or SHA,
        target_item_count=1,
        target_episode_count=1,
        created_at=NOW,
        updated_at=NOW,
    )
    return repository.add(scope, task)


def _add_batch(
    session: Session,
    scope: ProjectScope,
    task: AnnotationTask,
    episode_id: UUID,
) -> AnnotationBatch:
    repository = SqlAnnotationBatchRepository(session)
    batch = AnnotationBatch(
        batch_id=uuid4(),
        project_id=scope.project_id,
        task_id=task.task_id,
        code=f"batch-{uuid4().hex[:8]}",
        sequence_no=1,
        item_refs=(episode_id,),
        planned_item_count=1,
        created_at=NOW,
        updated_at=NOW,
    )
    return repository.add(scope, batch)


def _add_assignment(
    session: Session,
    scope: ProjectScope,
    task: AnnotationTask,
    batch: AnnotationBatch,
    assignee_id: UUID,
) -> AnnotationAssignment:
    repository = SqlAnnotationAssignmentRepository(session)
    assignment = AnnotationAssignment(
        assignment_id=uuid4(),
        project_id=scope.project_id,
        task_id=task.task_id,
        batch_id=batch.batch_id,
        assignee_id=assignee_id,
        planned_item_count=1,
        planned_minutes=30,
        lease_expires_at=NOW + timedelta(hours=1),
        created_at=NOW,
        updated_at=NOW,
    )
    return repository.add(scope, assignment)


def test_schema_roundtrip_isolation_and_duplicate(annotation_db) -> None:
    factory, ids = annotation_db
    scope_a = _scope(ids.project_a, ids.user_a)
    scope_b = _scope(ids.project_b, ids.user_b)
    with factory() as session:
        schema = _add_schema(session, scope_a)
        session.commit()
        repository = SqlAnnotationSchemaRepository(session)
        assert repository.get(scope_a, schema.schema_id).status.value == "PUBLISHED"
        assert repository.get(scope_b, schema.schema_id) is None

    with factory() as session:
        repository = SqlAnnotationSchemaRepository(session)
        first = AnnotationSchema(
            schema_id=uuid4(),
            project_id=ids.project_a,
            code="dup-schema",
            name="重复",
            schema_lineage_id=uuid4(),
            annotation_levels=("EPISODE",),
            labels=(_label(),),
        )
        repository.add(scope_a, first)
        session.commit()

    with factory() as session:
        repository = SqlAnnotationSchemaRepository(session)
        duplicate = AnnotationSchema(
            schema_id=uuid4(),
            project_id=ids.project_a,
            code="dup-schema",
            name="重复",
            schema_lineage_id=uuid4(),
            annotation_levels=("EPISODE",),
            labels=(_label(),),
        )
        with pytest.raises(DuplicateResourceError):
            repository.add(scope_a, duplicate)
        session.rollback()


def test_schema_published_is_immutable_and_undeletable(annotation_db) -> None:
    factory, ids = annotation_db
    scope = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        schema = _add_schema(session, scope)
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationSchemaModel)
                .where(AnnotationSchemaModel.id == schema.schema_id)
                .values(labels=[])
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationSchemaModel)
                .where(AnnotationSchemaModel.id == schema.schema_id)
                .values(status="DRAFT")
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                delete(AnnotationSchemaModel).where(AnnotationSchemaModel.id == schema.schema_id)
            )
        session.rollback()


def test_sample_set_snapshot_and_refs_are_immutable(annotation_db) -> None:
    factory, ids = annotation_db
    scope = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        snapshot = _add_snapshot(session, scope, ids.episode_a)
        session.commit()
        assert snapshot.snapshot_sha256 is not None

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(update(AnnotationSampleEpisode).values(selected_reason="篡改"))
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(delete(AnnotationSampleEpisode))
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationSampleSetModel)
                .where(AnnotationSampleSetModel.id == snapshot.sample_set_id)
                .values(filters={"scene_labels": ["tampered"]})
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                delete(AnnotationSampleSetModel).where(
                    AnnotationSampleSetModel.id == snapshot.sample_set_id
                )
            )
        session.rollback()


def test_cross_project_episode_reference_is_rejected(annotation_db) -> None:
    factory, ids = annotation_db
    scope = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        repository = SqlAnnotationSampleSetRepository(session)
        sample_set = AnnotationSampleSet(
            sample_set_id=uuid4(),
            project_id=ids.project_a,
            code="cross-project",
        )
        repository.add(scope, sample_set)
        ref = SampleEpisodeRef(
            project_id=ids.project_a,
            sample_set_id=sample_set.sample_set_id,
            episode_id=ids.episode_b,
            episode_lineage_id=uuid4(),
            episode_version=1,
            selected_reason="跨项目",
        )
        with pytest.raises(IntegrityError):
            repository.replace_refs(scope, sample_set.sample_set_id, (ref,))
        session.rollback()


def test_task_batch_assignment_triggers_and_lead_constraints(annotation_db) -> None:
    factory, ids = annotation_db
    scope = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        schema = _add_schema(session, scope)
        sample_set = _add_snapshot(session, scope, ids.episode_a)
        task = _add_task(session, scope, schema, sample_set)
        batch = _add_batch(session, scope, task, ids.episode_a)
        assignment = _add_assignment(session, scope, task, batch, ids.user_a)
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationTaskModel)
                .where(AnnotationTaskModel.id == task.task_id)
                .values(status="APPROVED")
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationTaskModel)
                .where(AnnotationTaskModel.id == task.task_id)
                .values(schema_id=uuid4())
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationBatchModel)
                .where(AnnotationBatchModel.id == batch.batch_id)
                .values(status="READY")
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                update(AnnotationAssignmentModel)
                .where(AnnotationAssignmentModel.id == assignment.assignment_id)
                .values(status="SUBMITTED")
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(delete(AnnotationTaskModel))
        session.rollback()


def test_assignment_role_separation_trigger(annotation_db) -> None:
    factory, ids = annotation_db
    scope = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        schema = _add_schema(session, scope)
        sample_set = _add_snapshot(session, scope, ids.episode_a)
        task = _add_task(session, scope, schema, sample_set)
        batch = _add_batch(session, scope, task, ids.episode_a)
        _add_assignment(session, scope, task, batch, ids.user_a)
        session.flush()
        with pytest.raises(IntegrityError):
            SqlAnnotationAssignmentRepository(session).add(
                scope,
                AnnotationAssignment(
                    assignment_id=uuid4(),
                    project_id=ids.project_a,
                    task_id=task.task_id,
                    batch_id=batch.batch_id,
                    assignee_id=ids.user_a,
                    assignment_role="REVIEWER",
                    planned_item_count=1,
                    planned_minutes=30,
                ),
            )
        session.rollback()


def test_cross_project_task_and_nonmember_assignment_are_rejected(annotation_db) -> None:
    factory, ids = annotation_db
    scope_a = _scope(ids.project_a, ids.user_a)
    scope_b = _scope(ids.project_b, ids.user_b)
    with factory() as session:
        schema_b = _add_schema(session, scope_b)
        sample_set_b = _add_snapshot(session, scope_b, ids.episode_b)
        with pytest.raises(IntegrityError):
            _add_task(session, scope_a, schema_b, sample_set_b)
        session.rollback()
