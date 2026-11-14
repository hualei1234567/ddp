"""传感器 API：真实调用、设备过滤、权限与错误码。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import (
    ApiIds,
    device_payload,
    make_client,
    sensor_payload,
)


def _sensor_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/sensors{suffix}"


def _create_device(client: TestClient, project_id: object, code: str = "device-1") -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/devices",
        json=device_payload(code),
    )
    assert response.status_code == 201, response.text
    return response.json()["device_id"]


def test_editor_can_create_and_list_sensors_by_device(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = _create_device(client, ids.project_a)

    created = client.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=device_id),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["device_id"] == device_id
    assert body["status"] == "PROVISIONED"
    assert body["channels"] is None

    listed = client.get(
        _sensor_url(ids.project_a),
        params={"device_id": device_id},
    )
    assert listed.status_code == 200
    assert [item["sensor_id"] for item in listed.json()["items"]] == [body["sensor_id"]]


def test_sensor_requires_at_least_one_mount(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    payload = {
        "code": "floating-sensor",
        "sensor_type": "IMU",
        "sample_rate_hz": 100.0,
        "coordinate_frame": "imu_link",
    }

    response = client.post(_sensor_url(ids.project_a), json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_sensor_rejects_cross_project_device_reference(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner_b = make_client(factory, ids.owner_a)
    foreign_device = _create_device(owner_b, ids.project_b, "b-device")
    editor_a = make_client(factory, ids.editor_a)

    response = editor_a.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=foreign_device),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_sensor_duplicate_code_and_stale_patch(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = _create_device(client, ids.project_a)
    created = client.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=device_id),
    )
    assert created.status_code == 201
    sensor_id = created.json()["sensor_id"]

    duplicate = client.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=device_id),
    )
    stale = client.patch(
        _sensor_url(ids.project_a, f"/{sensor_id}"),
        headers={"If-Match": '"7"'},
        json={"mount_position": "top"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_viewer_can_read_sensor_but_not_write(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    device_id = _create_device(editor, ids.project_a)
    assert (
        editor.post(
            _sensor_url(ids.project_a),
            json=sensor_payload(device_id=device_id),
        ).status_code
        == 201
    )

    viewer = make_client(factory, ids.viewer_a)
    assert viewer.get(_sensor_url(ids.project_a)).status_code == 200
    denied = viewer.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(code="sensor-2", device_id=device_id),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_invalid_sensor_enum_maps_to_validation_failed(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = _create_device(client, ids.project_a)

    response = client.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=device_id, sensor_type="RGB-D"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# --- G-10 / G-11：传感器动作语义落库与审计 ---------------------------------


def test_sensor_fault_fields_are_persisted_and_audited(api_db) -> None:
    from uuid import UUID

    from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
    from app.infrastructure.db.models.devices import Sensor as SensorModel
    from sqlalchemy import select

    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = _create_device(client, ids.project_a)
    sensor = client.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=device_id),
    ).json()

    activated = client.post(
        _sensor_url(ids.project_a, f"/{sensor['sensor_id']}/activate"),
        headers={"If-Match": '"1"'},
        json={"reason": "上线自检通过"},
    )
    assert activated.status_code == 200, activated.text

    faulted = client.post(
        _sensor_url(ids.project_a, f"/{sensor['sensor_id']}/fault"),
        headers={"If-Match": '"2"'},
        json={
            "reason": "镜头遮挡",
            "fault_code": "LENS_BLOCKED",
            "fault_detail": "画面 90% 为黑色",
        },
    )
    assert faulted.status_code == 200, faulted.text

    with factory() as session:
        row = session.scalar(select(SensorModel).where(SensorModel.id == UUID(sensor["sensor_id"])))
        assert row.status == "FAULT"
        assert row.fault_code == "LENS_BLOCKED"
        assert row.status_reason == "镜头遮挡"
        assert row.status_changed_by == ids.editor_a

        actions = {
            item.action
            for item in session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == ids.project_a,
                    AuditLog.resource_type == "sensor",
                    AuditLog.resource_id == UUID(sensor["sensor_id"]),
                )
            )
        }
        events = {
            item.event_type
            for item in session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.project_id == ids.project_a,
                    OutboxEvent.aggregate_type == "sensor",
                    OutboxEvent.aggregate_id == UUID(sensor["sensor_id"]),
                )
            )
        }

    assert {"create", "activate", "report-fault"} <= actions
    assert {"sensor.created", "sensor.activate", "sensor.report-fault"} <= events


def test_sensor_report_fault_requires_fault_code(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device_id = _create_device(client, ids.project_a)
    sensor = client.post(
        _sensor_url(ids.project_a), json=sensor_payload(device_id=device_id)
    ).json()
    client.post(
        _sensor_url(ids.project_a, f"/{sensor['sensor_id']}/activate"),
        headers={"If-Match": '"1"'},
        json={},
    )

    response = client.post(
        _sensor_url(ids.project_a, f"/{sensor['sensor_id']}/fault"),
        headers={"If-Match": '"2"'},
        json={"reason": "无故障码"},
    )

    assert response.status_code == 422
