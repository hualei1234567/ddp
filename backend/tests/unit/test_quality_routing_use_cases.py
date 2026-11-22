"""质检结论驱动路由（V31-032）：结论落库后推进标注提交状态。

与 ``test_quality_routing_rules.py`` 的分工：那个文件只验证"结论 → 动作"
的判定表；本文件验证**编排**——读结论、找目标提交、调用既有提交用例、
把"状态机拒绝"翻译成幂等成功。

断言的是数据库事实（提交状态），不 mock 提交用例：路由的价值就在于它真的
调用了既有状态机，替身会让"绕过状态机"的缺陷漏过去。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.annotation.submissions import AnnotationSubmissionUseCases
from app.application.quality.commands import NewQualityRun
from app.application.quality.registry import default_registry
from app.application.quality.routing import QualityRoutingUseCases
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.annotation.enums import AnnotationSubmissionStatus
from app.domain.annotation.submission import (
    AnnotationRecord as AnnotationRecordDomain,
)
from app.domain.annotation.submission import (
    LabelValue as LabelValueDomain,
)
from app.domain.annotation.submission import (
    compute_records_digest,
)
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import (
    QualityCategory,
    QualityRunLifecycle,
    QualityRunStatus,
    QualitySubjectType,
)
from app.domain.quality.errors import ResourceNotFoundError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_results import AnnotationRecord, AnnotationSubmission
from app.infrastructure.db.models.annotation_work import AnnotationBatch, AnnotationTask
from app.infrastructure.db.models.quality import QualityRunModel
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
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationArbitrationRepository,
    SqlAnnotationRecordRepository,
    SqlAnnotationReviewRepository,
    SqlAnnotationSubmissionRepository,
)
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import (
    SqlAnnotationSchemaRepository,
)
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.dataset_policy_repositories import (
    SqlDatasetReportRepository,
)
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.api.dataset_support import SUBMISSION_DIGEST, seed_dataset_episodes

NOW = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _quality_use_cases(session: Session) -> QualityRunUseCases:
    return QualityRunUseCases(
        runs=SqlQualityRunRepository(session),
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        episodes=SqlEpisodeRepository(session),
        episode_assets=SqlEpisodeRawAssetRepository(session),
        annotation_records=SqlAnnotationRecordRepository(session),
        schemas=SqlAnnotationSchemaRepository(session),
        tasks=SqlAnnotationTaskRepository(session),
        registry=default_registry(SqlDatasetReportRepository(session)),
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        uow=session,
    )


def _routing_use_cases(session: Session) -> QualityRoutingUseCases:
    records = SqlAnnotationRecordRepository(session)
    submissions = AnnotationSubmissionUseCases(
        SqlAnnotationSubmissionRepository(session),
        records,
        SqlAnnotationAssignmentRepository(session),
        SqlAnnotationBatchRepository(session),
        SqlAnnotationTaskRepository(session),
        SqlAnnotationReviewRepository(session),
        SqlAnnotationArbitrationRepository(session),
        SqlMemberDirectory(session),
        SqlAnnotationSampleSetRepository(session),
        SqlAnnotationSchemaRepository(session),
        SqlEpisodeDirectory(session),
        SqlAnnotationAuditRepository(session),
        SqlAnnotationOutboxRepository(session),
        SqlAnnotationIdempotencyRepository(session),
        session,
    )
    return QualityRoutingUseCases(
        runs=SqlQualityRunRepository(session),
        records=records,
        submissions=submissions,
        members=SqlMemberDirectory(session),
    )


def _seed_episode_with_submitted(
    factory: sessionmaker[Session],
) -> tuple[ProjectScope, UUID, UUID]:
    """一个 Episode + 一条 ``SUBMITTED`` 提交 + 一条指向该提交的记录。

    种子数据默认把提交推进到 ``APPROVED``（Dataset 门禁需要），路由测试
    需要"待路由"的 ``SUBMITTED``，因此这里把状态回退到 ``SUBMITTED``。
    """
    ids = seed_identity(factory)
    episodes = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=1,
        record_labels=[
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )
    episode_id = episodes[0]
    with factory() as session:
        # 种子的提交已经走到 APPROVED；数据库触发器只允许逐级迁移，
        # 直接回退到 SUBMITTED 会被拒。这里重新建一条 DRAFT 提交并推进到
        # SUBMITTED，再把记录挂到它下面——比"倒着改状态"更贴近真实链路。
        template = session.scalar(select(AnnotationSubmission))
        assert template is not None
        submission_id = uuid4()
        session.add(
            AnnotationSubmission(
                id=submission_id,
                project_id=template.project_id,
                task_id=template.task_id,
                batch_id=template.batch_id,
                assignment_id=template.assignment_id,
                annotator_id=template.annotator_id,
                submission_version=template.submission_version + 1,
                status="DRAFT",
                schema_id=template.schema_id,
                schema_version=template.schema_version,
                schema_sha256=template.schema_sha256,
                item_count=1,
                # 插入时必须是合法摘要（CHECK 校验格式），随后用真实记录重算。
                records_digest=SUBMISSION_DIGEST,
                data_source=template.data_source,
                record_refs=[],
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=template.created_by,
                updated_by=template.updated_by,
            )
        )
        session.flush()
        # 记录一旦离开 DRAFT 就不可改（触发器 `annotation_record_immutable`），
        # 因此给新提交建一条自己的记录，而不是把旧记录挪过来。
        record_id = uuid4()
        record = AnnotationRecordDomain(
            annotation_id=record_id,
            annotation_lineage_id=uuid4(),
            annotation_version=1,
            project_id=template.project_id,
            submission_id=submission_id,
            episode_id=episode_id,
            episode_version=1,
            task_id=template.task_id,
            schema_id=template.schema_id,
            schema_version=template.schema_version,
            labels=(
                LabelValueDomain(
                    label_key="action",
                    value="PICK",
                    source="HUMAN",
                    label_status="CONFIRMED",
                ),
            ),
        )
        session.add(
            AnnotationRecord(
                id=record_id,
                project_id=template.project_id,
                submission_id=submission_id,
                task_id=template.task_id,
                episode_id=episode_id,
                episode_version=1,
                annotation_lineage_id=record.annotation_lineage_id,
                annotation_version=1,
                schema_id=template.schema_id,
                schema_version=template.schema_version,
                status="DRAFT",
                time_range=None,
                labels=[
                    {
                        "label_key": "action",
                        "value": "PICK",
                        "source": "HUMAN",
                        "label_status": "CONFIRMED",
                    }
                ],
                evidence_refs=[],
                record_sha256=record.record_sha256,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=template.created_by,
                updated_by=template.updated_by,
            )
        )
        session.flush()
        # 返工要求以"任务与批次都处于 REVIEWING"为前提
        # （`_return_task_and_batch_for_rework` 的状态机校验）。种子停在
        # DRAFT/PLANNED，这里按真实链路逐级推进；任务到 SUBMITTED 要求批次
        # 已提交，因此两者交替推进。
        for task_status, batch_status in (
            ("PENDING_REVIEW", None),
            ("APPROVED", None),
            ("READY", None),
            ("ASSIGNED", None),
            ("IN_PROGRESS", None),
            (None, "READY"),
            (None, "ASSIGNED"),
            (None, "IN_PROGRESS"),
            # 任务转 SUBMITTED 要求批次已在 SUBMITTED/REVIEWING/ACCEPTED，
            # 触发器读库内状态，因此批次必须先走。
            (None, "SUBMITTED"),
            ("SUBMITTED", None),
            (None, "REVIEWING"),
            ("REVIEWING", None),
        ):
            if task_status is not None:
                session.execute(
                    update(AnnotationTask)
                    .where(AnnotationTask.id == template.task_id)
                    .values(status=task_status)
                )
            if batch_status is not None:
                session.execute(
                    update(AnnotationBatch)
                    .where(AnnotationBatch.id == template.batch_id)
                    .values(status=batch_status)
                )
            # 触发器逐行比较 OLD/NEW；同一事务里的连续 UPDATE 看不到中间态，
            # 因此每步单独提交。
            session.commit()
        # 记录初始必须是 DRAFT（触发器），再逐级推进到 SUBMITTED：
        # 路由按记录状态筛选候选，DRAFT 记录不会被路由。
        session.execute(
            update(AnnotationRecord)
            .where(AnnotationRecord.id == record_id)
            .values(status="SUBMITTED", version=2)
        )
        # 提交摘要必须与**读回**的记录一致（领域在读取时重算并校验）。
        # JSON 列往返可能改变表示形式，所以从库内读回的领域对象算摘要，
        # 而不是用插入前的内存对象。
        stored = SqlAnnotationRecordRepository(session).list_for_submission(
            ProjectScope(project_id=template.project_id, actor_id=template.created_by),
            submission_id,
        )
        records_digest = compute_records_digest(tuple(stored))
        session.execute(
            update(AnnotationSubmission)
            .where(AnnotationSubmission.id == submission_id)
            .values(
                records_digest=records_digest,
                record_refs=[str(record_id)],
                status=AnnotationSubmissionStatus.SUBMITTED.value,
                submitted_at=NOW,
                version=2,
            )
        )
        session.commit()
    return (
        ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a),
        episode_id,
        submission_id,
    )


def _succeeded_run(
    factory: sessionmaker[Session],
    *,
    scope: ProjectScope,
    subject_id: UUID,
    status: QualityRunStatus,
    subject_type: QualitySubjectType = QualitySubjectType.EPISODE,
) -> UUID:
    """造一条 ``SUCCEEDED`` 运行并直接写结论，返回 run_id。

    直接写库而不是走 Worker：本文件验证"结论产出之后"的路由，执行链路本身
    由 ``test_quality_run_worker.py`` 覆盖。
    """
    with factory() as session:
        use_cases = _quality_use_cases(session)
        run = use_cases.enqueue(
            scope,
            NewQualityRun(subject_type=subject_type, subject_id=subject_id),
        )
        session.commit()
        use_cases.start(scope, run.run_id, now=NOW)
        session.commit()
        session.execute(
            update(QualityRunModel)
            .where(QualityRunModel.id == run.run_id)
            .values(
                lifecycle=QualityRunLifecycle.SUCCEEDED.value,
                status=status.value,
                score=0.0,
                finding_count=0,
                error_count=0,
                warn_count=0,
                category_summary={
                    item.value: {
                        "status": "NOT_APPLICABLE",
                        "finding_count": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                        "not_applicable_count": 0,
                        "error_count": 0,
                        "score": None,
                    }
                    for item in QualityCategory
                },
                finished_at=NOW,
            )
        )
        session.commit()
        return run.run_id


def test_failed_conclusion_requires_rework_on_submitted_submission(
    factory: sessionmaker[Session],
) -> None:
    """质检 ``FAILED`` → 该 Episode 下 ``SUBMITTED`` 提交转 ``REWORK_REQUIRED``。"""
    scope, episode_id, submission_id = _seed_episode_with_submitted(factory)
    run_id = _succeeded_run(
        factory, scope=scope, subject_id=episode_id, status=QualityRunStatus.FAILED
    )

    with factory() as session:
        result = _routing_use_cases(session).route(scope, run_id, now=NOW)
        session.commit()

    assert result.status == "ROUTED"
    assert result.actions[-1] == "require-rework"
    assert submission_id in result.affected_submission_ids
    with factory() as session:
        row = session.get(AnnotationSubmission, submission_id)
        assert row is not None
        assert row.status == AnnotationSubmissionStatus.REWORK_REQUIRED.value


def test_passed_conclusion_starts_review(factory: sessionmaker[Session]) -> None:
    scope, episode_id, submission_id = _seed_episode_with_submitted(factory)
    run_id = _succeeded_run(
        factory, scope=scope, subject_id=episode_id, status=QualityRunStatus.PASSED
    )

    with factory() as session:
        result = _routing_use_cases(session).route(scope, run_id, now=NOW)
        session.commit()

    assert result.status == "ROUTED"
    assert result.actions == ("system-review-start",)
    with factory() as session:
        row = session.get(AnnotationSubmission, submission_id)
        assert row is not None
        assert row.status == AnnotationSubmissionStatus.IN_REVIEW.value


def test_reroute_is_idempotent(factory: sessionmaker[Session]) -> None:
    """同一结论被重复消费：第二次不报错，提交停在目标状态。

    第二次路由时提交已不在 ``SUBMITTED``，因此按"没有可推进的提交"跳过——
    这正是幂等的形态：不重复推进、不报错、不产生第二条审计。
    """
    scope, episode_id, submission_id = _seed_episode_with_submitted(factory)
    run_id = _succeeded_run(
        factory, scope=scope, subject_id=episode_id, status=QualityRunStatus.FAILED
    )

    with factory() as session:
        _routing_use_cases(session).route(scope, run_id, now=NOW)
        session.commit()
    with factory() as session:
        again = _routing_use_cases(session).route(scope, run_id, now=NOW)
        session.commit()

    assert again.status == "SKIPPED"
    with factory() as session:
        row = session.get(AnnotationSubmission, submission_id)
        assert row is not None
        assert row.status == AnnotationSubmissionStatus.REWORK_REQUIRED.value


def test_unfinished_run_is_skipped(factory: sessionmaker[Session]) -> None:
    """未到 ``SUCCEEDED`` 的运行没有可信结论，路由必须跳过。"""
    scope, episode_id, _ = _seed_episode_with_submitted(factory)
    with factory() as session:
        run = _quality_use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
        )
        session.commit()
        run_id = run.run_id

    with factory() as session:
        result = _routing_use_cases(session).route(scope, run_id, now=NOW)

    assert result.status == "SKIPPED"
    assert result.actions == ()


def test_incomplete_conclusion_is_skipped(factory: sessionmaker[Session]) -> None:
    """``INCOMPLETE`` 既非通过也非失败，推进任何状态都是伪造。"""
    scope, episode_id, _ = _seed_episode_with_submitted(factory)
    run_id = _succeeded_run(
        factory, scope=scope, subject_id=episode_id, status=QualityRunStatus.INCOMPLETE
    )

    with factory() as session:
        result = _routing_use_cases(session).route(scope, run_id, now=NOW)

    assert result.status == "SKIPPED"
    assert result.actions == ()


def test_datasource_import_run_is_skipped(factory: sessionmaker[Session]) -> None:
    """导入质检的结论不驱动标注提交。"""
    scope, episode_id, _ = _seed_episode_with_submitted(factory)
    run_id = _succeeded_run(
        factory,
        scope=scope,
        subject_id=episode_id,
        status=QualityRunStatus.FAILED,
        subject_type=QualitySubjectType.DATASOURCE_IMPORT,
    )

    with factory() as session:
        result = _routing_use_cases(session).route(scope, run_id, now=NOW)

    assert result.status == "SKIPPED"
    assert result.actions == ()


def test_unknown_run_raises_not_found(factory: sessionmaker[Session]) -> None:
    scope, _, _ = _seed_episode_with_submitted(factory)

    with factory() as session:
        with pytest.raises(ResourceNotFoundError):
            _routing_use_cases(session).route(scope, uuid4(), now=NOW)


def test_cross_project_run_is_not_visible(factory: sessionmaker[Session]) -> None:
    scope, episode_id, _ = _seed_episode_with_submitted(factory)
    run_id = _succeeded_run(
        factory, scope=scope, subject_id=episode_id, status=QualityRunStatus.FAILED
    )
    foreign = ProjectScope(project_id=uuid4(), actor_id=scope.actor_id)

    with factory() as session:
        with pytest.raises(ResourceNotFoundError):
            _routing_use_cases(session).route(foreign, run_id, now=NOW)
