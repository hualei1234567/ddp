"""PostgreSQL 标注域触发器与跨项目集成测试。

未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过并标注 BLOCKED。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.annotation.commands import (
    NewAnnotationArbitration,
    NewAnnotationReview,
    NewGoldSet,
    NewQualitySample,
)
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
from app.infrastructure.db.models.annotation_quality import (
    GoldSet as GoldSetModel,
)
from app.infrastructure.db.models.annotation_quality import (
    IAAReport as IAAReportModel,
)
from app.infrastructure.db.models.annotation_quality import (
    QualitySample as QualitySampleModel,
)
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
from app.infrastructure.db.models.annotation_work import AnnotationTask as AnnotationTaskModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import SqlAnnotationSchemaRepository
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from tests.annotation_results_support import (
    build_stacks,
    make_submission,
    prepare_ready_task,
    seed_identity,
)

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


_EXPECTED_ANNOTATION_RESULT_TRIGGERS = frozenset(
    {
        "trg_annotation_submissions_initial_status",
        "trg_annotation_submissions_transition",
        "trg_annotation_submissions_immutable",
        "trg_annotation_submissions_snapshot",
        "trg_annotation_submissions_no_delete",
        "trg_annotation_records_initial_status",
        "trg_annotation_records_transition",
        "trg_annotation_records_immutable",
        "trg_annotation_records_no_delete",
        "trg_annotation_reviews_initial_status",
        "trg_annotation_reviews_transition",
        "trg_annotation_reviews_immutable",
        "trg_annotation_reviews_separation",
        "trg_annotation_reviews_no_delete",
        "trg_annotation_arbitrations_initial_status",
        "trg_annotation_arbitrations_transition",
        "trg_annotation_arbitrations_immutable",
        "trg_annotation_arbitrations_decision",
        "trg_annotation_arbitrations_no_delete",
        "trg_annotation_gold_sets_initial_status",
        "trg_annotation_gold_sets_transition",
        "trg_annotation_gold_sets_immutable",
        "trg_annotation_gold_sets_no_delete",
        "trg_annotation_gold_set_exposures_append_only",
        "trg_annotation_gold_set_exposures_no_delete",
        "trg_annotation_gold_set_evaluations_append_only",
        "trg_annotation_gold_set_evaluations_no_delete",
        "trg_annotation_iaa_reports_initial_status",
        "trg_annotation_iaa_reports_transition",
        "trg_annotation_iaa_reports_immutable",
        "trg_annotation_iaa_reports_no_delete",
        "trg_annotation_quality_samples_initial_status",
        "trg_annotation_quality_samples_transition",
        "trg_annotation_quality_samples_immutable",
        "trg_annotation_quality_samples_no_delete",
    }
)


def test_postgres_annotation_result_quality_trigger_inventory(
    postgres_engine: Engine,
) -> None:
    """结果/质控批次触发器必须随 0014/0015 与 create_all 安装。"""
    with postgres_engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
    missing = _EXPECTED_ANNOTATION_RESULT_TRIGGERS - names
    assert not missing, f"缺少触发器：{sorted(missing)}"


def test_postgres_published_schema_json_immutable(postgres_engine: Engine) -> None:
    """PUBLISHED Schema 的 JSON 字段不可直写（PostgreSQL JSONB 比较回归）。"""
    factory = create_session_factory(postgres_engine)
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"p-{project_id.hex[:8]}", name="P"),
                User(id=user_id, display_name="U"),
            ]
        )
        session.flush()
        schema = _add_schema(session, ProjectScope(project_id, actor_id=user_id))
        session.commit()

        with pytest.raises(IntegrityError, match="annotation_schema_immutable"):
            session.execute(
                update(AnnotationSchemaModel)
                .where(AnnotationSchemaModel.id == schema.schema_id)
                .values(annotation_levels=["EPISODE", "FRAME"])
            )
            session.flush()
        session.rollback()


def _assert_json_update_blocked(
    engine: Engine,
    model: type,
    row_id: object,
    values: dict[str, object],
    message: str,
) -> None:
    """在真实 PostgreSQL 上更新 JSON 列，必须被不可变触发器拒绝。"""
    factory = create_session_factory(engine)
    with factory() as probe:
        with pytest.raises(IntegrityError, match=message):
            probe.execute(update(model).where(model.id == row_id).values(**values))
            probe.flush()
        probe.rollback()


def test_postgres_annotation_result_quality_json_immutability(
    postgres_engine: Engine,
) -> None:
    """7 个不可变守卫函数在 JSON/JSONB 列上的 UPDATE 回归（D1）。"""
    factory = create_session_factory(postgres_engine)
    with factory() as session:
        ids = seed_identity(session)
        stacks = build_stacks(session, ids)
        prepare_ready_task(stacks)
        submission = make_submission(stacks, stacks.scope_a, stacks.assignment_a)

        review = stacks.reviews.create(
            stacks.scope_c,
            NewAnnotationReview(
                submission_id=submission.submission_id,
                reviewer_id=ids.user_c,
            ),
            path="/projects/p/annotation-submissions/x/reviews",
        )
        review = stacks.reviews.apply_action(
            stacks.scope_c,
            review.review_id,
            "claim",
            expected_version=review.version,
            path="/projects/p/annotation-reviews/x/actions/claim",
        )
        review = stacks.reviews.apply_action(
            stacks.scope_c,
            review.review_id,
            "escalate",
            expected_version=review.version,
            reason="标注人与复核人意见不一致",
            path="/projects/p/annotation-reviews/x/actions/escalate",
        )
        assert review.status.value == "ESCALATED"

        arbitration = stacks.arbitrations.create(
            stacks.scope_b,
            NewAnnotationArbitration(
                submission_id=submission.submission_id,
                arbiter_ids=(ids.user_b,),
                quorum=1,
                trigger="REVIEWER_DISAGREEMENT",
                review_id=review.review_id,
            ),
            path="/projects/p/annotation-arbitrations",
        )
        arbitration = stacks.arbitrations.apply_action(
            stacks.scope_b,
            arbitration.arbitration_id,
            "cancel",
            expected_version=arbitration.version,
            reason="测试取消",
            path="/projects/p/annotation-arbitrations/x/actions/cancel",
        )
        assert arbitration.status.value == "CANCELLED"

        gold_set = stacks.gold_sets.create(
            stacks.scope_a,
            NewGoldSet(
                code=f"gold-{uuid4().hex[:8]}",
                schema_id=stacks.schema_id,
                items=(
                    {
                        "gold_item_id": str(uuid4()),
                        "episode_id": str(ids.episode_a),
                        "expected_labels": [{"label_key": "action", "value": "PICK"}],
                    },
                ),
            ),
            path="/projects/p/gold-sets",
        )
        gold_set = stacks.gold_sets.apply_action(
            stacks.scope_a,
            gold_set.gold_set_id,
            "revoke",
            expected_version=gold_set.version,
            reason="测试撤销",
            path="/projects/p/gold-sets/x/actions/revoke",
        )
        assert gold_set.status.value == "REVOKED"

        sample = stacks.quality_samples.create_one(
            stacks.scope_b,
            NewQualitySample(
                task_id=stacks.task_id,
                submission_id=submission.submission_id,
                annotator_id=ids.user_a,
                sampling_policy_version="quality/1",
                sample_type="RANDOM",
                sample_reason="例行抽检",
            ),
            path="/projects/p/quality-samples",
        )
        sample = stacks.quality_samples.apply_action(
            stacks.scope_b,
            sample.quality_sample_id,
            "cancel",
            expected_version=sample.version,
            reason="测试取消",
            path="/projects/p/quality-samples/x/actions/cancel",
        )
        assert sample.status.value == "CANCELLED"

        iaa = stacks.iaa.compute(
            stacks.scope_a,
            stacks.task_id,
            metric_definitions_version="ddp.stage3.iaa/1",
            path="/projects/p/iaa-reports",
        )
        assert iaa.status == "INSUFFICIENT_DATA"

        submission_id = submission.submission_id
        record_rows = stacks.records_repo.list_for_submission(stacks.scope_a, submission_id)
        record_id = record_rows[0].annotation_id
        review_id = review.review_id
        arbitration_id = arbitration.arbitration_id
        gold_set_id = gold_set.gold_set_id
        sample_id = sample.quality_sample_id
        iaa_id = iaa.report_id

    _assert_json_update_blocked(
        postgres_engine,
        AnnotationSubmissionModel,
        submission_id,
        {"record_refs": ["tampered"]},
        "annotation_submission_immutable",
    )
    _assert_json_update_blocked(
        postgres_engine,
        AnnotationRecordModel,
        record_id,
        {"labels": [{"label_key": "action", "value": "PLACE"}]},
        "annotation_record_immutable",
    )
    _assert_json_update_blocked(
        postgres_engine,
        AnnotationReviewModel,
        review_id,
        {"issues": [{"code": "tampered"}]},
        "annotation_review_immutable",
    )
    _assert_json_update_blocked(
        postgres_engine,
        AnnotationArbitrationModel,
        arbitration_id,
        {"votes": [{"arbiter_id": "tampered", "decision": "APPROVE"}]},
        "annotation_arbitration_immutable",
    )
    _assert_json_update_blocked(
        postgres_engine,
        GoldSetModel,
        gold_set_id,
        {"items": []},
        "annotation_gold_set_immutable",
    )
    _assert_json_update_blocked(
        postgres_engine,
        QualitySampleModel,
        sample_id,
        {"item_refs": ["tampered"]},
        "annotation_quality_sample_immutable",
    )
    _assert_json_update_blocked(
        postgres_engine,
        IAAReportModel,
        iaa_id,
        {"metrics": [{"metric": "COHEN_KAPPA", "value": 1.0}]},
        "annotation_iaa_report_immutable",
    )
