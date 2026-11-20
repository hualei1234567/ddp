"""DROID 公开硬件蓝图经既有 Robot/Device/Sensor API 的真实闭环。"""

from __future__ import annotations

import json
from pathlib import Path

from ddp_data.droid import build_droid_hardware_blueprint
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

_FIXTURE = Path(__file__).parents[1] / "fixtures/public/droid-hardware/facts.json"


def test_droid_hardware_blueprint_persists_and_reads_back(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    blueprint = build_droid_hardware_blueprint(json.loads(_FIXTURE.read_text(encoding="utf-8")))

    robot_response = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots",
        json=blueprint["robot"],
        headers={"Idempotency-Key": "droid-public-robot-v1"},
    )
    assert robot_response.status_code == 201, robot_response.text
    robot = robot_response.json()
    assert robot["model"] == "Franka Emika Panda / Franka Research 3"
    assert robot["serial_number"] is None
    assert robot["capability_profile"]["num_dofs"] == 7

    created_devices: dict[str, dict[str, object]] = {}
    for raw in blueprint["devices"]:
        payload = {**raw, "robot_id": robot["robot_id"]}
        response = editor.post(
            f"/api/v1/projects/{ids.project_a}/devices",
            json=payload,
            headers={"Idempotency-Key": f"droid-public-{raw['code']}-v1"},
        )
        assert response.status_code == 201, response.text
        created_devices[str(raw["code"])] = response.json()

    camera_rig = created_devices["droid-camera-rig"]
    assert camera_rig["model"] == "ZED 2 + ZED Mini"
    assert camera_rig["serial_number"] is None

    sensor_ids: list[str] = []
    for raw in blueprint["sensors"]:
        payload = {
            **raw,
            "robot_id": robot["robot_id"],
            "device_id": camera_rig["device_id"],
        }
        response = editor.post(
            f"/api/v1/projects/{ids.project_a}/sensors",
            json=payload,
            headers={"Idempotency-Key": f"droid-public-{raw['code']}-v1"},
        )
        assert response.status_code == 201, response.text
        sensor_ids.append(response.json()["sensor_id"])

    listed = editor.get(
        f"/api/v1/projects/{ids.project_a}/sensors",
        params={"device_id": camera_rig["device_id"]},
    )
    assert listed.status_code == 200
    sensors = listed.json()["items"]
    assert {item["sensor_id"] for item in sensors} == set(sensor_ids)
    assert {item["model"] for item in sensors} == {"ZED 2", "ZED Mini"}
    assert {item["sample_rate_hz"] for item in sensors} == {15.0}
    assert all(item["sync_mode"] == "NONE" for item in sensors)
    assert all(item["serial_number"] is None for item in sensors)

    outsider = make_client(factory, ids.outsider)
    assert outsider.get(f"/api/v1/projects/{ids.project_a}/robots").status_code == 403
    assert outsider.get(f"/api/v1/projects/{ids.project_a}/devices").status_code == 403
    assert outsider.get(f"/api/v1/projects/{ids.project_a}/sensors").status_code == 403
