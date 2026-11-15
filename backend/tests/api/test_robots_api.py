"""Robot 与 CalibrationProfile 路由（G-01）：真实调用、隔离、动作与幂等。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _robot_payload(code: str = "robot-1") -> dict[str, object]:
    return {"code": code, "name": "主机器人", "robot_type": "HUMANOID"}


def _create_robot(
    client: TestClient, project_id: object, code: str = "robot-1"
) -> dict[str, object]:
    response = client.post(f"/api/v1/projects/{project_id}/robots", json=_robot_payload(code))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_and_get_robot(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    created = _create_robot(editor, ids.project_a)
    assert created["status"] == "PROVISIONED"
    assert created["version"] == 1
    assert created["robot_type"] == "HUMANOID"

    listed = editor.get(f"/api/v1/projects/{ids.project_a}/robots")
    assert listed.status_code == 200
    assert [item["robot_id"] for item in listed.json()["items"]] == [created["robot_id"]]

    detail = editor.get(f"/api/v1/projects/{ids.project_a}/robots/{created['robot_id']}")
    assert detail.status_code == 200
    assert detail.json()["code"] == "robot-1"


def test_robot_is_project_isolated(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    # outsider 不在任何项目内：用它验证"看不到别人项目的机器人"。
    outsider = make_client(factory, ids.outsider)
    created = _create_robot(editor, ids.project_a)

    cross = outsider.get(f"/api/v1/projects/{ids.project_a}/robots/{created['robot_id']}")
    assert cross.status_code == 403

    other_list = outsider.get(f"/api/v1/projects/{ids.project_a}/robots")
    assert other_list.status_code == 403


def test_create_robot_requires_idempotency_key(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    first = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots",
        json=_robot_payload("robot-1"),
        headers={"Idempotency-Key": "robot-key-1"},
    )
    assert first.status_code == 201, first.text

    # 同一幂等键重放：返回同一资源，不新建。
    replay = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots",
        json=_robot_payload("robot-1"),
        headers={"Idempotency-Key": "robot-key-1"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["robot_id"] == first.json()["robot_id"]

    listed = editor.get(f"/api/v1/projects/{ids.project_a}/robots")
    assert len(listed.json()["items"]) == 1


def test_robot_action_requires_if_match_and_records_reason(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create_robot(editor, ids.project_a)
    robot_id = created["robot_id"]

    missing = editor.post(f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/activate")
    assert missing.status_code == 428, missing.text

    activated = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/activate",
        headers={"If-Match": '"1"'},
        json={"reason": "到货验收通过"},
    )
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["status"] == "ACTIVE"
    assert body["version"] == 2
    # G-10/G-11：动作请求体的语义字段必须落库并可读回。
    assert body["status_reason"] == "到货验收通过"
    assert body["status_changed_by"] == str(ids.editor_a)

    # 旧版本号再动作：乐观锁拒绝。
    stale = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/suspend",
        headers={"If-Match": '"1"'},
        json={"reason": "重复请求"},
    )
    assert stale.status_code == 409


def test_report_fault_requires_fault_code(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create_robot(editor, ids.project_a)
    robot_id = created["robot_id"]

    editor.post(
        f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/activate",
        headers={"If-Match": '"1"'},
    )

    bad = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/fault",
        headers={"If-Match": '"2"'},
        json={"reason": "掉线"},
    )
    assert bad.status_code == 422, bad.text

    good = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots/{robot_id}/fault",
        headers={"If-Match": '"2"'},
        json={
            "reason": "关节过温",
            "fault_code": "JOINT_OVERHEAT",
            "fault_detail": "右肘关节 78℃",
            "occurred_at": "2026-11-15T01:00:00Z",
        },
    )
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["status"] == "FAULT"
    assert body["fault_code"] == "JOINT_OVERHEAT"
    assert body["fault_detail"] == "右肘关节 78℃"
    assert body["fault_occurred_at"].startswith("2026-11-15T01:00:00")


def test_patch_rejects_unknown_fields(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create_robot(editor, ids.project_a)

    bad = editor.patch(
        f"/api/v1/projects/{ids.project_a}/robots/{created['robot_id']}",
        headers={"If-Match": '"1"'},
        json={"status": "ACTIVE"},
    )
    assert bad.status_code == 422

    good = editor.patch(
        f"/api/v1/projects/{ids.project_a}/robots/{created['robot_id']}",
        headers={"If-Match": '"1"'},
        json={"name": "主机器人（改名）"},
    )
    assert good.status_code == 200, good.text
    assert good.json()["name"] == "主机器人（改名）"
    assert good.json()["version"] == 2


def test_profile_create_then_robot_references_it(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    profile = editor.post(
        f"/api/v1/projects/{ids.project_a}/calibration-profiles",
        json={
            "name": "机器人标定模板",
            "scope": "ROBOT",
            "target_type": "ROBOT",
            "schema_version": "ddp.device.calibration-parameters/1.0.0",
            "required_types": ["INTRINSICS", "EXTRINSICS"],
            "selection_rule": {},
        },
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["profile_id"]
    assert profile.json()["profile_version"] == 1

    robot = editor.post(
        f"/api/v1/projects/{ids.project_a}/robots",
        json={**_robot_payload("robot-2"), "calibration_profile_id": profile_id},
    )
    assert robot.status_code == 201, robot.text
    assert robot.json()["calibration_profile_id"] == profile_id


def test_profile_target_type_must_equal_scope(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    bad = editor.post(
        f"/api/v1/projects/{ids.project_a}/calibration-profiles",
        json={
            "name": "错配",
            "scope": "ROBOT",
            "target_type": "SENSOR",
            "schema_version": "ddp.device.calibration-parameters/1.0.0",
        },
    )
    assert bad.status_code == 422, bad.text


def test_profile_supersede_requires_continuous_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = {
        "name": "机器人标定模板",
        "scope": "ROBOT",
        "target_type": "ROBOT",
        "schema_version": "ddp.device.calibration-parameters/1.0.0",
    }
    first = editor.post(f"/api/v1/projects/{ids.project_a}/calibration-profiles", json=base)
    assert first.status_code == 201
    first_id = first.json()["profile_id"]

    skipped = editor.post(
        f"/api/v1/projects/{ids.project_a}/calibration-profiles",
        json={**base, "profile_version": 3, "supersedes_id": first_id},
    )
    assert skipped.status_code == 422, skipped.text

    second = editor.post(
        f"/api/v1/projects/{ids.project_a}/calibration-profiles",
        json={**base, "profile_version": 2, "supersedes_id": first_id},
    )
    assert second.status_code == 201, second.text
    assert second.json()["profile_version"] == 2


def test_profile_has_no_update_route(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """Profile 不可变：改配置只能新建版本，因此不应存在 PATCH 路由。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    profile = editor.post(
        f"/api/v1/projects/{ids.project_a}/calibration-profiles",
        json={
            "name": "模板",
            "scope": "ROBOT",
            "target_type": "ROBOT",
            "schema_version": "ddp.device.calibration-parameters/1.0.0",
        },
    ).json()

    patched = editor.patch(
        f"/api/v1/projects/{ids.project_a}/calibration-profiles/{profile['profile_id']}",
        json={"name": "改名"},
    )
    assert patched.status_code == 405


def test_patch_capability_profile_null_means_leave_unchanged(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约允许 ``capability_profile: null``，语义是「本次不改该字段」。

    此前实现把 None 直接传给领域层，``frozen_mapping`` 只认 Mapping 因而 422——
    契约允许的值被实现拒绝。收紧契约（去掉 null）不是正确修法：那会让原本
    合法的 null 变成 422，对客户端是破坏性变更（CI 的 oasdiff 会拦
    request-property-became-not-nullable）。

    正确语义是剔除该键：既不报错，也不把它当空对象（那会静默清空已有声明）。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = client.post(
        f"/api/v1/projects/{ids.project_a}/robots",
        json={**_robot_payload("robot-cp"), "capability_profile": {"gripper": "two_finger"}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["capability_profile"] == {"gripper": "two_finger"}

    patched = client.patch(
        f"/api/v1/projects/{ids.project_a}/robots/{created.json()['robot_id']}",
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={"capability_profile": None, "name": "改名但不动能力"},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "改名但不动能力"
    assert patched.json()["capability_profile"] == {
        "gripper": "two_finger"
    }, "null 不应清空已有能力声明"
