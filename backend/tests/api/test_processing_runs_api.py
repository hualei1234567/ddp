"""ProcessingRun HTTP API 测试。

覆盖：创建 / 列表 / 详情、项目隔离、跨项目不可见、viewer 写入拒绝、
If-Match 乐观锁、非法内部动作与状态机冲突。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.api.conftest import make_client
from tests.api.dataset_support import url

pytestmark = pytest.mark.usefixtures("api_db")


def _create(client, project_id, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_version": "pipeline-v1",
        "input_refs": ["raw/asset-1"],
    }
    payload.update(overrides)
    response = client.post(url(project_id, "/processing-runs"), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_and_allowed_actions(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)

    created = _create(editor, ids.project_a)
    assert created["status"] == "CREATED"
    assert created["stage"] is None
    assert created["attempt"] == 0
    assert created["retry_count"] == 0
    assert created["allowed_actions"] == ["cancel"]
    assert created["version"] == 1
    assert created["project_id"] == str(ids.project_a)

    detail = viewer.get(url(ids.project_a, f"/processing-runs/{created['run_id']}"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["run_id"] == created["run_id"]
    assert detail.headers["ETag"] == '"1"'


def test_list_filters_and_pagination(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    first = _create(editor, ids.project_a, input_refs=["raw/1"])
    second = _create(editor, ids.project_a, input_refs=["raw/2"], pipeline_version="pipeline-v2")

    page = editor.get(url(ids.project_a, "/processing-runs"), params={"limit": 1})
    assert page.status_code == 200, page.text
    body = page.json()
    assert [item["run_id"] for item in body["items"]] == [first["run_id"]]
    assert body["next_cursor"]

    rest = editor.get(
        url(ids.project_a, "/processing-runs"),
        params={"limit": 1, "cursor": body["next_cursor"]},
    )
    assert rest.status_code == 200, rest.text
    assert [item["run_id"] for item in rest.json()["items"]] == [second["run_id"]]

    filtered = editor.get(
        url(ids.project_a, "/processing-runs"),
        params={"status": "CREATED", "stage": "FORMAT_PARSING"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []


def test_cancel_requires_if_match_and_is_idempotent_only_once(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    run = _create(editor, ids.project_a)

    missing = editor.post(url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/cancel"))
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    stale = editor.post(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/cancel"),
        headers={"If-Match": '"2"'},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    cancelled = editor.post(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/cancel"),
        headers={"If-Match": f'"{run["version"]}"'},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["allowed_actions"] == []
    assert cancelled.headers["ETag"] == '"2"'

    again = editor.post(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/cancel"),
        headers={"If-Match": '"2"'},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "state_transition_invalid"


def test_internal_actions_cannot_be_invoked_by_clients(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    run = _create(editor, ids.project_a)
    response = editor.post(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/worker-succeed"),
        headers={"If-Match": f'"{run["version"]}"'},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_project_isolation_and_role_permissions(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    outsider = make_client(factory, ids.outsider)
    owner = make_client(factory, ids.owner_a)

    forbidden = viewer.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "input_refs": ["raw/1"]},
    )
    assert forbidden.status_code == 403

    run = _create(editor, ids.project_a)

    hidden = outsider.get(url(ids.project_a, f"/processing-runs/{run['run_id']}"))
    assert hidden.status_code in {403, 404}

    # owner 同时在项目 A/B；用 B 的路径访问 A 的运行必须 404，不能泄漏存在性。
    cross = owner.get(url(ids.project_b, f"/processing-runs/{run['run_id']}"))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    assert owner.get(url(ids.project_b, "/processing-runs")).json()["items"] == []


def test_invalid_payload_and_unknown_run(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    empty_refs = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "input_refs": []},
    )
    assert empty_refs.status_code == 422

    extra_field = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={
            "pipeline_version": "pipeline-v1",
            "input_refs": ["raw/1"],
            "project_id": str(ids.project_b),
        },
    )
    assert extra_field.status_code == 422

    missing = editor.get(url(ids.project_a, f"/processing-runs/{uuid4()}"))
    assert missing.status_code == 404

    invalid_status = editor.get(
        url(ids.project_a, "/processing-runs"),
        params={"status": "UNKNOWN"},
    )
    assert invalid_status.status_code == 422

    invalid_cursor = editor.get(
        url(ids.project_a, "/processing-runs"),
        params={"cursor": "not-a-cursor"},
    )
    assert invalid_cursor.status_code == 422
