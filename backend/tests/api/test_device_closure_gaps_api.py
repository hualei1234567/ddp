"""G-04 / G-05 / G-08 / G-11 / G-19 / G-20 的设备域闭环验收测试。

这些用例的共同点：**断言真实落库与真实响应**，而不是只看接口 200。
每条都对应一次"审计发现标注闭环但实际不完整"的缺陷，因此断言必须能
在实现退化时立刻失败。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import (
    ApiIds,
    calibration_payload,
    make_client,
    seed_profile,
    sensor_payload,
)
from tests.calibration_fixtures import parameters_for


def _device_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/devices{suffix}"


def _sensor_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/sensors{suffix}"


def _calibration_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/calibrations{suffix}"


def _profile_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/calibration-profiles{suffix}"


def _create_device(client: TestClient, project_id: object, code: str = "device-1") -> dict:
    response = client.post(
        _device_url(project_id),
        json={"code": code, "name": f"设备 {code}", "device_type": "COMPUTE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_calibration(
    client: TestClient,
    ids: ApiIds,
    *,
    target_id: str,
    profile_id: str,
    target_type: str = "DEVICE",
) -> dict:
    response = client.post(
        _calibration_url(ids.project_a),
        json=calibration_payload(
            profile_id=profile_id,
            target_id=target_id,
            evidence_asset_id=str(uuid4()),
            target_type=target_type,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- G-08：frame_id / frame_convention 必须真落库，且 PATCH 可改 --------------


def test_sensor_frame_fields_are_persisted_on_create(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-08：POST 传 frame_convention=ROS_REP_103，落库不许是默认的 UNSPECIFIED。"""
    from app.infrastructure.db.models.devices import Sensor as SensorModel

    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = _create_device(client, ids.project_a)
    payload = {
        **sensor_payload(code="cam-ros", device_id=device["device_id"]),
        "frame_id": "camera_link",
        "frame_convention": "ROS_REP_103",
    }

    created = client.post(_sensor_url(ids.project_a), json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["frame_convention"] == "ROS_REP_103"
    assert created.json()["frame_id"] == "camera_link"

    with factory() as session:
        row = session.scalar(
            select(SensorModel).where(SensorModel.id == UUID(created.json()["sensor_id"]))
        )
        assert row is not None
        assert row.frame_convention == "ROS_REP_103", "frame_convention 未被写入（退回默认值）"
        assert row.frame_id == "camera_link"


def test_sensor_frame_convention_is_patchable(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-08：frame_convention 此前不在 PATCH 白名单，修改请求直接 422。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = _create_device(client, ids.project_a)
    created = client.post(
        _sensor_url(ids.project_a),
        json=sensor_payload(device_id=device["device_id"]),
    ).json()
    assert created["frame_convention"] == "UNSPECIFIED"

    patched = client.patch(
        _sensor_url(ids.project_a, f"/{created['sensor_id']}"),
        headers={"If-Match": f'"{created["version"]}"'},
        json={"frame_convention": "OPENCV"},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["frame_convention"] == "OPENCV"
    refetched = client.get(_sensor_url(ids.project_a, f"/{created['sensor_id']}")).json()
    assert refetched["frame_convention"] == "OPENCV"


def test_sensor_mismatched_frame_id_is_rejected_not_rewritten(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-08：frame_id 与 coordinate_frame 不一致必须报错，不许静默改写。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = _create_device(client, ids.project_a)
    payload = {
        **sensor_payload(device_id=device["device_id"]),
        "frame_id": "base_link",
    }

    response = client.post(_sensor_url(ids.project_a), json=payload)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


# --- G-04：内容变更递增业务修订号，状态迁移不动 --------------------------------


def test_content_patch_increments_calibration_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-04：两次内容 PATCH → calibration_version 应为 3（1 起，每次 +1）。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    created = _create_calibration(
        client,
        ids,
        target_id=device["device_id"],
        profile_id=str(profile.profile_id),
    )
    assert created["calibration_version"] == 1

    first = client.patch(
        _calibration_url(ids.project_a, f"/{created['calibration_id']}"),
        headers={"If-Match": f'"{created["version"]}"'},
        json={"method": "hand-eye-tool/1.1"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["calibration_version"] == 2

    second = client.patch(
        _calibration_url(ids.project_a, f"/{created['calibration_id']}"),
        headers={"If-Match": f'"{first.json()["version"]}"'},
        json={"source": "field-calibration"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["calibration_version"] == 3, "内容变更未递增业务修订号"
    # 乐观锁同样随每次写入推进，但两者是独立的计数器。
    assert second.json()["version"] == 3

    refetched = client.get(_calibration_url(ids.project_a, f"/{created['calibration_id']}")).json()
    assert refetched["calibration_version"] == 3, "修订号没有落库"


def test_patch_with_identical_value_does_not_increment_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-04：把值改成相同值不是"内容变更"，不应虚增修订号。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    created = _create_calibration(
        client,
        ids,
        target_id=device["device_id"],
        profile_id=str(profile.profile_id),
    )

    response = client.patch(
        _calibration_url(ids.project_a, f"/{created['calibration_id']}"),
        headers={"If-Match": f'"{created["version"]}"'},
        json={"method": created["method"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["calibration_version"] == 1


def test_state_transition_does_not_increment_calibration_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-04：submit 只推进 version，calibration_version 必须保持不变。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    created = _create_calibration(
        client,
        ids,
        target_id=device["device_id"],
        profile_id=str(profile.profile_id),
    )

    submitted = client.post(
        _calibration_url(ids.project_a, f"/{created['calibration_id']}/submit"),
        headers={"If-Match": f'"{created["version"]}"'},
    )

    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "SUBMITTED"
    assert body["version"] == created["version"] + 1
    assert (
        body["calibration_version"] == created["calibration_version"]
    ), "状态迁移不得递增业务修订号"


# --- G-05：换 profile_id 必须重写快照 ----------------------------------------


def test_changing_profile_id_refreshes_snapshot(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-05：记录改指向 p2 后，快照必须同步，否则"可复现"失效。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    first_profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    created = _create_calibration(
        client,
        ids,
        target_id=device["device_id"],
        profile_id=str(first_profile.profile_id),
    )
    assert created["profile_version"] == first_profile.profile_version

    second = client.post(
        _profile_url(ids.project_a),
        json={
            "name": "DEVICE 新标定配置",
            "scope": "DEVICE",
            "target_type": "DEVICE",
            "schema_version": "ddp.device.calibration-parameters/2.0.0",
            "required_types": ["EXTRINSICS", "INTRINSICS"],
            "selection_rule": {"prefer": "latest_validated"},
        },
    )
    assert second.status_code == 201, second.text
    second_profile = second.json()

    patched = client.patch(
        _calibration_url(ids.project_a, f"/{created['calibration_id']}"),
        headers={"If-Match": f'"{created["version"]}"'},
        json={"profile_id": second_profile["profile_id"]},
    )

    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["profile_id"] == second_profile["profile_id"]
    assert body["profile_version"] == second_profile["profile_version"]
    assert (
        body["profile_schema_version"] == "ddp.device.calibration-parameters/2.0.0"
    ), "换 profile 后 schema 快照没有刷新"
    assert body["profile_snapshot"]["required_types"] == [
        "EXTRINSICS",
        "INTRINSICS",
    ], "换 profile 后内容快照没有刷新"
    assert body["profile_snapshot"]["target_type"] == "DEVICE"


# --- G-11：标定域审计与 outbox ------------------------------------------------


def test_calibration_create_and_submit_write_audit_and_outbox(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-11：创建与 submit 各产生一条 audit 与一条 outbox。"""
    from app.infrastructure.db.models.collection import AuditLog, OutboxEvent

    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    created = _create_calibration(
        client,
        ids,
        target_id=device["device_id"],
        profile_id=str(profile.profile_id),
    )
    calibration_id = UUID(created["calibration_id"])

    submitted = client.post(
        _calibration_url(ids.project_a, f"/{created['calibration_id']}/submit"),
        headers={"If-Match": f'"{created["version"]}"'},
    )
    assert submitted.status_code == 200, submitted.text

    with factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == ids.project_a,
                    AuditLog.resource_type == "calibration",
                    AuditLog.resource_id == calibration_id,
                )
            )
        )
        events = list(
            session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.project_id == ids.project_a,
                    OutboxEvent.aggregate_type == "calibration",
                    OutboxEvent.aggregate_id == calibration_id,
                )
            )
        )

    assert {row.action for row in audits} >= {"create", "submit"}
    assert {row.event_type for row in events} >= {
        "calibration.created",
        "calibration.submitted",
    }
    submit_audit = next(row for row in audits if row.action == "submit")
    assert submit_audit.from_status == "DRAFT"
    assert submit_audit.to_status == "SUBMITTED"
    assert submit_audit.actor_user_id == ids.editor_a
    # 留痕类事件：写入即终态，不进投递队列。
    assert {row.dispatch for row in events} == {False}


# --- G-19：参数校验错误的字段路径必须到客户端 ---------------------------------


def test_invalid_calibration_parameters_return_field_path_details(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-19：非法参数返回 422，details 里带出错字段路径。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    payload = calibration_payload(
        profile_id=str(profile.profile_id),
        target_id=device["device_id"],
        evidence_asset_id=str(uuid4()),
    )
    # INTRINSICS.unit 是 const "px"；给 "mm" 的报错路径正好落在 /unit，
    # 客户端据此知道该高亮哪个输入框。
    payload["calibration_type"] = "INTRINSICS"
    payload["parent_frame"] = None
    payload["child_frame"] = None
    payload["parameters"] = {**parameters_for("INTRINSICS"), "unit": "mm"}

    response = client.post(_calibration_url(ids.project_a), json=payload)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    details = error.get("details")
    assert details is not None, "参数校验失败必须回显结构化 details"
    assert details["calibration_type"] == "INTRINSICS"
    assert details["field_path"] == ["unit"], details
    assert details["error_count"] >= 1


def test_unknown_parameter_key_reports_root_level_path(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-19：根级错误（多传了字段）如实返回空路径，不伪造成某个具体字段。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)
    payload = calibration_payload(
        profile_id=str(profile.profile_id),
        target_id=device["device_id"],
        evidence_asset_id=str(uuid4()),
    )
    payload["parameters"] = {**payload["parameters"], "unexpected_key": 1}

    response = client.post(_calibration_url(ids.project_a), json=payload)

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field_path"] == []
    assert "unexpected_key" in response.text or details["error_count"] >= 1


# --- G-20：calibration_not_active 的真实触发 ---------------------------------


def test_activating_device_without_active_calibration_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-20：绑定 Profile 要求标定，但没有 ACTIVE 记录时激活必须被拒。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(client, ids.project_a)

    bound = client.patch(
        _device_url(ids.project_a, f"/{device['device_id']}"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={"calibration_profile_id": str(profile.profile_id)},
    )
    assert bound.status_code == 200, bound.text

    response = client.post(
        _device_url(ids.project_a, f"/{bound.json()['device_id']}/activate"),
        headers={"If-Match": f'"{bound.json()["version"]}"'},
        json={},
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "calibration_not_active"
    assert error["details"]["missing_calibration_types"] == ["INTRINSICS"]


def test_activating_device_with_active_calibration_succeeds(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-20 的正向对照：补齐 ACTIVE 标定后同一动作放行。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    # 门禁比较的是"Profile 要求的类型 ↔ 目标上的 ACTIVE 记录类型"，
    # 所以这里的 Profile 必须真的要求 EXTRINSICS（夹具只覆盖 EXTRINSICS 记录）。
    profile_response = editor.post(
        _profile_url(ids.project_a),
        json={
            "name": "设备外参标定配置",
            "scope": "DEVICE",
            "target_type": "DEVICE",
            "schema_version": "ddp.device.calibration-parameters/1.0.0",
            "required_types": ["EXTRINSICS"],
            "selection_rule": {},
        },
    )
    assert profile_response.status_code == 201, profile_response.text
    profile_id = profile_response.json()["profile_id"]
    device = _create_device(editor, ids.project_a)
    calibration = _create_calibration(
        editor,
        ids,
        target_id=device["device_id"],
        profile_id=profile_id,
    )

    submitted = editor.post(
        _calibration_url(ids.project_a, f"/{calibration['calibration_id']}/submit"),
        headers={"If-Match": f'"{calibration["version"]}"'},
    )
    assert submitted.status_code == 200, submitted.text
    validated = owner.post(
        _calibration_url(ids.project_a, f"/{calibration['calibration_id']}/validate"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
    )
    assert validated.status_code == 200, validated.text
    activated = editor.post(
        _calibration_url(ids.project_a, f"/{calibration['calibration_id']}/activate"),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
    )
    assert activated.status_code == 200, activated.text

    bound = editor.patch(
        _device_url(ids.project_a, f"/{device['device_id']}"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={"calibration_profile_id": profile_id},
    )
    assert bound.status_code == 200, bound.text

    response = editor.post(
        _device_url(ids.project_a, f"/{bound.json()['device_id']}/activate"),
        headers={"If-Match": f'"{bound.json()["version"]}"'},
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACTIVE"


def test_activating_calibration_outside_valid_window_maps_to_calibration_invalid(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-20：有效期窗口不满足时激活映射为 calibration_invalid（非 validation_failed）。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device = _create_device(editor, ids.project_a)
    payload = calibration_payload(
        profile_id=str(profile.profile_id),
        target_id=device["device_id"],
        evidence_asset_id=str(uuid4()),
    )
    # 已过期的标定：创建合法，激活必须被拒且错误码可区分。
    payload["valid_to"] = "2026-02-01T00:00:00Z"
    created = editor.post(_calibration_url(ids.project_a), json=payload)
    assert created.status_code == 201, created.text
    calibration_id = created.json()["calibration_id"]

    submitted = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/submit"),
        headers={"If-Match": f'"{created.json()["version"]}"'},
    )
    assert submitted.status_code == 200, submitted.text
    validated = owner.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/validate"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
    )
    assert validated.status_code == 200, validated.text

    response = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/activate"),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "calibration_invalid"
    assert error["details"]["reason"] == "expired"


# --- G-20 边界：门禁不能把"没声明要求"当成"要求已满足"或反向拒绝 ----------


def test_unbound_profile_does_not_block_activation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """未绑定 Profile 时激活放行。

    这是门禁最容易被写错的一条：``profile_id is None`` 若顺手 return 之外
    再抛错，会把所有没配标定的实体锁死；若反过来把 ``required`` 为空也当
    失败，同样锁死。两种错都会让设备域整体不可用，必须有测试钉住。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = _create_device(client, ids.project_a, code="device-no-profile")

    assert device["calibration_profile_id"] is None
    response = client.post(
        _device_url(ids.project_a, f"/{device['device_id']}/activate"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACTIVE"


def test_profile_without_required_types_does_not_block_activation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """Profile 存在但 ``required_types`` 为空时同样放行。

    与上一条的区别：这里是"声明了配置但没声明要标定"。门禁只校验
    ``required_types`` 里列出的类型，空清单意味着没有前置要求。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    device = _create_device(client, ids.project_a, code="device-empty-req")

    profile = client.post(
        _profile_url(ids.project_a),
        json={
            "name": "无标定要求的配置",
            "scope": "DEVICE",
            "target_type": "DEVICE",
            "schema_version": "ddp.device.calibration-parameters/1.0.0",
            "required_types": [],
            "selection_rule": {},
        },
    )
    assert profile.status_code == 201, profile.text

    bound = client.patch(
        _device_url(ids.project_a, f"/{device['device_id']}"),
        headers={"If-Match": f'"{device["version"]}"'},
        json={"calibration_profile_id": profile.json()["profile_id"]},
    )
    assert bound.status_code == 200, bound.text

    response = client.post(
        _device_url(ids.project_a, f"/{bound.json()['device_id']}/activate"),
        headers={"If-Match": f'"{bound.json()["version"]}"'},
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACTIVE"
