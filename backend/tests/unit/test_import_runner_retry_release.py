"""ImportRunner 释放可重试失败：真实仓储 + 真实状态机，不 mock。

与 ``test_import_retry_state_machine`` 的分工：那个文件测纯领域规则，
本文件测**用例层真的把状态改对了**——因为原缺陷正出在用例与 Worker 的
配合上（状态机允许、但没人调用；Worker 反而把事件排进了下一轮）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.datasources.import_runner import ImportRunner
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import SqlOutboxRepository
from app.infrastructure.repositories.dataset_audit import SqlDatasetAuditRepository
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlDataSourceRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 16, tzinfo=UTC)
_SHA = "a" * 40


class _NoopStorage:
    def put(self, key: str, data: bytes, *, content_type: str) -> None:  # noqa: ARG002
        raise AssertionError("本用例不应写对象存储")


@pytest.fixture()
def env(tmp_path: Path) -> tuple[sessionmaker[Session], ProjectScope]:
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'retry.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_id, actor_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code=f"p-{project_id.hex[:6]}", name="重试项目"))
        session.add(User(id=actor_id, display_name="导入提交人"))
        session.flush()
        session.add(ProjectMember(project_id=project_id, user_id=actor_id, role="editor"))
        session.commit()
    try:
        yield factory, ProjectScope(project_id, actor_id=actor_id)
    finally:
        engine.dispose()


def _runner(session: Session) -> ImportRunner:
    return ImportRunner(
        sources=SqlDataSourceRepository(session),
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        raw_assets=SqlRawAssetRepository(session),
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        storage=_NoopStorage(),  # type: ignore[arg-type]
        uow=session,
    )


def _seed_failed_import(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    *,
    attempt: int = 1,
    max_attempts: int = 3,
) -> object:
    with factory() as session:
        source = SqlDataSourceRepository(session).add(
            scope,
            DataSource(
                data_source_id=uuid4(),
                project_id=scope.project_id,
                code=f"hf-{uuid4().hex[:8]}",
                name="示例数据源",
                kind="HUGGINGFACE",
                repo_id="lerobot/aloha_sim_insertion_human",
                revision=_SHA,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
            ),
        )
        item = SqlDataSourceImportRepository(session).add(
            scope,
            DataSourceImport(
                import_id=uuid4(),
                project_id=scope.project_id,
                data_source_id=source.data_source_id,
                status=ImportStatus.FAILED,
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=10,
                error_message="网络抖动",
                finished_at=NOW,
                attempt=attempt,
                max_attempts=max_attempts,
            ),
        )
        session.commit()
        return item.import_id


def test_failed_import_is_released_back_to_pending(env) -> None:
    """可重试失败退回 PENDING，并且 attempt 递增、错误信息清空。"""
    factory, scope = env
    import_id = _seed_failed_import(factory, scope)

    with factory() as session:
        assert _runner(session).release_for_retry(scope, import_id, moment=NOW) is True

    with factory() as session:
        saved = SqlDataSourceImportRepository(session).get(scope, import_id)
    assert saved is not None
    assert saved.status is ImportStatus.PENDING
    assert saved.attempt == 2
    assert saved.error_message is None
    assert saved.started_at is None
    assert saved.finished_at is None


def test_released_import_can_be_executed_again(env) -> None:
    """退回后再次 execute 的前置检查确实放行——这是死锁解除的关键。

    不实际下载：execute 会走到"数据源存在"之后才需网络，这里用一个必然
    抛错的 fetcher，断言它抛的是**网络层**错误而不是"状态不允许执行"。
    """
    factory, scope = env
    import_id = _seed_failed_import(factory, scope)

    with factory() as session:
        _runner(session).release_for_retry(scope, import_id, moment=NOW)

    class _Boom(Exception):
        pass

    def _fetch(**kwargs: object) -> object:
        raise _Boom("fetch 不可用")

    with factory() as session:
        with pytest.raises(_Boom):
            _runner(session).execute(
                scope,
                import_id,
                fetch=_fetch,  # type: ignore[arg-type]
                parse_metadata=lambda data: [],
                parse_episodes=lambda rows: (),
                now=NOW,
            )


def test_exhausted_import_stays_failed(env) -> None:
    """attempt 已达上限时不得无限重试，留在 FAILED 等人工处理。"""
    factory, scope = env
    import_id = _seed_failed_import(factory, scope, attempt=3, max_attempts=3)

    with factory() as session:
        assert _runner(session).release_for_retry(scope, import_id, moment=NOW) is False

    with factory() as session:
        saved = SqlDataSourceImportRepository(session).get(scope, import_id)
    assert saved is not None
    assert saved.status is ImportStatus.FAILED
    assert saved.attempt == 3


def test_release_is_noop_for_non_failed_import(env) -> None:
    """只有 FAILED 才谈得上"退回重试"；PENDING 的行不该被改。"""
    factory, scope = env
    import_id = _seed_failed_import(factory, scope)
    with factory() as session:
        repo = SqlDataSourceImportRepository(session)
        current = repo.get(scope, import_id)
        assert current is not None
        repo.save(scope, _replace_status(current), expected_version=current.row_version)
        session.commit()

    with factory() as session:
        assert _runner(session).release_for_retry(scope, import_id, moment=NOW) is False


def _replace_status(item: DataSourceImport) -> DataSourceImport:
    from dataclasses import replace

    return replace(item, status=ImportStatus.PENDING, error_message=None, finished_at=None)


def test_import_records_real_duration(env) -> None:
    """开始与结束时间必须分别取时钟：否则导入耗时恒为 0。

    此前整个 ``execute`` 只在入口取一次 ``moment``，``started_at`` 与
    ``finished_at`` 写的是同一个值，运维无法判断一次导入是大文件慢还是卡死。
    ``clock`` 是注入点：开始时刻用 ``now``，结束时重新取时钟。
    """
    factory, scope = env
    import_id = _seed_failed_import(factory, scope)
    with factory() as session:
        assert _runner(session).release_for_retry(scope, import_id, moment=NOW) is True

    started = NOW
    finished = NOW + __import__("datetime").timedelta(seconds=42)

    def _fetch(**kwargs: object) -> object:
        raise RuntimeError("网络不可用")

    with factory() as session:
        with pytest.raises(RuntimeError):
            _runner(session).execute(
                scope,
                import_id,
                fetch=_fetch,  # type: ignore[arg-type]
                parse_metadata=lambda data: [],
                parse_episodes=lambda rows: (),
                now=started,
                clock=lambda: finished,
            )

    with factory() as session:
        saved = SqlDataSourceImportRepository(session).get(scope, import_id)
    assert saved is not None
    assert saved.status is ImportStatus.FAILED
    assert saved.started_at == started
    assert saved.finished_at == finished
    assert saved.finished_at > saved.started_at


def test_import_finish_time_never_precedes_start(env) -> None:
    """注入的未来开始时间不得让结束时间倒挂（max(时钟, 开始) 兜底）。"""
    factory, scope = env
    import_id = _seed_failed_import(factory, scope)
    with factory() as session:
        assert _runner(session).release_for_retry(scope, import_id, moment=NOW) is True

    future_start = NOW + __import__("datetime").timedelta(minutes=5)

    def _fetch(**kwargs: object) -> object:
        raise RuntimeError("网络不可用")

    with factory() as session:
        with pytest.raises(RuntimeError):
            _runner(session).execute(
                scope,
                import_id,
                fetch=_fetch,  # type: ignore[arg-type]
                parse_metadata=lambda data: [],
                parse_episodes=lambda rows: (),
                now=future_start,
                clock=lambda: NOW,
            )

    with factory() as session:
        saved = SqlDataSourceImportRepository(session).get(scope, import_id)
    assert saved is not None
    assert saved.started_at == future_start
    assert saved.finished_at == future_start
