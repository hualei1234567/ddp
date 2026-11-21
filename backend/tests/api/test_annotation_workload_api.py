"""标注工作量聚合端点（V31-026）。

后端 `AnnotationAssignmentUseCases.workload` 早已实现并落库，但从未暴露成
HTTP：运营页因此拿不到任何真实数字。本测试锁定三件事——

1. 端点按服务端身份与项目隔离返回聚合值；
2. 周期非法与「不是项目成员」都如实拒绝，不返回全 0 冒充"没有工作量"；
3. 容量未配置时 `utilization_ratio` 为 `null`、状态为 `NORMAL`，
   绝不用 0 冒充"未超容量"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client

WORKLOAD = "/annotation-workload"


def _period() -> dict[str, str]:
    end = datetime.now(UTC)
    start = end - timedelta(days=7)
    return {"period_start": start.isoformat(), "period_end": end.isoformat()}


def _work(factory, ids: ApiIds) -> None:
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-WL-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-wl")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-wl")
    create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-wl",
    )


def test_workload_returns_real_aggregate(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)
    client = make_client(factory, ids.owner_a)

    response = client.get(
        url(ids.project_a, WORKLOAD),
        params={"assignee_id": str(ids.editor_a), **_period()},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(ids.project_a)
    assert body["user_id"] == str(ids.editor_a)
    assert body["assigned_item_count"] == 1
    assert body["source_version"] == "annotation-assignment-events/1"
    # 容量未配置：如实为 null，状态不能被伪造成 OVER_CAPACITY 或 0 利用率。
    assert body["capacity_minutes"] is None
    assert body["utilization_ratio"] is None
    assert body["status"] == "NORMAL"


def test_non_member_assignee_is_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)
    client = make_client(factory, ids.owner_a)

    response = client.get(
        url(ids.project_a, WORKLOAD),
        params={"assignee_id": str(ids.outsider), **_period()},
    )

    assert response.status_code == 404, response.text
    # 域错误码：成员不存在（不区分"项目不存在"与"不是成员"，避免成员枚举）。
    assert response.json()["error"]["code"] == "resource_not_found"


def test_inverted_period_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)
    client = make_client(factory, ids.owner_a)
    now = datetime.now(UTC)

    response = client.get(
        url(ids.project_a, WORKLOAD),
        params={
            "assignee_id": str(ids.editor_a),
            "period_start": now.isoformat(),
            "period_end": (now - timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_outsider_cannot_read_project_workload(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)

    response = make_client(factory, ids.outsider).get(
        url(ids.project_a, WORKLOAD),
        params={"assignee_id": str(ids.editor_a), **_period()},
    )

    assert response.status_code == 403, response.text


def test_workload_requires_period_bounds(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)

    response = make_client(factory, ids.owner_a).get(
        url(ids.project_a, WORKLOAD),
        params={"assignee_id": str(ids.editor_a)},
    )

    # 周期必填：没有时间窗口，"工作量"没有定义，不能默认成"全部时间"。
    assert response.status_code == 422, response.text


def test_workload_response_is_project_scoped(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)
    # 负责人同时是项目 B 的 owner；查询项目 A 的聚合不得串到 B。
    client = make_client(factory, ids.owner_a)

    response = client.get(
        url(ids.project_b, WORKLOAD),
        params={"assignee_id": str(ids.owner_a), **_period()},
    )

    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == str(ids.project_b)
    assert response.json()["assigned_item_count"] == 0


def test_period_upper_bound_excludes_future_assignments(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _work(factory, ids)
    client = make_client(factory, ids.owner_a)
    past = datetime.now(UTC) - timedelta(days=30)

    response = client.get(
        url(ids.project_a, WORKLOAD),
        params={
            "assignee_id": str(ids.editor_a),
            "period_start": (past - timedelta(days=7)).isoformat(),
            "period_end": past.isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["assigned_item_count"] == 0
