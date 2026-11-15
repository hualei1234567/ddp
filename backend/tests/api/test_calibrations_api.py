"""标定 API：真实调用、四眼原则、项目隔离与错误码。"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import (
    ApiIds,
    calibration_payload,
    device_payload,
    make_client,
    seed_profile,
)


def _calibration_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/calibrations{suffix}"


def _create_device(client: TestClient, project_id: object, code: str = "device-1") -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/devices",
        json=device_payload(code),
    )
    assert response.status_code == 201, response.text
    return response.json()["device_id"]


def _create_calibration(
    client: TestClient,
    ids: ApiIds,
    *,
    device_id: str,
    profile_id: str,
) -> dict[str, object]:
    response = client.post(
        _calibration_url(ids.project_a),
        json=calibration_payload(
            profile_id=profile_id,
            target_id=device_id,
            evidence_asset_id=str(uuid4()),
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_full_calibration_flow_with_four_eyes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)

    created = _create_calibration(
        editor,
        ids,
        device_id=device_id,
        profile_id=str(profile.profile_id),
    )
    assert created["status"] == "DRAFT"
    assert created["performed_by"] == str(ids.editor_a)
    assert str(created["checksum"]).startswith("sha256:")
    calibration_id = created["calibration_id"]

    listed = editor.get(
        _calibration_url(ids.project_a),
        params={"target_type": "DEVICE", "target_id": device_id},
    )
    assert listed.status_code == 200
    assert [item["calibration_id"] for item in listed.json()["items"]] == [calibration_id]

    submitted = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/submit"),
        headers={"If-Match": '"1"'},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "SUBMITTED"
    assert submitted.json()["version"] == 2

    validated = owner.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/validate"),
        headers={"If-Match": '"2"'},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "VALIDATED"
    assert validated.json()["validated_by"] == str(ids.owner_a)

    activated = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/activate"),
        headers={"If-Match": '"3"'},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert activated.json()["version"] == 4


def test_executor_cannot_validate_own_calibration(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)
    created = _create_calibration(
        editor,
        ids,
        device_id=device_id,
        profile_id=str(profile.profile_id),
    )
    calibration_id = created["calibration_id"]
    assert (
        editor.post(
            _calibration_url(ids.project_a, f"/{calibration_id}/submit"),
            headers={"If-Match": '"1"'},
        ).status_code
        == 200
    )

    response = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/validate"),
        headers={"If-Match": '"2"'},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_viewer_cannot_create_or_validate_calibration(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)
    created = _create_calibration(
        editor,
        ids,
        device_id=device_id,
        profile_id=str(profile.profile_id),
    )

    create_denied = viewer.post(
        _calibration_url(ids.project_a),
        json=calibration_payload(
            profile_id=str(profile.profile_id),
            target_id=device_id,
            evidence_asset_id=str(uuid4()),
        ),
    )
    validate_denied = viewer.post(
        _calibration_url(
            ids.project_a,
            f"/{created['calibration_id']}/validate",
        ),
        headers={"If-Match": '"1"'},
    )

    assert create_denied.status_code == 403
    assert create_denied.json()["error"]["code"] == "forbidden"
    assert validate_denied.status_code == 403
    assert validate_denied.json()["error"]["code"] == "forbidden"


def test_cross_project_target_maps_to_calibration_target_invalid(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    editor = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    foreign_device = _create_device(owner, ids.project_b, "b-device")

    response = editor.post(
        _calibration_url(ids.project_a),
        json=calibration_payload(
            profile_id=str(profile.profile_id),
            target_id=foreign_device,
            evidence_asset_id=str(uuid4()),
        ),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "calibration_target_invalid"


def test_cross_project_profile_maps_to_calibration_profile_invalid(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    foreign_profile = seed_profile(factory, ids.project_b, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)

    response = editor.post(
        _calibration_url(ids.project_a),
        json=calibration_payload(
            profile_id=str(foreign_profile.profile_id),
            target_id=device_id,
            evidence_asset_id=str(uuid4()),
        ),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "calibration_profile_invalid"


def test_invalid_state_and_stale_version_are_stable_codes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)
    created = _create_calibration(
        editor,
        ids,
        device_id=device_id,
        profile_id=str(profile.profile_id),
    )
    calibration_id = created["calibration_id"]

    invalid = owner.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/validate"),
        headers={"If-Match": '"1"'},
    )
    stale = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/submit"),
        headers={"If-Match": '"9"'},
    )

    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "state_transition_invalid"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_checksum_matches_rfc8785_jcs_of_fixed_fields(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-15：checksum 必须能由固定字段按 JCS 独立重算出来。

    这里不复用服务端函数，而是按"外部审计方"的方式重建载荷——如果实现退回
    ``json.dumps(sort_keys=True)``，本用例立刻失败。
    """
    from app.domain.common.canonical_json import canonical_json, sha256_text

    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)
    created = _create_calibration(
        editor, ids, device_id=device_id, profile_id=str(profile.profile_id)
    )

    payload = {
        "profile_id": created["profile_id"],
        "scope": created["scope"],
        "target_type": created["target_type"],
        "target_id": created["target_id"],
        "calibration_type": created["calibration_type"],
        "method": created["method"],
        "parameters": created["parameters"],
        "parent_frame": created.get("parent_frame"),
        "child_frame": created.get("child_frame"),
        "valid_from": created["valid_from"].replace("+00:00", "Z"),
        "valid_to": created.get("valid_to"),
        "evidence_asset_ids": created["evidence_asset_ids"],
        "uncertainty": created["uncertainty"],
        "performed_by": created["performed_by"],
        "supersedes_id": created.get("supersedes_id"),
    }
    assert created["checksum"] == sha256_text(canonical_json(payload))


def test_submit_rejects_record_tampered_after_creation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """G-15：绕过用例层直写参数后，submit 必须因 checksum 不符而失败。

    这是"DRAFT 写入后被人改过"的真实场景——只在写入路径重算 checksum
    发现不了它，必须在状态命令前重算比对。
    """
    from uuid import UUID

    from app.infrastructure.db.models.devices import CalibrationRecord as CalibrationRecordModel
    from sqlalchemy import select

    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    profile = seed_profile(factory, ids.project_a, ids.owner_a)
    device_id = _create_device(editor, ids.project_a)
    created = _create_calibration(
        editor, ids, device_id=device_id, profile_id=str(profile.profile_id)
    )
    calibration_id = created["calibration_id"]

    with factory() as session:
        row = session.execute(
            select(CalibrationRecordModel).where(CalibrationRecordModel.id == UUID(calibration_id))
        ).scalar_one()
        tampered = dict(created["parameters"])
        # 改成**仍然合法**但不同的值：G-19 的 schema 会放行，
        # 只有 checksum 比对能发现——这正是本用例要锁定的那层防线。
        tampered["translation_m"] = [0.9, 0.9, 0.9]
        row.parameters = tampered
        session.commit()

    submitted = editor.post(
        _calibration_url(ids.project_a, f"/{calibration_id}/submit"),
        headers={"If-Match": '"1"'},
    )
    # 错误体按契约只回通用文案（不泄漏内部字段），因此断言状态码与"未被推进"。
    assert submitted.status_code == 422, submitted.text
    still_draft = editor.get(_calibration_url(ids.project_a, f"/{calibration_id}"))
    assert still_draft.status_code == 200
    assert still_draft.json()["status"] == "DRAFT"
