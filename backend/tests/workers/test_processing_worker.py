"""处理运行 Worker（N4-002）：事件真的会被消费，失败按 outbox 语义收口。

覆盖：happy path、阶段失败 → 退避重试 → 死信（先判死信再 release，不留孤儿）、
重复投递幂等、单事件只被一个 Worker 消费、运行缺失进死信。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.use_cases import (
    PROCESSING_RUN_REQUESTED_EVENT,
    ProcessingRunUseCases,
)
from app.core.config import Settings
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingRunStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import OutboxEvent
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.workers.processing import (
    RUN_REQUESTED_EVENT,
    ProcessingRunWorker,
    RunProcessingStatus,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity


class FakeInspector:
    def __init__(self, sizes: dict[str, int] | None = None) -> None:
        self._sizes = sizes or {}

    def object_size(self, object_key: str) -> int | None:
        return self._sizes.get(object_key)


def _now() -> datetime:
    """事件 available_at 用真实当前时间，因此"现在"必须在调用时取。"""
    return datetime.now(UTC)


@pytest.fixture()
def env(tmp_path: Path):
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'processing.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = seed_identity(factory)
    yield factory, ids
    engine.dispose()


def _settings(max_attempts: int = 2) -> Settings:
    return Settings(  # type: ignore[call-arg]
        validation_max_attempts=max_attempts,
        validation_retry_base_seconds=1.0,
        validation_retry_max_seconds=2.0,
    )


def test_claim_lease_prevents_second_worker_from_taking_the_same_event(api_db) -> None:
    """claim 后必须落租约：pipeline 中途提交会释放行锁，否则事件会被重复领取。"""
    from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue

    factory, ids = api_db
    _create_run(factory, ids)
    moment = _now()
    with factory() as session:
        queue = SqlOutboxValidationQueue(session)
        claimed = queue.claim_next(now=moment, event_type=RUN_REQUESTED_EVENT)
        assert claimed is not None
        assert queue.mark_claimed(claimed.event_id, now=moment, lease_seconds=60)
        session.commit()

    with factory() as session:
        queue = SqlOutboxValidationQueue(session)
        assert queue.claim_next(now=moment, event_type=RUN_REQUESTED_EVENT) is None


def _create_run(
    factory: sessionmaker[Session],
    ids,
    *,
    input_refs: tuple[str, ...] | None = None,
) -> UUID:
    with factory() as session:
        runs = SqlProcessingRunRepository(session)
        use_cases = ProcessingRunUseCases(
            runs,
            SqlProcessingRunStepRepository(session),
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        )
        run = use_cases.create(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=input_refs or (f"raw/{ids.project_a}/a",),
            ),
        )
        return run.run_id


def _run(factory: sessionmaker[Session], ids, run_id: UUID):
    with factory() as session:
        runs = SqlProcessingRunRepository(session)
        return runs.get(ProjectScope(ids.project_a, actor_id=ids.editor_a), run_id)


def _steps(factory: sessionmaker[Session], ids, run_id: UUID):
    with factory() as session:
        repo = SqlProcessingRunStepRepository(session)
        return repo.list(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            run_id=run_id,
            limit=100,
        )


def _event_status(factory: sessionmaker[Session]) -> list[tuple[str, int]]:
    with factory() as session:
        rows = session.execute(
            select(OutboxEvent.status, OutboxEvent.attempts).where(
                OutboxEvent.event_type == PROCESSING_RUN_REQUESTED_EVENT
            )
        ).all()
        return [(status, attempts) for status, attempts in rows]


def test_event_type_is_the_documented_dispatch_contract() -> None:
    # 事件名是跨进程契约：生产点、消费者与 outbox 归类文档必须是同一个字符串。
    assert RUN_REQUESTED_EVENT == "processing.run.requested"
    assert PROCESSING_RUN_REQUESTED_EVENT == RUN_REQUESTED_EVENT


def test_worker_consumes_requested_event_and_runs_all_stages(env) -> None:
    factory, ids = env
    run_id = _create_run(factory, ids)
    worker = ProcessingRunWorker(
        factory,
        _settings(),
        inspector=FakeInspector({f"raw/{ids.project_a}/a": 12}),
    )

    result = worker.process_once(now=_now())

    assert result["status"] == RunProcessingStatus.PUBLISHED.value
    assert result["run_id"] == str(run_id)
    run = _run(factory, ids, run_id)
    assert run is not None
    assert run.status == ProcessingRunStatus.SUCCEEDED.value
    assert len(_steps(factory, ids, run_id)) == 8
    assert _event_status(factory) == [("PUBLISHED", 0)]


def test_failure_retries_then_dead_letters_without_orphans(env) -> None:
    factory, ids = env
    run_id = _create_run(factory, ids, input_refs=(f"raw/{ids.project_a}/missing",))
    worker = ProcessingRunWorker(factory, _settings(max_attempts=2), inspector=FakeInspector())

    first_moment = _now()
    first = worker.process_once(now=first_moment)

    assert first["status"] == RunProcessingStatus.RETRIED.value
    assert first["attempts"] == 1
    retrying = _run(factory, ids, run_id)
    assert retrying is not None
    assert retrying.status == ProcessingRunStatus.RETRYING.value
    assert _event_status(factory) == [("PENDING", 1)]

    second = worker.process_once(now=first_moment + timedelta(seconds=5))

    assert second["status"] == RunProcessingStatus.DEAD_LETTER.value
    assert second["released_for_retry"] is False
    # 死信时先判死信、不再把运行退回 RETRYING：运行停在 FAILED，原因可查。
    final = _run(factory, ids, run_id)
    assert final is not None
    assert final.status == ProcessingRunStatus.FAILED.value
    assert final.error_code == "object_missing"
    failed_steps = [step for step in _steps(factory, ids, run_id) if step.status == "FAILED"]
    assert [step.attempt for step in failed_steps] == [1, 2]
    assert _event_status(factory) == [("FAILED", 2)]


def test_duplicate_event_is_published_without_extra_steps(env) -> None:
    factory, ids = env
    run_id = _create_run(factory, ids)
    with factory() as session:
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=ids.project_a,
                aggregate_type="PROCESSING_RUN",
                aggregate_id=run_id,
                event_type=PROCESSING_RUN_REQUESTED_EVENT,
                payload={"run_id": str(run_id)},
                status="PENDING",
                dispatch=True,
                attempts=0,
            )
        )
        session.commit()
    worker = ProcessingRunWorker(
        factory,
        _settings(),
        inspector=FakeInspector({f"raw/{ids.project_a}/a": 1}),
    )

    assert worker.process_once(now=_now())["status"] == RunProcessingStatus.PUBLISHED.value
    duplicate = worker.process_once(now=_now())

    assert duplicate["status"] == RunProcessingStatus.PUBLISHED.value
    assert duplicate["outcome"] == "NOOP"
    assert len(_steps(factory, ids, run_id)) == 8


def test_only_the_consuming_worker_gets_the_event(env) -> None:
    factory, ids = env
    _create_run(factory, ids)
    first = ProcessingRunWorker(
        factory, _settings(), inspector=FakeInspector({f"raw/{ids.project_a}/a": 1})
    )
    second = ProcessingRunWorker(
        factory, _settings(), inspector=FakeInspector({f"raw/{ids.project_a}/a": 1})
    )

    assert first.process_once(now=_now())["status"] == RunProcessingStatus.PUBLISHED.value
    assert second.process_once(now=_now())["status"] == RunProcessingStatus.IDLE.value


def test_missing_run_goes_to_dead_letter(env) -> None:
    factory, ids = env
    with factory() as session:
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=ids.project_a,
                aggregate_type="PROCESSING_RUN",
                aggregate_id=uuid4(),
                event_type=PROCESSING_RUN_REQUESTED_EVENT,
                payload={"run_id": str(uuid4())},
                status="PENDING",
                dispatch=True,
                attempts=0,
            )
        )
        session.commit()
    worker = ProcessingRunWorker(factory, _settings(), inspector=FakeInspector())

    result = worker.process_once(now=_now())

    assert result["status"] == RunProcessingStatus.DEAD_LETTER.value
    assert _event_status(factory) == [("FAILED", 1)]
