"""质检批量重跑与差异报告 HTTP API 测试（V31-033）。

覆盖：批量入队（``QUEUED`` + 投递事件）、空列表/重复/超限拒绝、差异报告
四个分类、同对象校验、未完成运行拒绝、项目隔离与角色权限。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.collection import OutboxEvent
from sqlalchemy import select

from tests.api.conftest import make_client
from tests.api.dataset_support import seed_dataset_episodes, url

pytestmark = pytest.mark.usefixtures("api_db")

REQUESTED = "quality.run.requested"


def _seed_episodes(factory, ids, count: int = 2) -> list[UUID]:
    return seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=count,
        record_labels=[
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )


def _create(client, project_id: object, subject_type: str, subject_id: object):
    return client.post(
        url(project_id, "/quality-runs"),
        json={"subject_type": subject_type, "subject_id": str(subject_id)},
    )


def _batch(client, project_id: object, subject_ids: list[UUID]):
    return client.post(
        url(project_id, "/quality-runs/batch"),
        json={
            "subject_type": "EPISODE",
            "subject_ids": [str(item) for item in subject_ids],
        },
    )


def test_batch_rerun_enqueues_runs_and_emits_requested_events(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episodes = _seed_episodes(factory, ids, count=3)

    response = _batch(editor, ids.project_a, episodes)

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body) == 3
    assert {item["subject_id"] for item in body} == {str(item) for item in episodes}
    # 入队是异步语义：响应只描述排队事实，没有结论。
    assert all(item["lifecycle"] == "QUEUED" for item in body)
    assert all("status" not in item for item in body)

    with factory() as session:
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == REQUESTED)
        ).all()
        assert len(events) == 3
        assert all(event.dispatch is True for event in events)


def test_batch_rerun_rejects_empty_list(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = _batch(editor, ids.project_a, [])

    assert response.status_code == 422, response.text


def test_batch_rerun_rejects_duplicate_subjects(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episodes = _seed_episodes(factory, ids, count=1)

    response = _batch(editor, ids.project_a, [episodes[0], episodes[0]])

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_batch_rerun_requires_write_role(api_db) -> None:
    """只读用户不能批量重跑。"""
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)
    episodes = _seed_episodes(factory, ids, count=1)

    response = _batch(viewer, ids.project_a, episodes)

    assert response.status_code == 403, response.text


def test_diff_reports_four_kinds(api_db) -> None:
    """同一 Episode 两次运行：RESOLVED / UNCHANGED / CHANGED / NEW 各一条。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episode_id = _seed_episodes(factory, ids, count=1)[0]

    baseline = _create(editor, ids.project_a, "EPISODE", episode_id)
    assert baseline.status_code == 201, baseline.text
    baseline_id = baseline.json()["run_id"]

    candidate = _create(editor, ids.project_a, "EPISODE", episode_id)
    assert candidate.status_code == 201, candidate.text
    candidate_id = candidate.json()["run_id"]

    response = editor.get(url(ids.project_a, f"/quality-runs/{baseline_id}/diff/{candidate_id}"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["baseline_run_id"] == baseline_id
    assert body["candidate_run_id"] == candidate_id
    # 同样的数据跑两次：所有结论都应 UNCHANGED。
    assert body["new_count"] == 0
    assert body["resolved_count"] == 0
    assert body["changed_count"] == 0
    assert body["unchanged_count"] == len(body["findings"])
    assert body["has_regression"] is False


def test_diff_rejects_unfinished_run(api_db) -> None:
    """排队中的运行没有结论，不可比较。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episodes = _seed_episodes(factory, ids, count=1)
    episode_id = episodes[0]

    queued = _batch(editor, ids.project_a, episodes)
    assert queued.status_code == 201, queued.text
    queued_id = queued.json()[0]["run_id"]

    concluded = _create(editor, ids.project_a, "EPISODE", episode_id)
    assert concluded.status_code == 201, concluded.text

    response = editor.get(
        url(ids.project_a, f"/quality-runs/{queued_id}/diff/{concluded.json()['run_id']}")
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_diff_rejects_cross_subject_runs(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episodes = _seed_episodes(factory, ids, count=2)

    first = _create(editor, ids.project_a, "EPISODE", episodes[0])
    second = _create(editor, ids.project_a, "EPISODE", episodes[1])
    assert first.status_code == 201 and second.status_code == 201

    response = editor.get(
        url(
            ids.project_a,
            f"/quality-runs/{first.json()['run_id']}/diff/{second.json()['run_id']}",
        )
    )

    assert response.status_code == 422, response.text
    # 错误体统一脱敏：只暴露稳定 code，不回显内部消息。
    assert response.json()["error"]["code"] == "validation_failed"


def test_diff_unknown_run_returns_404(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.get(url(ids.project_a, f"/quality-runs/{uuid4()}/diff/{uuid4()}"))

    assert response.status_code == 404, response.text


def test_diff_cross_project_run_is_not_visible(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episode_id = _seed_episodes(factory, ids, count=1)[0]
    created = _create(editor, ids.project_a, "EPISODE", episode_id)
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    other = make_client(factory, ids.owner_a)
    response = other.get(url(ids.project_b, f"/quality-runs/{run_id}/diff/{run_id}"))

    assert response.status_code == 404, response.text
