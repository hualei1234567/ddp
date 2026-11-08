"""采集任务/批次 API：权限、项目隔离、If-Match、幂等与状态动作。"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _tasks_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/collection-tasks{suffix}"


def _batches_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/collection-batches{suffix}"


def task_payload(code: str = "task-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"采集任务 {code}",
        "priority": "P1",
        "target_episode_count": 10,
        "target_duration_sec": 600,
    }
    payload.update(overrides)
    return payload


def _create_task(
    client: TestClient,
    project_id: object,
    code: str = "task-1",
    **overrides: object,
) -> dict[str, object]:
    response = client.post(_tasks_url(project_id), json=task_payload(code, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_editor_can_create_list_and_get_task(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = _create_task(client, ids.project_a)
    assert created["project_id"] == str(ids.project_a)
    assert created["status"] == "DRAFT"
    assert created["version"] == 1
    assert created["assigned_device_ids"] == []
    assert created["assigned_sensor_ids"] == []
    assert created["created_at"] is not None

    listed = client.get(_tasks_url(ids.project_a))
    assert listed.status_code == 200
    assert [item["task_id"] for item in listed.json()["items"]] == [created["task_id"]]
    assert listed.json()["next_cursor"] is None

    detail = client.get(_tasks_url(ids.project_a, f"/{created['task_id']}"))
    assert detail.status_code == 200
    assert detail.json()["code"] == "task-1"


def test_viewer_reads_but_cannot_write_and_outsider_is_denied(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    task = _create_task(editor, ids.project_a)

    viewer = make_client(factory, ids.viewer_a)
    assert viewer.get(_tasks_url(ids.project_a)).status_code == 200
    assert viewer.get(_tasks_url(ids.project_a, f"/{task['task_id']}")).status_code == 200
    denied = viewer.post(_tasks_url(ids.project_a), json=task_payload("task-2"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    batch_denied = viewer.post(
        _tasks_url(ids.project_a, f"/{task['task_id']}/batches"),
        json={"expected_count": 1},
    )
    assert batch_denied.status_code == 403

    outsider = make_client(factory, ids.outsider)
    assert outsider.get(_tasks_url(ids.project_a)).status_code == 403
    assert outsider.get(_tasks_url(ids.project_a, f"/{task['task_id']}")).status_code == 403


def test_cross_project_task_and_batch_ids_are_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign_task = _create_task(owner, ids.project_b, "b-task")
    foreign_batch = owner.post(
        _tasks_url(ids.project_b, f"/{foreign_task['task_id']}/batches"),
        json={"expected_count": 1},
    )
    assert foreign_batch.status_code == 201, foreign_batch.text

    editor_a = make_client(factory, ids.editor_a)
    task_response = editor_a.get(_tasks_url(ids.project_a, f"/{foreign_task['task_id']}"))
    batch_response = editor_a.get(
        _batches_url(ids.project_a, f"/{foreign_batch.json()['batch_id']}")
    )
    assert task_response.status_code == 404
    assert task_response.json()["error"]["code"] == "resource_not_found"
    assert batch_response.status_code == 404
    assert batch_response.json()["error"]["code"] == "resource_not_found"


def test_duplicate_code_patch_if_match_and_immutability(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    task = _create_task(client, ids.project_a)

    duplicate = client.post(_tasks_url(ids.project_a), json=task_payload())
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"

    missing = client.patch(
        _tasks_url(ids.project_a, f"/{task['task_id']}"),
        json={"name": "无前置条件"},
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    patched = client.patch(
        _tasks_url(ids.project_a, f"/{task['task_id']}"),
        headers={"If-Match": '"1"'},
        json={"name": "新名称"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "新名称"
    assert patched.json()["version"] == 2

    stale = client.patch(
        _tasks_url(ids.project_a, f"/{task['task_id']}"),
        headers={"If-Match": '"1"'},
        json={"name": "陈旧版本"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    status_patch = client.patch(
        _tasks_url(ids.project_a, f"/{task['task_id']}"),
        headers={"If-Match": '"2"'},
        json={"status": "APPROVED"},
    )
    assert status_patch.status_code == 422
    assert status_patch.json()["error"]["code"] == "validation_failed"

    non_draft = _create_task(client, ids.project_a, "task-non-draft")
    submitted = client.post(
        _tasks_url(ids.project_a, f"/{non_draft['task_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    blocked_patch = client.patch(
        _tasks_url(ids.project_a, f"/{non_draft['task_id']}"),
        headers={"If-Match": '"2"'},
        json={"name": "不可修改"},
    )
    assert blocked_patch.status_code == 409
    assert blocked_patch.json()["error"]["code"] == "resource_immutable"


def test_task_state_machine_and_self_approval(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    task = _create_task(editor, ids.project_a)
    url = _tasks_url(ids.project_a, f"/{task['task_id']}/actions")

    submitted = editor.post(f"{url}/submit-review", headers={"If-Match": '"1"'}, json={})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "PENDING_REVIEW"

    self_approval = editor.post(f"{url}/approve", headers={"If-Match": '"2"'}, json={})
    assert self_approval.status_code == 403
    assert self_approval.json()["error"]["code"] == "forbidden"

    approved = owner.post(f"{url}/approve", headers={"If-Match": '"2"'}, json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    ready = editor.post(f"{url}/ready", headers={"If-Match": '"3"'}, json={})
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "READY"

    illegal = editor.post(f"{url}/approve", headers={"If-Match": '"4"'}, json={})
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "state_transition_invalid"

    unknown = editor.post(f"{url}/explode", headers={"If-Match": '"4"'}, json={})
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_failed"


def test_create_task_supports_idempotency_key(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "idem-key-0001"}
    payload = task_payload()

    first = client.post(_tasks_url(ids.project_a), json=payload, headers=headers)
    second = client.post(_tasks_url(ids.project_a), json=payload, headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["task_id"] == second.json()["task_id"]

    listed = client.get(_tasks_url(ids.project_a))
    assert len(listed.json()["items"]) == 1

    reused = client.post(
        _tasks_url(ids.project_a),
        json=task_payload("task-2"),
        headers=headers,
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    too_short = client.post(
        _tasks_url(ids.project_a),
        json=task_payload("task-3"),
        headers={"Idempotency-Key": "short"},
    )
    assert too_short.status_code == 422


def test_batch_flow_and_task_compatibility(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    task = _create_task(editor, ids.project_a)
    task_id = task["task_id"]
    batch = editor.post(
        _tasks_url(ids.project_a, f"/{task_id}/batches"),
        json={"expected_count": 3},
    )
    assert batch.status_code == 201, batch.text
    body = batch.json()
    assert body["status"] == "PLANNED"
    assert body["sequence_no"] == 1
    assert body["code"] == "B-0001"

    listed = editor.get(_tasks_url(ids.project_a, f"/{task_id}/batches"))
    assert [item["batch_id"] for item in listed.json()["items"]] == [body["batch_id"]]

    early_ready = editor.post(
        _batches_url(ids.project_a, f"/{body['batch_id']}/actions/ready"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert early_ready.status_code == 409
    assert early_ready.json()["error"]["code"] == "state_transition_invalid"

    task_url = _tasks_url(ids.project_a, f"/{task_id}/actions")
    editor.post(f"{task_url}/submit-review", headers={"If-Match": '"1"'}, json={})
    owner.post(f"{task_url}/approve", headers={"If-Match": '"2"'}, json={})
    editor.post(f"{task_url}/ready", headers={"If-Match": '"3"'}, json={})
    assigned = editor.post(
        f"{task_url}/assign",
        headers={"If-Match": '"4"'},
        json={"assigned_to": str(ids.editor_a)},
    )
    assert assigned.status_code == 200, assigned.text
    started = editor.post(f"{task_url}/start", headers={"If-Match": '"5"'}, json={})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "IN_PROGRESS"

    ready = editor.post(
        _batches_url(ids.project_a, f"/{body['batch_id']}/actions/ready"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert ready.status_code == 200, ready.text
    batch_started = editor.post(
        _batches_url(ids.project_a, f"/{body['batch_id']}/actions/start"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert batch_started.status_code == 200, batch_started.text
    assert batch_started.json()["status"] == "IN_PROGRESS"

    missing_reason = editor.post(
        _batches_url(ids.project_a, f"/{body['batch_id']}/actions/pause"),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert missing_reason.status_code == 422
    paused = editor.post(
        _batches_url(ids.project_a, f"/{body['batch_id']}/actions/pause"),
        headers={"If-Match": '"3"'},
        json={"reason": "设备维护"},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"


def test_task_cancel_cascades_non_terminal_batches(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    task = _create_task(client, ids.project_a)
    batch = client.post(
        _tasks_url(ids.project_a, f"/{task['task_id']}/batches"),
        json={"expected_count": 1},
    )
    assert batch.status_code == 201

    cancelled = client.post(
        _tasks_url(ids.project_a, f"/{task['task_id']}/actions/cancel"),
        headers={"If-Match": '"1"'},
        json={"reason": "需求取消"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["actual_end"] is not None

    batch_detail = client.get(_batches_url(ids.project_a, f"/{batch.json()['batch_id']}"))
    assert batch_detail.json()["status"] == "CANCELLED"


def test_audit_and_outbox_are_written_with_status_actions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    task = _create_task(client, ids.project_a)
    submitted = client.post(
        _tasks_url(ids.project_a, f"/{task['task_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text

    with factory() as session:
        audits = session.scalars(
            select(AuditLog).where(AuditLog.resource_id == UUID(str(task["task_id"])))
        ).all()
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(str(task["task_id"])))
        ).all()

    assert sorted(item.action for item in audits) == ["create", "submit-review"]
    assert all(item.resource_type == "COLLECTION_TASK" for item in audits)
    assert all(item.request_id for item in audits)
    ordered_events = sorted(events, key=lambda item: item.event_type)
    assert [item.event_type for item in ordered_events] == [
        "collection.task.created",
        "collection.task.status_changed",
    ]
    assert ordered_events[0].payload["status"] == "DRAFT"


def test_batch_create_requires_existing_task_in_same_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    missing = client.post(
        _tasks_url(ids.project_a, f"/{uuid4()}/batches"),
        json={"expected_count": 1},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "resource_not_found"
