"""接入任务 Worker：任务真的会被执行，且失败留痕。

此前 `ingestion.job.created` 是留痕类事件，没有消费者——任务建完永远停在
QUEUED。本文件锁住「有人真的在处理」这件事。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.domain.ingestion.enums import IngestionJobStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from app.workers.ingestion_jobs import (
    JOB_CREATED_EVENT,
    IngestionJobWorker,
    JobProcessingStatus,
)
from sqlalchemy.orm import Session, sessionmaker


def _now() -> datetime:
    """事件行的 available_at 用 CURRENT_TIMESTAMP（真实当前时间），所以"现在"
    必须在**调用时**取，不能是模块导入时的常量——模块级常量会早于事件插入，
    claim_next 的 `available_at <= now` 就不成立了。"""
    return datetime.now(UTC)


@pytest.fixture()
def env(tmp_path: Path):
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'jobs.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_id, actor_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code=f"p-{project_id.hex[:6]}", name="Job 项目"))
        session.add(User(id=actor_id, display_name="提交人"))
        session.flush()
        session.add(ProjectMember(project_id=project_id, user_id=actor_id, role="editor"))
        session.commit()
    yield factory, project_id, actor_id
    engine.dispose()


def _settings() -> Settings:
    return Settings(validation_max_attempts=3)  # type: ignore[call-arg]


def _seed_job_event(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    job_type: str = "VALIDATE",
    status: str = "QUEUED",
) -> UUID:
    """直接插入一条 QUEUED 任务 + 一条投递类事件。"""
    from app.infrastructure.db.models.collection import OutboxEvent
    from app.infrastructure.db.models.jobs import IngestionJobModel

    job_id = uuid4()
    with factory() as session:
        session.add(
            IngestionJobModel(
                id=job_id,
                project_id=project_id,
                job_type=job_type,
                status=status,
                timeout_sec=300,
                # DB CHECK ck_ingestion_jobs_input_present 要求：至少一个
                # raw_asset 或 input_manifest_key。这是正确的保护，不是障碍。
                input_manifest_key="canonical/inputs/seed.json",
                raw_asset_ids=[],
                output_refs=[],
                metrics={},
                attempt=1,
                max_attempts=3,
                created_by=actor_id,
                updated_by=actor_id,
                version=1,
            )
        )
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=project_id,
                aggregate_type="INGESTION_JOB",
                aggregate_id=job_id,
                event_type=JOB_CREATED_EVENT,
                payload={"job_id": str(job_id)},
                status="PENDING",
                dispatch=True,
                attempts=0,
            )
        )
        session.commit()
    return job_id


def _seed_event_only(factory, project_id: UUID, job_id: UUID) -> None:
    """只投一条事件，不建任务（用于重复投递场景）。"""
    from app.infrastructure.db.models.collection import OutboxEvent

    with factory() as session:
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=project_id,
                aggregate_type="INGESTION_JOB",
                aggregate_id=job_id,
                event_type=JOB_CREATED_EVENT,
                payload={"job_id": str(job_id)},
                status="PENDING",
                dispatch=True,
                attempts=0,
            )
        )
        session.commit()


def _job_status(factory, project_id: UUID, job_id: UUID) -> str:
    from app.domain.identity.models import ProjectScope

    with factory() as session:
        repo = SqlIngestionJobRepository(session)
        job = repo.get(ProjectScope(project_id, actor_id=project_id), job_id)
    assert job is not None
    return job.status.value


def test_worker_processes_queued_job(env) -> None:
    """QUEUED 的 VALIDATE 任务被执行并推进到 SUCCEEDED。"""
    factory, project_id, actor_id = env
    job_id = _seed_job_event(factory, project_id, actor_id, job_type="VALIDATE")

    result = IngestionJobWorker(factory, _settings()).process_once(now=_now())

    assert result["status"] == JobProcessingStatus.PUBLISHED.value
    assert result["job_id"] == str(job_id)
    assert _job_status(factory, project_id, job_id) == IngestionJobStatus.SUCCEEDED.value


def test_worker_is_idle_without_events(env) -> None:
    factory, _pid, _actor = env
    result = IngestionJobWorker(factory, _settings()).process_once(now=_now())
    assert result["status"] == JobProcessingStatus.IDLE.value


def test_unsupported_job_type_fails_without_faking_success(env) -> None:
    """不支持的 job_type 必须明确失败，而不是假装成功。"""
    factory, project_id, actor_id = env
    job_id = _seed_job_event(factory, project_id, actor_id, job_type="SEGMENT_EPISODE")

    result = IngestionJobWorker(factory, _settings()).process_once(now=_now())

    assert result["status"] == JobProcessingStatus.UNSUPPORTED.value
    assert _job_status(factory, project_id, job_id) == IngestionJobStatus.FAILED.value


def test_parse_metadata_is_explicitly_unsupported(env) -> None:
    factory, project_id, actor_id = env
    job_id = _seed_job_event(factory, project_id, actor_id, job_type="PARSE_METADATA")

    result = IngestionJobWorker(factory, _settings()).process_once(now=_now())

    assert result["status"] == JobProcessingStatus.UNSUPPORTED.value
    assert _job_status(factory, project_id, job_id) == IngestionJobStatus.FAILED.value


def test_already_advanced_job_is_not_reprocessed(env) -> None:
    """幂等：任务已被别人推进过（不在 QUEUED），重复事件不重复执行。

    注意不能直接把任务建成 SUCCEEDED——DB CHECK 要求初始状态必须是 QUEUED
    （`ck_ingestion_jobs_initial_status`），构造"已推进"必须先跑一次真实执行。
    """
    factory, project_id, actor_id = env
    job_id = _seed_job_event(factory, project_id, actor_id, job_type="VALIDATE")
    first = IngestionJobWorker(factory, _settings()).process_once(now=_now())
    assert first["status"] == JobProcessingStatus.PUBLISHED.value
    assert _job_status(factory, project_id, job_id) == IngestionJobStatus.SUCCEEDED.value

    # 再投一条同样的事件（模拟重复投递），必须被跳过。
    _seed_event_only(factory, project_id, job_id)
    second = IngestionJobWorker(factory, _settings()).process_once(now=_now())

    assert second["status"] == JobProcessingStatus.PUBLISHED.value
    assert "skipped" in second
    assert _job_status(factory, project_id, job_id) == IngestionJobStatus.SUCCEEDED.value


def test_malformed_payload_goes_to_dead_letter(env) -> None:
    factory, project_id, actor_id = env
    from app.infrastructure.db.models.collection import OutboxEvent

    with factory() as session:
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=project_id,
                aggregate_type="INGESTION_JOB",
                aggregate_id=uuid4(),
                event_type=JOB_CREATED_EVENT,
                payload={},
                status="PENDING",
                dispatch=True,
                attempts=0,
            )
        )
        session.commit()

    result = IngestionJobWorker(factory, _settings()).process_once(now=_now())

    assert result["status"] == JobProcessingStatus.DEAD_LETTER.value


def test_job_created_event_is_dispatch_class() -> None:
    """事件必须是投递类，否则没有 Worker 会领到它。"""
    # 通过归类脚本的权威清单断言（DISPATCH_EVENTS 是唯一权威）。
    import importlib.util

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "check_outbox_events", root / "scripts" / "check_outbox_events.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert JOB_CREATED_EVENT in module.DISPATCH_EVENTS


# --------------------------------------------------------------------------
# 导入 Worker 的重复事件幂等（真实库曾积压 38 条）
# --------------------------------------------------------------------------


def test_terminal_import_event_is_published_not_retried(env, tmp_path) -> None:
    """任务已在终态时，重复投递的事件必须直接 PUBLISHED。

    真实库里曾积压 38 条这样的历史事件：任务早已 SUCCEEDED，事件仍是 PENDING。
    Worker 每次都领取、执行、抛"状态不允许执行"，然后 mark_retry —— 白烧
    attempts 直到死信，把「已完成的导入」记成「反复失败」。
    """
    import httpx
    from app.domain.datasources.models import DataSource, DataSourceImport
    from app.domain.identity.models import ProjectScope
    from app.infrastructure.db.models.collection import OutboxEvent
    from app.infrastructure.repositories.datasources import (
        SqlDataSourceImportRepository,
        SqlDataSourceRepository,
    )
    from app.workers.datasources import IMPORT_REQUESTED_EVENT, DataSourceImportWorker

    factory, project_id, actor_id = env
    scope = ProjectScope(project_id, actor_id=actor_id)
    with factory() as session:
        source = SqlDataSourceRepository(session).add(
            scope,
            DataSource(
                data_source_id=uuid4(),
                project_id=project_id,
                code=f"hf-{uuid4().hex[:8]}",
                name="源",
                kind="HUGGINGFACE",
                repo_id="lerobot/aloha_sim_insertion_human",
                revision="a" * 40,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
            ),
        )
        job = SqlDataSourceImportRepository(session).add(
            scope,
            DataSourceImport(
                import_id=uuid4(),
                project_id=project_id,
                data_source_id=source.data_source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=3,
            ),
        )
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=project_id,
                aggregate_type="DATA_SOURCE_IMPORT",
                aggregate_id=job.import_id,
                event_type=IMPORT_REQUESTED_EVENT,
                payload={"import_id": str(job.import_id)},
                status="PENDING",
                dispatch=True,
                attempts=0,
            )
        )
        session.commit()

    settings = _settings()
    with httpx.Client(timeout=5) as client:
        worker = DataSourceImportWorker(factory, settings, client)
        result = worker.process_once(now=_now())

    assert result["status"] == "PUBLISHED"
    assert "skipped" in result
