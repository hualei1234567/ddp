"""ProcessingRun HTTP API 测试。

覆盖：创建 / 列表 / 详情、项目隔离、跨项目不可见、viewer 写入拒绝、
If-Match 乐观锁、非法内部动作与状态机冲突。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.collection import AuditLog
from sqlalchemy import select

from tests.api.conftest import make_client
from tests.api.dataset_support import url

pytestmark = pytest.mark.usefixtures("api_db")


def _audit_rows(factory, run_id: str) -> dict[str, AuditLog]:
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(
                AuditLog.resource_type == "PROCESSING_RUN",
                AuditLog.resource_id == UUID(run_id),
            )
        ).all()
        return {row.action: row for row in rows}


def _create(client, project_id, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_version": "pipeline-v1",
        "input_refs": [f"raw/{project_id}/asset-1"],
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
    assert created["closed_at"] is None
    assert created["allowed_actions"] == ["cancel"]
    assert created["version"] == 1
    assert created["project_id"] == str(ids.project_a)

    detail = viewer.get(url(ids.project_a, f"/processing-runs/{created['run_id']}"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["run_id"] == created["run_id"]
    assert detail.headers["ETag"] == '"1"'

    audits = _audit_rows(factory, created["run_id"])
    create_row = audits["create"]
    assert create_row.actor_user_id == ids.editor_a
    assert (create_row.from_status, create_row.to_status) == (None, "CREATED")
    assert create_row.request_id is not None


def test_list_filters_and_pagination(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    first = _create(editor, ids.project_a, input_refs=[f"raw/{ids.project_a}/1"])
    second = _create(
        editor,
        ids.project_a,
        input_refs=[f"raw/{ids.project_a}/2"],
        pipeline_version="pipeline-v2",
    )

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

    cancel_audit = _audit_rows(factory, run["run_id"])["cancel"]
    assert cancel_audit.actor_user_id == ids.editor_a
    assert (cancel_audit.from_status, cancel_audit.to_status) == ("CREATED", "CANCELLED")
    assert cancel_audit.request_id is not None

    again = editor.post(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/cancel"),
        headers={"If-Match": '"2"'},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "state_transition_invalid"


@pytest.mark.parametrize("action", ["worker-succeed", "worker-retry", "system-close"])
def test_internal_actions_cannot_be_invoked_by_clients(api_db, action: str) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    run = _create(editor, ids.project_a)
    response = editor.post(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/actions/{action}"),
        headers={"If-Match": f'"{run["version"]}"'},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"

    # 伪造内部动作不得改变状态/版本，也不得留下任何审计。
    unchanged = editor.get(url(ids.project_a, f"/processing-runs/{run['run_id']}"))
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "CREATED"
    assert unchanged.json()["version"] == run["version"]
    assert set(_audit_rows(factory, run["run_id"])) == {"create"}


def test_project_isolation_and_role_permissions(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    outsider = make_client(factory, ids.outsider)
    owner = make_client(factory, ids.owner_a)

    forbidden = viewer.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "input_refs": [f"raw/{ids.project_a}/1"]},
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
            "input_refs": [f"raw/{ids.project_a}/1"],
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


class _AnyObjectInspector:
    """API 测试用：任意输入引用都视为存在（避免真实 S3 依赖）。"""

    def object_size(self, object_key: str) -> int | None:
        return len(object_key.encode())


def _run_worker_once(api_db, *, inspector=None, now=None) -> dict[str, object]:
    from datetime import UTC, datetime

    from app.core.config import Settings
    from app.workers.processing import ProcessingRunWorker

    factory, _ids = api_db
    worker = ProcessingRunWorker(
        factory,
        Settings(validation_max_attempts=2),  # type: ignore[call-arg]
        inspector=inspector or _AnyObjectInspector(),
    )
    return worker.process_once(now=now or datetime.now(UTC))


def test_steps_are_inlined_in_detail_and_paged_by_endpoint(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    run = _create(editor, ids.project_a)

    # 未执行前：详情是空数组（有结论：还没有步骤），列表一律 null（不内联）。
    empty = viewer.get(url(ids.project_a, f"/processing-runs/{run['run_id']}"))
    assert empty.status_code == 200
    assert empty.json()["steps"] == []

    result = _run_worker_once(api_db)
    assert result["status"] == "PUBLISHED"

    detail = viewer.get(url(ids.project_a, f"/processing-runs/{run['run_id']}"))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "SUCCEEDED"
    assert len(body["steps"]) == 8
    assert body["steps"][0]["stage"] == "FILE_VALIDATION"
    assert body["steps"][0]["status"] == "SUCCEEDED"
    assert body["steps"][1]["status"] == "SKIPPED"
    assert body["steps"][1]["log_summary"]

    listed = editor.get(url(ids.project_a, "/processing-runs"))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["steps"] is None

    first_page = viewer.get(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/steps"),
        params={"limit": 3},
    )
    assert first_page.status_code == 200, first_page.text
    page_body = first_page.json()
    assert len(page_body["items"]) == 3
    assert page_body["next_cursor"]

    rest = viewer.get(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/steps"),
        params={"limit": 100, "cursor": page_body["next_cursor"]},
    )
    assert rest.status_code == 200
    assert len(rest.json()["items"]) == 5


def test_steps_endpoint_enforces_project_isolation_and_unknown_run(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    outsider = make_client(factory, ids.outsider)
    run = _create(editor, ids.project_a)

    hidden = outsider.get(url(ids.project_a, f"/processing-runs/{run['run_id']}/steps"))
    assert hidden.status_code in {403, 404}

    cross = owner.get(url(ids.project_b, f"/processing-runs/{run['run_id']}/steps"))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    missing = editor.get(url(ids.project_a, f"/processing-runs/{uuid4()}/steps"))
    assert missing.status_code == 404

    invalid = editor.get(
        url(ids.project_a, f"/processing-runs/{run['run_id']}/steps"),
        params={"cursor": "not-a-cursor"},
    )
    assert invalid.status_code == 422


class _MissingObjectInspector:
    def object_size(self, object_key: str) -> int | None:
        return None


def test_dead_letter_closes_failed_run_and_is_visible_in_audit_timeline(api_db) -> None:
    """失败收口：错误保留在运行上，system-close 只出现在审计时间线（owner 可读）。"""
    from datetime import UTC, datetime, timedelta

    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    viewer = make_client(factory, ids.viewer_a)
    run = _create(editor, ids.project_a, input_refs=[f"raw/{ids.project_a}/missing"])

    first_moment = datetime.now(UTC)
    first = _run_worker_once(
        api_db,
        inspector=_MissingObjectInspector(),
        now=first_moment,
    )
    assert first["status"] == "RETRIED", first
    second = _run_worker_once(
        api_db,
        inspector=_MissingObjectInspector(),
        now=first_moment + timedelta(seconds=5),
    )
    assert second["status"] == "DEAD_LETTER", second

    detail = editor.get(url(ids.project_a, f"/processing-runs/{run['run_id']}"))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "object_missing"
    assert body["error_summary"]
    assert body["closed_at"] is not None
    # FAILED 本来就没有客户端动作；收口后仍是空集合，客户端无法伪造 system-close。
    assert body["allowed_actions"] == []

    timeline = owner.get(
        url(ids.project_a, f"/audit-logs/PROCESSING_RUN/{run['run_id']}"),
    )
    assert timeline.status_code == 200, timeline.text
    actions = {item["action"] for item in timeline.json()["items"]}
    assert {"create", "worker-fail", "system-close"} <= actions

    close_row = next(item for item in timeline.json()["items"] if item["action"] == "system-close")
    assert (close_row["from_status"], close_row["to_status"]) == ("FAILED", "FAILED")
    assert close_row["reason"]

    forbidden = viewer.get(
        url(ids.project_a, f"/audit-logs/PROCESSING_RUN/{run['run_id']}"),
    )
    assert forbidden.status_code == 403
