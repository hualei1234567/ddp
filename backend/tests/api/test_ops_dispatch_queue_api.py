"""投递队列运维快照端点测试（V4.1 FR-042）。

覆盖：队列状态分布、重试计数、最老待处理年龄、stale 判定、失败按类型聚合、
项目隔离、权限（仅 owner/平台管理员）、只读（无写方法）、无 payload 泄漏。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.collection import OutboxEvent
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def _event(
    *,
    project_id: UUID,
    status: str,
    dispatch: bool = True,
    event_type: str = "dataset.export.requested",
    attempts: int = 0,
    available_at: datetime | None = None,
) -> OutboxEvent:
    moment = available_at or NOW
    return OutboxEvent(
        id=uuid4(),
        project_id=project_id,
        aggregate_type="DATASET_VERSION",
        aggregate_id=uuid4(),
        event_type=event_type,
        payload={"secret_field": "must-not-leak"},
        status=status,
        dispatch=dispatch,
        attempts=attempts,
        available_at=moment,
        created_at=moment,
    )


def _url(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/ops/dispatch-queue"


def _seed(factory: sessionmaker[Session], ids: ApiIds) -> None:
    with factory() as session:
        session.add_all(
            [
                # 3 条待处理：其中 1 条在重试、1 条已积压 30 分钟
                _event(project_id=ids.project_a, status="PENDING"),
                _event(project_id=ids.project_a, status="PENDING", attempts=2),
                _event(
                    project_id=ids.project_a,
                    status="PENDING",
                    available_at=NOW - timedelta(minutes=30),
                ),
                _event(project_id=ids.project_a, status="PUBLISHED"),
                # 2 条失败，不同类型各 1
                _event(
                    project_id=ids.project_a,
                    status="FAILED",
                    event_type="dataset.export.failed",
                ),
                _event(
                    project_id=ids.project_a,
                    status="FAILED",
                    event_type="quality.run.failed",
                ),
                # 留痕类：不应计入投递口径
                _event(project_id=ids.project_a, status="RECORDED", dispatch=False),
                # 其他项目：不得出现
                _event(project_id=ids.project_b, status="PENDING"),
            ]
        )
        session.commit()


def test_snapshot_reports_dispatch_queue_health(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _seed(factory, ids)
    owner = make_client(factory, ids.owner_a)

    response = owner.get(_url(ids.project_a))
    assert response.status_code == 200, response.text
    body = response.json()

    # 只统计本项目 dispatch=True 的事件
    assert body["pending"] == 3
    assert body["published"] == 1
    assert body["failed"] == 2
    assert body["retrying"] == 1
    # 最老待处理距今 30 分钟
    assert body["oldest_pending_age_seconds"] >= 1800
    assert body["stale"] is True
    # 失败按类型聚合
    buckets = {item["event_type"]: item["count"] for item in body["failures_by_event_type"]}
    assert buckets == {"dataset.export.failed": 1, "quality.run.failed": 1}
    assert body["generated_at"]


def test_snapshot_is_scoped_to_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """其他项目的积压不得出现在本项目的运维视图里。"""
    factory, ids = api_db
    _seed(factory, ids)
    owner = make_client(factory, ids.owner_a)

    body = owner.get(_url(ids.project_a)).json()
    # project_b 只有 1 条 PENDING，若跨项目泄漏会变成 4
    assert body["pending"] == 3


def test_empty_queue_reports_null_age_not_zero(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """无待处理时 ``oldest_pending_age_seconds`` 必须是 null——0 会被误读成「刚刚卡住」。"""
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    body = owner.get(_url(ids.project_a)).json()
    assert body["pending"] == 0
    assert body["oldest_pending_age_seconds"] is None
    assert body["stale"] is False
    assert body["failures_by_event_type"] == []


@pytest.mark.parametrize("role_client", ["viewer", "editor", "outsider"])
def test_non_owner_cannot_read_ops_snapshot(
    api_db: tuple[sessionmaker[Session], ApiIds], role_client: str
) -> None:
    """运维视图只对项目负责人口径开放（owner 或平台管理员）。"""
    factory, ids = api_db
    user = {
        "viewer": ids.viewer_a,
        "editor": ids.editor_a,
        "outsider": ids.outsider,
    }[role_client]
    client = make_client(factory, user)
    response = client.get(_url(ids.project_a))
    assert response.status_code == 403, response.text


def test_snapshot_does_not_leak_event_payload(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """响应只含计数与时间：事件 payload 里的业务字段不得出现。"""
    factory, ids = api_db
    _seed(factory, ids)
    owner = make_client(factory, ids.owner_a)
    raw = owner.get(_url(ids.project_a)).text
    assert "must-not-leak" not in raw
    assert "payload" not in raw


def test_endpoint_is_read_only(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """不提供任何写方法：清理/重投必须走 Worker 运维流程并留审计。"""
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    for method in ("post", "patch", "put", "delete"):
        response = getattr(owner, method)(_url(ids.project_a))
        assert response.status_code == 405, (method, response.status_code)
