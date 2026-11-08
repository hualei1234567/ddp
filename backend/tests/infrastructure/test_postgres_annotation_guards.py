"""PostgreSQL 标注域触发器与跨项目集成测试。

未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过并标注 BLOCKED。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.annotation import (
    AnnotationSampleSet,
    AnnotationSchema,
    AnnotationTask,
    LabelDefinition,
    SampleEpisodeRef,
)
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
from app.infrastructure.db.models.annotation_work import AnnotationTask as AnnotationTaskModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import SqlAnnotationSchemaRepository
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from sqlalchemy import create_engine, delete, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_annotation_it_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            _POSTGRES_URL,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _seed(engine: Engine) -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    factory = create_session_factory(engine)
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    episode_a, episode_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code=f"p-{project_a.hex[:8]}", name="项目 A"),
                Project(id=project_b, code=f"p-{project_b.hex[:8]}", name="项目 B"),
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
                    project_id=project_b,
                    user_id=user_b,
                    role=ProjectRole.OWNER.value,
                ),
            ]
        )
        for project_id, user_id, episode_id, suffix in (
            (project_a, user_a, episode_a, "a"),
            (project_b, user_b, episode_b, "b"),
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
    return project_a, project_b, user_a, user_b, episode_a, episode_b


def _label() -> LabelDefinition:
    return LabelDefinition(
        label_key="action",
        label_type="ENUM",
        required=True,
        level="EPISODE",
        value_spec={"enum_values": ["PICK"]},
    )


def _add_schema(session, scope: ProjectScope) -> AnnotationSchema:
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


def _add_snapshot(session, scope: ProjectScope, episode: EpisodeModel) -> AnnotationSampleSet:
    repository = SqlAnnotationSampleSetRepository(session)
    sample_set = AnnotationSampleSet(
        sample_set_id=uuid4(),
        project_id=scope.project_id,
        code=f"sample-{uuid4().hex[:8]}",
    )
    repository.add(scope, sample_set)
    ref = SampleEpisodeRef(
        project_id=scope.project_id,
        sample_set_id=sample_set.sample_set_id,
        episode_id=episode.id,
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


def test_postgres_annotation_cross_project_and_triggers(postgres_engine: Engine) -> None:
    factory = create_session_factory(postgres_engine)
    project_a, project_b, user_a, _user_b, episode_a, episode_b = _seed(postgres_engine)
    scope_a = ProjectScope(project_a, actor_id=user_a)
    scope_b = ProjectScope(project_b, actor_id=_user_b)

    with factory() as session:
        sample_set = AnnotationSampleSet(
            sample_set_id=uuid4(),
            project_id=project_a,
            code="cross",
        )
        cross_ref = SampleEpisodeRef(
            project_id=project_a,
            sample_set_id=sample_set.sample_set_id,
            episode_id=episode_b,
            episode_lineage_id=uuid4(),
            episode_version=1,
            selected_reason="跨项目",
        )
        repository = SqlAnnotationSampleSetRepository(session)
        repository.add(scope_a, sample_set)
        with pytest.raises(IntegrityError):
            repository.replace_refs(scope_a, sample_set.sample_set_id, (cross_ref,))
        session.rollback()

    with factory() as session:
        episode_row = session.get(EpisodeModel, episode_a)
        schema = _add_schema(session, scope_a)
        snapshot = _add_snapshot(session, scope_a, episode_row)
        task_repository = SqlAnnotationTaskRepository(session)
        task = AnnotationTask(
            task_id=uuid4(),
            project_id=project_a,
            code=f"task-{uuid4().hex[:8]}",
            name="任务",
            sample_set_id=snapshot.sample_set_id,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            schema_sha256=schema.schema_sha256 or SHA,
            target_item_count=1,
            target_episode_count=1,
        )
        task_repository.add(scope_a, task)
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError, match="annotation_schema_immutable"):
            session.execute(
                update(AnnotationSchemaModel)
                .where(AnnotationSchemaModel.id == schema.schema_id)
                .values(labels=[])
            )
        session.rollback()
        with pytest.raises(IntegrityError, match="annotation_sample_set_immutable"):
            session.execute(
                update(AnnotationSampleSetModel)
                .where(AnnotationSampleSetModel.id == snapshot.sample_set_id)
                .values(deduplicated_count=2)
            )
        session.rollback()
        with pytest.raises(IntegrityError, match="annotation_task_state_transition_invalid"):
            session.execute(
                update(AnnotationTaskModel)
                .where(AnnotationTaskModel.id == task.task_id)
                .values(status="APPROVED")
            )
        session.rollback()
        with pytest.raises(IntegrityError, match="annotation_sample_episode_immutable"):
            session.execute(update(AnnotationSampleEpisode).values(selected_reason="篡改"))
        session.rollback()
        with pytest.raises(IntegrityError, match="delete_forbidden"):
            session.execute(delete(AnnotationSchemaModel))
        session.rollback()

    with factory() as session:
        assert SqlAnnotationSchemaRepository(session).get(scope_b, schema.schema_id) is None
