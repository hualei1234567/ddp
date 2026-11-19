"""采集会话 API 集成测试（N9-001）。

真实 SQLite + 真实仓储 + 真实路由，不 mock。覆盖：
- 创建/列表/详情与审计、outbox（留痕类）；
- 状态动作与 allowed_actions；
- If-Match 版本冲突、非法迁移、缺少原因；
- 跨项目 404、越权 403；
- 任务/批次/成员/机器人/场地的同项目校验。
"""

from __future__ import annotations

from app.infrastructure.db.models.capture_session import CaptureSessionModel
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/capture-sessions{suffix}"


def _create_task(
    client: TestClient,
    project_id: object,
    code: str = "task-1",
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/projects/{project_id}/collection-tasks",
        json={
            "code": code,
            "name": f"采集任务 {code}",
            "priority": "P1",
            "target_episode_count": 10,
            "target_duration_sec": 600,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_batch(
    client: TestClient,
    project_id: object,
    task_id: object,
    *,
    expected_count: int = 1,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/projects/{project_id}/collection-tasks/{task_id}/batches",
        json={"expected_count": expected_count},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_session(
    client: TestClient,
    project_id: object,
    code: str = "session-1",
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"code": code}
    payload.update(overrides)
    response = client.post(_url(project_id), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _act(
    client: TestClient,
    project_id: object,
    session_id: object,
    action: str,
    *,
    version: int,
    reason: str | None = None,
):
    body: dict[str, object] = {}
    if reason is not None:
        body["reason"] = reason
    return client.post(
        _url(project_id, f"/{session_id}/actions/{action}"),
        json=body,
        headers={"If-Match": f'"{version}"'},
    )


def test_create_list_get_and_audit_outbox(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    task = _create_task(client, ids.project_a)

    created = _create_session(
        client,
        ids.project_a,
        task_id=task["task_id"],
        operator_id=str(ids.editor_a),
    )
    assert created["project_id"] == str(ids.project_a)
    assert created["status"] == "SCHEDULED"
    assert created["task_id"] == task["task_id"]
    assert created["operator_id"] == str(ids.editor_a)
    assert created["started_at"] is None
    assert created["ended_at"] is None
    assert created["version"] == 1
    assert created["allowed_actions"] == ["abort", "start"]

    listed = client.get(_url(ids.project_a))
    assert listed.status_code == 200
    assert [item["session_id"] for item in listed.json()["items"]] == [created["session_id"]]
    assert listed.json()["next_cursor"] is None

    detail = client.get(_url(ids.project_a, f"/{created['session_id']}"))
    assert detail.status_code == 200
    assert detail.json() == created

    with factory() as session:
        audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_type == "CAPTURE_SESSION",
            )
        )
        assert audits == 1
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.project_id == ids.project_a,
                OutboxEvent.aggregate_type == "CAPTURE_SESSION",
            )
        ).all()
        assert [event.event_type for event in events] == [
            "collection.capture_session.created"
        ]
        assert all(event.dispatch is False for event in events)


def test_state_actions_and_allowed_actions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create_session(client, ids.project_a)
    version = created["version"]

    started = _act(client, ids.project_a, created["session_id"], "start", version=version)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "RUNNING"
    assert started.json()["started_at"] is not None
    assert started.json()["allowed_actions"] == ["abort", "complete", "pause"]
    version = started.json()["version"]

    paused = _act(
        client,
        ids.project_a,
        created["session_id"],
        "pause",
        version=version,
        reason="更换电池",
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"
    assert paused.json()["status_reason"] == "更换电池"
    assert paused.json()["allowed_actions"] == ["abort", "complete", "resume"]
    version = paused.json()["version"]

    resumed = _act(
        client,
        ids.project_a,
        created["session_id"],
        "resume",
        version=version,
        reason="电池更换完成",
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "RUNNING"
    version = resumed.json()["version"]

    completed = _act(
        client,
        ids.project_a,
        created["session_id"],
        "complete",
        version=version,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["ended_at"] is not None
    assert completed.json()["allowed_actions"] == []

    terminal = _act(
        client,
        ids.project_a,
        created["session_id"],
        "start",
        version=completed.json()["version"],
    )
    assert terminal.status_code == 409
    assert terminal.json()["error"]["code"] == "state_transition_invalid"


def test_action_requires_reason_and_matching_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create_session(client, ids.project_a)
    started = _act(client, ids.project_a, created["session_id"], "start", version=1)
    assert started.status_code == 200
    version = started.json()["version"]

    missing_reason = _act(
        client,
        ids.project_a,
        created["session_id"],
        "pause",
        version=version,
    )
    assert missing_reason.status_code == 422
    assert missing_reason.json()["error"]["code"] == "validation_failed"

    stale = _act(
        client,
        ids.project_a,
        created["session_id"],
        "pause",
        version=version + 1,
        reason="过期版本",
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_cross_project_session_is_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign = _create_session(owner, ids.project_b, code="session-b")

    editor = make_client(factory, ids.editor_a)
    detail = editor.get(_url(ids.project_a, f"/{foreign['session_id']}"))
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "resource_not_found"

    action = _act(editor, ids.project_a, foreign["session_id"], "start", version=1)
    assert action.status_code == 404

    # 列表也不能出现别的项目的会话。
    listed = editor.get(_url(ids.project_a))
    assert listed.status_code == 200
    assert listed.json()["items"] == []

    # 用别的项目的任务创建会话 → 404。
    foreign_task = _create_task(owner, ids.project_b, "task-b")
    denied = editor.post(
        _url(ids.project_a),
        json={"code": "session-x", "task_id": foreign_task["task_id"]},
    )
    assert denied.status_code == 404


def test_viewer_cannot_write_and_outsider_cannot_read(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create_session(editor, ids.project_a)

    viewer = make_client(factory, ids.viewer_a)
    assert viewer.get(_url(ids.project_a)).status_code == 200
    denied = viewer.post(_url(ids.project_a), json={"code": "session-2"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    outsider = make_client(factory, ids.outsider)
    assert outsider.get(_url(ids.project_a)).status_code == 403
    assert outsider.get(_url(ids.project_a, f"/{created['session_id']}")).status_code == 403


def test_relation_validation_task_batch_operator_robot_location(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    task = _create_task(client, ids.project_a)
    batch = _create_batch(client, ids.project_a, task["task_id"])

    # 只给批次：自动继承批次的任务。
    from_batch = _create_session(
        client,
        ids.project_a,
        code="session-from-batch",
        batch_id=batch["batch_id"],
    )
    assert from_batch["task_id"] == task["task_id"]
    assert from_batch["batch_id"] == batch["batch_id"]

    # 任务与批次不一致 → 422。
    other_task = _create_task(client, ids.project_a, "task-2")
    mismatch = client.post(
        _url(ids.project_a),
        json={
            "code": "session-mismatch",
            "task_id": other_task["task_id"],
            "batch_id": batch["batch_id"],
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "validation_failed"

    # 非项目成员不能作为操作员。
    not_member = client.post(
        _url(ids.project_a),
        json={"code": "session-operator", "operator_id": str(ids.outsider)},
    )
    assert not_member.status_code == 404

    # 场地：同项目可绑定；跨项目 → 404。
    location = client.post(
        f"/api/v1/projects/{ids.project_a}/locations",
        json={"code": "loc-a", "name": "基地 A"},
    )
    assert location.status_code == 201, location.text
    with_location = _create_session(
        client,
        ids.project_a,
        code="session-location",
        location_id=location.json()["location_id"],
    )
    assert with_location["location_id"] == location.json()["location_id"]

    owner = make_client(factory, ids.owner_a)
    foreign_location = owner.post(
        f"/api/v1/projects/{ids.project_b}/locations",
        json={"code": "loc-b", "name": "基地 B"},
    )
    assert foreign_location.status_code == 201, foreign_location.text
    denied_location = client.post(
        _url(ids.project_a),
        json={"code": "session-loc-x", "location_id": foreign_location.json()["location_id"]},
    )
    assert denied_location.status_code == 404

    # 机器人：新建为 PROVISIONED，不可用于采集；激活后可用。
    robot = client.post(
        f"/api/v1/projects/{ids.project_a}/robots",
        json={"code": "robot-1", "name": "机器人一", "robot_type": "HUMANOID"},
    )
    assert robot.status_code == 201, robot.text
    robot_id = robot.json()["robot_id"]
    not_active = client.post(
        _url(ids.project_a),
        json={"code": "session-robot-1", "robot_id": robot_id},
    )
    assert not_active.status_code == 422
    assert not_active.json()["error"]["code"] == "validation_failed"

    activated = client.post(
        f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/activate",
        headers={"If-Match": f'"{robot.json()["version"]}"'},
        json={},
    )
    assert activated.status_code == 200, activated.text
    with_robot = _create_session(
        client,
        ids.project_a,
        code="session-robot-2",
        robot_id=robot_id,
    )
    assert with_robot["robot_id"] == robot_id


def test_idempotency_key_replays_create(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "capture-session-create-0001"}

    first = client.post(_url(ids.project_a), json={"code": "session-idem"}, headers=headers)
    assert first.status_code == 201, first.text
    second = client.post(_url(ids.project_a), json={"code": "session-idem"}, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["session_id"] == first.json()["session_id"]

    with factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(CaptureSessionModel)
            .where(CaptureSessionModel.project_id == ids.project_a)
        )
        assert count == 1
