"""设备 API：真实调用、权限、项目隔离、乐观锁与错误码。"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, device_payload, make_client


def _device_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/devices{suffix}"


def test_editor_can_create_list_get_and_patch_device(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = client.post(_device_url(ids.project_a), json=device_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["project_id"] == str(ids.project_a)
    assert body["status"] == "PROVISIONED"
    assert body["version"] == 1
    device_id = body["device_id"]

    listed = client.get(_device_url(ids.project_a))
    assert listed.status_code == 200
    assert [item["device_id"] for item in listed.json()["items"]] == [device_id]
    assert listed.json()["next_cursor"] is None

    detail = client.get(_device_url(ids.project_a, f"/{device_id}"))
    assert detail.status_code == 200
    assert detail.json()["code"] == "device-1"

    patched = client.patch(
        _device_url(ids.project_a, f"/{device_id}"),
        headers={"If-Match": '"1"'},
        json={"name": "新名称"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "新名称"
    assert patched.json()["version"] == 2


def test_viewer_can_read_but_cannot_write(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = editor.post(_device_url(ids.project_a), json=device_payload())
    assert created.status_code == 201

    viewer = make_client(factory, ids.viewer_a)
    assert viewer.get(_device_url(ids.project_a)).status_code == 200
    denied = viewer.post(_device_url(ids.project_a), json=device_payload("device-2"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_outsider_cannot_access_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.outsider)

    denied = client.get(_device_url(ids.project_a))
    missing = client.get(_device_url(uuid4()))

    assert denied.status_code == 403
    assert missing.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_cross_project_device_id_is_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner_b = make_client(factory, ids.owner_a)
    foreign = owner_b.post(_device_url(ids.project_b), json=device_payload("b-device"))
    assert foreign.status_code == 201
    foreign_id = foreign.json()["device_id"]

    editor_a = make_client(factory, ids.editor_a)
    response = editor_a.get(_device_url(ids.project_a, f"/{foreign_id}"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_duplicate_code_maps_to_duplicate_resource(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    assert client.post(_device_url(ids.project_a), json=device_payload()).status_code == 201
    duplicate = client.post(_device_url(ids.project_a), json=device_payload())

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"


def test_if_match_is_required_and_stale_version_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = client.post(_device_url(ids.project_a), json=device_payload()).json()["device_id"]

    missing = client.patch(
        _device_url(ids.project_a, f"/{device_id}"),
        json={"name": "无前置条件"},
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    stale = client.patch(
        _device_url(ids.project_a, f"/{device_id}"),
        headers={"If-Match": '"99"'},
        json={"name": "陈旧版本"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_client_cannot_patch_status_or_submit_invalid_enum(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = client.post(_device_url(ids.project_a), json=device_payload())
    device_id = created.json()["device_id"]

    status_patch = client.patch(
        _device_url(ids.project_a, f"/{device_id}"),
        headers={"If-Match": '"1"'},
        json={"status": "ACTIVE"},
    )
    invalid_type = client.post(
        _device_url(ids.project_a),
        json={**device_payload("device-x"), "device_type": "ROBOT"},
    )

    assert status_patch.status_code == 422
    assert status_patch.json()["error"]["code"] == "validation_failed"
    assert invalid_type.status_code == 422
    assert invalid_type.json()["error"]["code"] == "validation_failed"


def test_invalid_lifecycle_transition_maps_to_stable_code(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = client.post(_device_url(ids.project_a), json=device_payload()).json()["device_id"]

    response = client.post(
        _device_url(ids.project_a, f"/{device_id}/fault"),
        headers={"If-Match": '"1"'},
        json={"fault_code": "DEVICE_OFFLINE"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "state_transition_invalid"


def test_cursor_pagination_returns_next_page(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    for index in range(3):
        response = client.post(
            _device_url(ids.project_a),
            json=device_payload(f"device-{index}"),
        )
        assert response.status_code == 201

    first = client.get(_device_url(ids.project_a), params={"limit": 2})
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    cursor = first.json()["next_cursor"]
    assert cursor

    second = client.get(
        _device_url(ids.project_a),
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["next_cursor"] is None


def test_invalid_cursor_maps_to_validation_failed(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client: TestClient = make_client(factory, ids.editor_a)

    response = client.get(_device_url(ids.project_a), params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# --- G-10 / G-11 / G-17：动作语义持久化、审计与 outbox、幂等 -----------------


def _activate(client, project_id, device):
    return client.post(
        _device_url(project_id, f"/{device['device_id']}/activate"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={},
    )


def test_action_reason_and_fault_fields_are_persisted(api_db) -> None:
    """G-10：动作请求体不再被丢弃，reason/fault 必须落库。"""
    from app.infrastructure.db.models.devices import Device as DeviceModel
    from sqlalchemy import select

    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = client.post(_device_url(ids.project_a), json=device_payload()).json()
    device = _activate(client, ids.project_a, device).json()

    faulted = client.post(
        _device_url(ids.project_a, f"/{device['device_id']}/fault"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={
            "reason": "视觉模块无输出",
            "fault_code": "CAM_TIMEOUT",
            "fault_detail": "连续 30 秒未收到帧",
            "occurred_at": "2026-11-14T10:00:00Z",
        },
    )
    assert faulted.status_code == 200, faulted.text

    with factory() as session:
        row = session.scalar(select(DeviceModel).where(DeviceModel.id == UUID(device["device_id"])))
        assert row.status == "FAULT"
        assert row.status_reason == "视觉模块无输出"
        assert row.fault_code == "CAM_TIMEOUT"
        assert row.fault_detail == "连续 30 秒未收到帧"
        assert row.fault_occurred_at is not None
        assert row.status_changed_by == ids.editor_a
        assert row.status_changed_at is not None


def test_report_fault_requires_fault_code(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = client.post(_device_url(ids.project_a), json=device_payload()).json()
    device = _activate(client, ids.project_a, device).json()

    response = client.post(
        _device_url(ids.project_a, f"/{device['device_id']}/fault"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={"reason": "无故障码"},
    )

    assert response.status_code == 422


def test_device_actions_write_audit_and_outbox(api_db) -> None:
    """G-11：设备域此前零审计、零 outbox。"""
    from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
    from sqlalchemy import select

    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = client.post(_device_url(ids.project_a), json=device_payload()).json()
    assert _activate(client, ids.project_a, device).status_code == 200

    with factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == ids.project_a,
                    AuditLog.resource_type == "device",
                    AuditLog.resource_id == UUID(device["device_id"]),
                )
            )
        )
        events = list(
            session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.project_id == ids.project_a,
                    OutboxEvent.aggregate_type == "device",
                    OutboxEvent.aggregate_id == UUID(device["device_id"]),
                )
            )
        )

    actions = {row.action for row in audits}
    assert {"create", "activate"} <= actions
    activate_audit = next(row for row in audits if row.action == "activate")
    assert activate_audit.from_status == "PROVISIONED"
    assert activate_audit.to_status == "ACTIVE"
    assert activate_audit.actor_user_id == ids.editor_a
    assert {row.event_type for row in events} >= {"device.created", "device.activate"}


def test_create_device_replays_on_same_idempotency_key(api_db) -> None:
    """G-17：设备域写动作此前不支持 Idempotency-Key。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "device-idem-0001"}
    payload = device_payload()

    first = client.post(_device_url(ids.project_a), json=payload, headers=headers)
    second = client.post(_device_url(ids.project_a), json=payload, headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["device_id"] == first.json()["device_id"]


def test_device_action_replays_without_second_transition(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = client.post(_device_url(ids.project_a), json=device_payload()).json()
    headers = {"If-Match": '"1"', "Idempotency-Key": "device-idem-0002"}

    first = client.post(
        _device_url(ids.project_a, f"/{device['device_id']}/activate"),
        headers=headers,
        json={},
    )
    second = client.post(
        _device_url(ids.project_a, f"/{device['device_id']}/activate"),
        headers=headers,
        json={},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["version"] == first.json()["version"]


def test_device_idempotency_key_reuse_with_different_body_is_rejected(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "device-idem-0003"}

    first = client.post(
        _device_url(ids.project_a), json=device_payload(code="dev-a"), headers=headers
    )
    assert first.status_code == 201, first.text
    conflict = client.post(
        _device_url(ids.project_a), json=device_payload(code="dev-b"), headers=headers
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"
