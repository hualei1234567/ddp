"""数据源 API 集成测试：登记、许可守卫、项目隔离、幂等与导入状态机。"""

from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_client

_SHA = "0123456789abcdef0123456789abcdef01234567"


def _url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/data-sources{suffix}"


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "code": f"hf-{uuid4().hex[:8]}",
        "name": "示例 HF 数据集",
        "repo_id": "lerobot/aloha_sim_insertion_human",
        "revision": _SHA,
        "license": "Apache-2.0",
        "access_condition": "PUBLIC",
        "is_gated": False,
    }
    base.update(overrides)
    return base


def test_editor_can_register_and_read_data_source(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    payload = _payload()

    created = client.post(_url(ids.project_a), json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["repo_id"] == "lerobot/aloha_sim_insertion_human"
    assert body["revision"] == _SHA
    assert body["license"] == "Apache-2.0"
    assert body["version"] == 1

    detail = client.get(_url(ids.project_a, f"/{body['data_source_id']}"))
    assert detail.status_code == 200
    assert detail.json()["code"] == payload["code"]


def test_branch_name_as_revision_is_rejected_at_contract_layer(api_db) -> None:
    """revision 必须是 commit sha；用 main 登记无法复现导入。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    response = client.post(_url(ids.project_a), json=_payload(revision="main"))

    assert response.status_code == 422


def test_gated_without_flag_is_rejected(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    response = client.post(
        _url(ids.project_a), json=_payload(access_condition="GATED", is_gated=False)
    )

    assert response.status_code == 422


def test_same_repo_revision_cannot_be_registered_twice(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    first = client.post(_url(ids.project_a), json=_payload())
    assert first.status_code == 201, first.text
    duplicate = client.post(_url(ids.project_a), json=_payload(code="another-code"))

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"


def test_outsider_cannot_read_data_source(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    outsider = make_client(factory, ids.outsider)
    created = editor.post(_url(ids.project_a), json=_payload()).json()

    response = outsider.get(_url(ids.project_a, f"/{created['data_source_id']}"))

    assert response.status_code in {403, 404}


def test_create_is_idempotent_on_same_key(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "ds-idem-0001"}
    payload = _payload()

    first = client.post(_url(ids.project_a), json=payload, headers=headers)
    second = client.post(_url(ids.project_a), json=payload, headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["data_source_id"] == first.json()["data_source_id"]


def test_patch_updates_license_with_if_match(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = client.post(_url(ids.project_a), json=_payload()).json()

    patched = client.patch(
        _url(ids.project_a, f"/{created['data_source_id']}"),
        headers={"If-Match": '"1"'},
        json={"license": "MIT"},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["license"] == "MIT"
    assert patched.json()["version"] == 2


def test_patch_without_if_match_is_rejected(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = client.post(_url(ids.project_a), json=_payload()).json()

    response = client.patch(
        _url(ids.project_a, f"/{created['data_source_id']}"), json={"license": "MIT"}
    )

    assert response.status_code == 428


def test_import_lifecycle_and_terminal_state(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = client.post(_url(ids.project_a), json=_payload()).json()

    created = client.post(
        _url(ids.project_a, f"/{source['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 10,
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "PENDING"
    assert sorted(job["allowed_actions"]) == ["cancel", "start"]

    import_url = f"/api/v1/projects/{ids.project_a}/data-source-imports/{job['import_id']}"
    started = client.post(f"{import_url}/actions/start", headers={"If-Match": '"1"'}, json={})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "RUNNING"
    assert started.json()["started_at"] is not None

    succeeded = client.post(
        f"{import_url}/actions/succeed",
        headers={"If-Match": '"2"'},
        json={"row_count": 10, "file_count": 2},
    )
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["status"] == "SUCCEEDED"
    assert succeeded.json()["imported_row_count"] == 10
    assert succeeded.json()["allowed_actions"] == []

    # 终态不得回退
    again = client.post(f"{import_url}/actions/start", headers={"If-Match": '"3"'}, json={})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "state_transition_invalid"


def test_failed_import_requires_error_message(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = client.post(_url(ids.project_a), json=_payload()).json()
    job = client.post(
        _url(ids.project_a, f"/{source['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 5,
        },
    ).json()
    import_url = f"/api/v1/projects/{ids.project_a}/data-source-imports/{job['import_id']}"
    client.post(f"{import_url}/actions/start", headers={"If-Match": '"1"'}, json={})

    response = client.post(f"{import_url}/actions/fail", headers={"If-Match": '"2"'}, json={})

    assert response.status_code == 422


def test_import_writes_audit_and_outbox(api_db) -> None:
    from uuid import UUID

    from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
    from sqlalchemy import select

    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = client.post(_url(ids.project_a), json=_payload()).json()
    job = client.post(
        _url(ids.project_a, f"/{source['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 3,
        },
    ).json()

    with factory() as session:
        audits = list(session.scalars(select(AuditLog).where(AuditLog.project_id == ids.project_a)))
        events = list(
            session.scalars(select(OutboxEvent).where(OutboxEvent.project_id == ids.project_a))
        )

    assert {row.event_type for row in events} >= {"datasource.import_requested"}
    assert "create" in {row.action for row in audits}
    # 导入请求事件必须带足够信息给 worker，否则无法离线重放。
    request_event = next(row for row in events if row.event_type == "datasource.import_requested")
    assert request_event.payload["repo_id"] == "lerobot/aloha_sim_insertion_human"
    assert request_event.payload["revision"] == _SHA
    assert UUID(str(request_event.payload["import_id"])) == UUID(job["import_id"])
