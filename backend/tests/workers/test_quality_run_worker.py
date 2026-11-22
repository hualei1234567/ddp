"""质检 Worker（V31-031 切片二）：排队 → 执行 → 结论，失败按上限重投。

与 ``test_processing_quality_pipeline.py`` 的区别：那条链路是**同步**阶段
（处理流水线内部），本文件验证的是**异步**入口——入队只写 ``QUEUED`` 与
``quality.run.requested`` 事件，由 Worker 领取后推进状态机。

断言的是数据库事实与状态机终态，不 mock 规则引擎：规则源用真实实现，只有
"规则实现崩溃"这类基础设施故障用替身注入。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.quality.commands import NewQualityRun
from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import QualityRunLifecycle, QualitySubjectType
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import OutboxEvent as OutboxEventModel
from app.infrastructure.db.models.quality import QualityRunModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationRecordRepository,
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
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from app.workers.quality import QualityRunWorker
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.api.dataset_support import seed_dataset_episodes

NOW = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
REQUESTED = "quality.run.requested"


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


def _use_cases(session: Session) -> QualityRunUseCases:
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


def _episode(factory: sessionmaker[Session]) -> tuple[ProjectScope, UUID]:
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
    return ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a), episodes[0]


def _exploding_executor(session: Session, scope: ProjectScope, run_id: UUID, now: datetime):
    """模拟规则执行基础设施故障。

    领取（``QUEUED``/``RETRYING`` → ``RUNNING``）由 Worker 自己完成并先提交，
    执行器被调用时运行已是 ``RUNNING``，直接抛异常即可——这正是"算到一半崩了"
    的真实形态。
    """
    raise RuntimeError("模拟规则执行基础设施故障")


def test_enqueue_writes_queued_run_and_dispatch_event(factory: sessionmaker[Session]) -> None:
    scope, episode_id = _episode(factory)
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
            max_attempts=3,
        )
        session.commit()

        assert run.lifecycle is QualityRunLifecycle.QUEUED
        assert run.status is None
        assert run.max_attempts == 3

        events = list(
            session.scalars(
                select(OutboxEventModel).where(OutboxEventModel.event_type == REQUESTED)
            )
        )
        assert len(events) == 1
        assert events[0].dispatch is True
        assert events[0].status == "PENDING"
        assert events[0].payload["run_id"] == str(run.run_id)


def test_worker_executes_queued_run_to_conclusion(factory: sessionmaker[Session]) -> None:
    scope, episode_id = _episode(factory)
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
        )
        session.commit()

        worker = QualityRunWorker(
            factory=factory,
            max_attempts=3,
            retry_base_seconds=1.0,
            retry_max_seconds=10.0,
        )
        result = worker.process_once(now=NOW)

        assert result.status == "PUBLISHED"
        assert result.run_id == run.run_id

    with factory() as session:
        row = session.get(QualityRunModel, run.run_id)
        assert row is not None
        assert row.lifecycle == QualityRunLifecycle.SUCCEEDED.value
        assert row.status is not None
        assert row.started_at is not None
        assert row.finished_at is not None
        # 结论视图可见。
        assert SqlQualityRunRepository(session).get(scope, run.run_id) is not None


def test_worker_retries_then_dead_letters_on_repeated_failure(
    factory: sessionmaker[Session],
) -> None:
    """基础设施故障按指数退避重投；超过上限进死信，运行停在 FAILED。"""
    scope, episode_id = _episode(factory)
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
            max_attempts=2,
        )
        session.commit()

    worker = QualityRunWorker(
        factory=factory,
        max_attempts=2,
        retry_base_seconds=1.0,
        retry_max_seconds=10.0,
        execute=_exploding_executor,
    )
    first = worker.process_once(now=NOW)
    assert first.status == "RETRIED"

    second = worker.process_once(now=NOW + timedelta(seconds=30))
    assert second.status == "DEAD_LETTER"

    with factory() as session:
        row = session.get(QualityRunModel, run.run_id)
        assert row is not None
        assert row.lifecycle == QualityRunLifecycle.FAILED.value
        assert row.error_code is not None
        assert row.error_summary is not None
        event = session.scalar(
            select(OutboxEventModel).where(OutboxEventModel.event_type == REQUESTED)
        )
        assert event is not None
        assert event.status == "FAILED"


def test_worker_is_idle_without_pending_events(factory: sessionmaker[Session]) -> None:
    worker = QualityRunWorker(
        factory=factory,
        max_attempts=1,
        retry_base_seconds=1.0,
        retry_max_seconds=10.0,
    )

    result = worker.process_once(now=NOW)

    assert result.status == "IDLE"
    assert result.run_id is None


def test_worker_dead_letters_malformed_payload(factory: sessionmaker[Session]) -> None:
    """事件 payload 缺 run_id 是永久性错误：重试只会得到同样结果。"""
    ids = seed_identity(factory)
    with factory() as session:
        SqlOutboxRepository(session).enqueue(
            ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a),
            _event_without_run_id(ids.project_a),
        )
        session.commit()

    worker = QualityRunWorker(
        factory=factory,
        max_attempts=3,
        retry_base_seconds=1.0,
        retry_max_seconds=10.0,
    )

    result = worker.process_once(now=NOW)

    assert result.status == "DEAD_LETTER"
    with factory() as session:
        event = session.scalar(
            select(OutboxEventModel).where(OutboxEventModel.event_type == REQUESTED)
        )
        assert event is not None
        assert event.status == "FAILED"
        assert "run_id" in (event.last_error or "")


def _event_without_run_id(project_id: UUID):
    from app.application.audit import OutboxEvent  # noqa: PLC0415 - 仅测试内使用

    return OutboxEvent(
        event_id=uuid4(),
        project_id=project_id,
        aggregate_type="QUALITY_RUN",
        aggregate_id=uuid4(),
        event_type=REQUESTED,
        payload={"subject_type": "EPISODE"},
        dispatch=True,
        occurred_at=NOW,
    )


def test_worker_does_not_fail_run_claimed_by_another_worker(
    factory: sessionmaker[Session],
) -> None:
    """另一 Worker 已领取（RUNNING 已提交）时，本次不能把它标成 FAILED。

    这是"两个 Worker 抢同一运行"的真实形态：后到者的 ``start`` 因 CAS 失败
    抛 ``QualityRunConflictError``，但运行此刻正被前者执行——若按失败处理，
    会误伤一次正常执行。正确行为是只让事件退避重投。
    """
    scope, episode_id = _episode(factory)
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
            max_attempts=3,
        )
        session.commit()

        # 模拟另一个 Worker 已经领取并提交了 RUNNING。
        _use_cases(session).start(scope, run.run_id, now=NOW - timedelta(seconds=5))
        session.commit()

    worker = QualityRunWorker(
        factory=factory,
        max_attempts=3,
        retry_base_seconds=1.0,
        retry_max_seconds=10.0,
    )
    result = worker.process_once(now=NOW)

    assert result.status == "RETRIED"
    with factory() as session:
        row = session.get(QualityRunModel, run.run_id)
        assert row is not None
        assert row.lifecycle == QualityRunLifecycle.RUNNING.value
        assert row.error_code is None


def test_dead_letter_for_run_owned_by_another_worker_keeps_it_running(
    factory: sessionmaker[Session],
) -> None:
    """重投耗尽进死信时，也不能把另一个执行者正在跑的运行标成 FAILED。"""
    scope, episode_id = _episode(factory)
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
        )
        session.commit()
        _use_cases(session).start(scope, run.run_id, now=NOW - timedelta(seconds=5))
        session.commit()

    worker = QualityRunWorker(
        factory=factory,
        max_attempts=1,
        retry_base_seconds=1.0,
        retry_max_seconds=10.0,
    )
    result = worker.process_once(now=NOW)

    assert result.status == "DEAD_LETTER"
    with factory() as session:
        row = session.get(QualityRunModel, run.run_id)
        assert row is not None
        assert row.lifecycle == QualityRunLifecycle.RUNNING.value
        assert row.error_code is None
        event = session.scalar(
            select(OutboxEventModel).where(OutboxEventModel.event_type == REQUESTED)
        )
        assert event is not None
        assert event.status == "FAILED"


def test_redelivered_event_for_finished_run_is_published_not_dead_letter(
    factory: sessionmaker[Session],
) -> None:
    """重复投递已完成运行的事件：按已发布收口，不改运行、不进死信。

    事件本身没有错，只是来晚了；把它当死信会让运维持续处理"假故障"。
    """
    scope, episode_id = _episode(factory)
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
        )
        session.commit()

    worker = QualityRunWorker(
        factory=factory,
        max_attempts=3,
        retry_base_seconds=1.0,
        retry_max_seconds=10.0,
    )
    assert worker.process_once(now=NOW).status == "PUBLISHED"

    # 同一事件被再次投递（例如上游重发）：运行已 SUCCEEDED。
    with factory() as session:
        event = session.scalar(
            select(OutboxEventModel).where(OutboxEventModel.event_type == REQUESTED)
        )
        assert event is not None
        event.status = "PENDING"
        event.published_at = None
        event.available_at = NOW
        session.commit()

    again = worker.process_once(now=NOW)

    assert again.status == "PUBLISHED"
    assert again.run_id == run.run_id
    with factory() as session:
        row = session.get(QualityRunModel, run.run_id)
        assert row is not None
        assert row.lifecycle == QualityRunLifecycle.SUCCEEDED.value
        event = session.scalar(
            select(OutboxEventModel).where(OutboxEventModel.event_type == REQUESTED)
        )
        assert event is not None
        assert event.status == "PUBLISHED"


def test_queue_claim_only_takes_requested_events(factory: sessionmaker[Session]) -> None:
    """队列按事件类型过滤：别的投递类事件不会被质检 Worker 领走。"""
    ids = seed_identity(factory)
    with factory() as session:
        SqlOutboxRepository(session).enqueue(
            ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a),
            _other_event(ids.project_a),
        )
        session.commit()

        claimed = SqlOutboxValidationQueue(session).claim_next(now=NOW, event_type=REQUESTED)

        assert claimed is None


def _other_event(project_id: UUID):
    from app.application.audit import OutboxEvent  # noqa: PLC0415 - 仅测试内使用

    return OutboxEvent(
        event_id=uuid4(),
        project_id=project_id,
        aggregate_type="DATASET",
        aggregate_id=uuid4(),
        event_type="dataset.export.requested",
        payload={},
        dispatch=True,
        occurred_at=NOW,
    )
