"""导入成功事件 → 自动 Episode 物化 Worker。

V31-014：此前「导入成功 → Episode」必须由客户端再发一次手工请求
（``POST /data-source-imports/{id}/episodes``），链路断开一次就卡在
RAW_ASSET 阶段。现在 ``datasource.import.succeed`` 由本 Worker 直接消费，
手工端点退化为重放入口。

外部系统只有两处替身（显式标注）：HF 网络与 MinIO 写入。导入状态机、
Outbox、RawAsset、EpisodeService、ReadinessService 全部是生产实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.collection import OutboxEvent
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.e2e.support import (
    EXPECTED_EPISODES,
    InMemoryObjectStore,
    MediaTransport,
    install_object_store,
    register_import_chain,
    run_import_worker,
)

SUCCEED_EVENT = "datasource.import.succeed"


def _base() -> datetime:
    """时间基准必须**晚于**事件创建时刻。

    ``_imported_chain`` 通过 HTTP 创建事件，``available_at`` 取的是真实
    时钟。写死一个日历时刻会让整个文件在该时刻之后必然失败（事件"还没到
    可见时间"，Worker 领不到，返回 IDLE）；模块级常量又会在用例之间冻结，
    前一条用例耗时越多，后一条越容易踩到边界。每次调用取当前时刻 + 1 秒，
    既保证可见，也保留各步骤之间的相对顺序。
    """
    return datetime.now(UTC) + timedelta(seconds=1)


def _imported_chain(
    factory: sessionmaker[Session],
    ids: ApiIds,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code: str,
) -> dict[str, Any]:
    """真实导入一次（Worker 只处理 ``import_requested``，此后停在成功事件）。"""
    editor = make_client(factory, ids.editor_a)
    _source, job = register_import_chain(editor, ids.project_a, code=code)
    install_object_store(monkeypatch, InMemoryObjectStore())
    result = run_import_worker(factory, MediaTransport(), now=_base())
    assert result["status"] == "PUBLISHED", result
    assert result["episodes"] == EXPECTED_EPISODES, result
    return job


def _episode_count(factory: sessionmaker[Session], project_id: UUID) -> int:
    from app.infrastructure.db.models.episodes import EpisodeModel

    with factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(EpisodeModel)
                .where(EpisodeModel.project_id == project_id)
            )
            or 0
        )


def test_succeed_event_materializes_episodes_without_manual_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    job = _imported_chain(factory, ids, monkeypatch, code=f"hf-auto-{uuid4().hex[:6]}")

    # 导入成功事件还没被消费：手工端点没调用过，Episode 仍为 0。
    assert _episode_count(factory, ids.project_a) == 0

    result = run_import_worker(factory, MediaTransport(), now=_base() + timedelta(seconds=2))

    assert result["status"] == "PUBLISHED", result
    assert result["event"] == "episodes", result
    assert result["import_id"] == job["import_id"], result
    assert result["created"] == EXPECTED_EPISODES, result
    assert result["skipped"] == 0, result
    # 自动就绪同样要发生：没有人提交 Manifest，Episode 也应是 READY。
    assert result["ready"] == EXPECTED_EPISODES, result
    assert result["blocked"] == 0, result

    episodes = editor.get(url(ids.project_a, "/episodes"), params={"limit": 100}).json()["items"]
    assert len(episodes) == EXPECTED_EPISODES, episodes
    for body in episodes:
        assert body["status"] == "READY", body
        assert body["manifest_key"] and body["manifest_sha256"], body

    with factory() as session:
        status = session.scalar(
            select(OutboxEvent.status).where(
                OutboxEvent.project_id == ids.project_a,
                OutboxEvent.event_type == SUCCEED_EVENT,
            )
        )
        assert status == "PUBLISHED"


def test_duplicate_succeed_event_does_not_duplicate_episodes(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = api_db
    _imported_chain(factory, ids, monkeypatch, code=f"hf-dup-{uuid4().hex[:6]}")
    first = run_import_worker(factory, MediaTransport(), now=_base() + timedelta(seconds=2))
    assert first["created"] == EXPECTED_EPISODES, first

    # 重复投递：把已发布事件退回 PENDING，模拟 outbox 至少一次语义。
    with factory() as session:
        session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.project_id == ids.project_a,
                OutboxEvent.event_type == SUCCEED_EVENT,
            )
            .values(status="PENDING", published_at=None, attempts=0, available_at=_base())
        )
        session.commit()

    again = run_import_worker(factory, MediaTransport(), now=_base() + timedelta(seconds=4))

    assert again["status"] == "PUBLISHED", again
    assert again["event"] == "episodes", again
    assert again["created"] == 0, again
    assert again["skipped"] == EXPECTED_EPISODES, again
    assert _episode_count(factory, ids.project_a) == EXPECTED_EPISODES


def test_succeed_event_for_unfinished_import_goes_dead_letter(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    _source, job = register_import_chain(editor, ids.project_a, code=f"hf-pend-{uuid4().hex[:6]}")
    import_id = UUID(job["import_id"])

    # 模拟"请求事件已被消费、但导入没有推进"：请求事件直接标记发布，
    # 再人工投递一条与事实不符的成功事件（导入仍是 PENDING）。
    with factory() as session:
        session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.project_id == ids.project_a,
                OutboxEvent.event_type == "datasource.import_requested",
            )
            .values(status="PUBLISHED", published_at=_base())
        )
        session.add(
            OutboxEvent(
                id=uuid4(),
                project_id=ids.project_a,
                aggregate_type="data_source_import",
                aggregate_id=import_id,
                event_type=SUCCEED_EVENT,
                payload={"import_id": str(import_id)},
                status="PENDING",
                dispatch=True,
                attempts=0,
                available_at=_base(),
                created_at=_base(),
            )
        )
        session.commit()

    result = run_import_worker(factory, MediaTransport(), now=_base() + timedelta(seconds=2))

    assert result["status"] == "DEAD_LETTER", result
    assert result["event"] == "episodes", result
    assert "只有 SUCCEEDED" in str(result["error"]), result
    with factory() as session:
        row = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == import_id,
                OutboxEvent.event_type == SUCCEED_EVENT,
            )
        )
        # outbox 的死信状态码是 FAILED（"DEAD_LETTER" 是 Worker 的结果口径）。
        assert row is not None and row.status == "FAILED"
        assert "只有 SUCCEEDED" in (row.last_error or "")
    assert _episode_count(factory, ids.project_a) == 0


def test_transient_materialize_failure_is_retried(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = api_db
    _imported_chain(factory, ids, monkeypatch, code=f"hf-retry-{uuid4().hex[:6]}")

    from app.workers import datasources as worker_module

    def _boom(_session: Session) -> Any:
        raise RuntimeError("数据库连接抖动")

    monkeypatch.setattr(worker_module, "build_episode_materializer", _boom)
    result = run_import_worker(factory, MediaTransport(), now=_base() + timedelta(seconds=2))

    assert result["status"] == "RETRIED", result
    assert result["event"] == "episodes", result
    assert result["attempts"] == 1, result
    assert _episode_count(factory, ids.project_a) == 0
    with factory() as session:
        status, attempts = session.execute(
            select(OutboxEvent.status, OutboxEvent.attempts).where(
                OutboxEvent.project_id == ids.project_a,
                OutboxEvent.event_type == SUCCEED_EVENT,
            )
        ).one()
        assert status == "PENDING" and attempts == 1
