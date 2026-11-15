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


# --- 媒体资产与播放 URL ------------------------------------------------------


def _seed_asset(factory, ids, source_id, import_id, **overrides):
    """直接插一条媒体资产：播放 URL 的测试不需要真的下载文件。"""
    from uuid import UUID, uuid4

    from app.infrastructure.db.models.datasources import MediaAssetModel

    asset_id = uuid4()
    path = overrides.get("path", "videos/demo_001_main.mp4")
    camera = overrides.get("camera", "main")
    with factory() as session:
        session.add(
            MediaAssetModel(
                id=asset_id,
                project_id=ids.project_a,
                data_source_id=UUID(str(source_id)),
                import_id=UUID(str(import_id)),
                episode_key="demo_001",
                path=path,
                camera=camera,
                object_key=f"data-sources/{ids.project_a}/{asset_id}/v.mp4",
                size_bytes=754668,
                sha256="sha256:" + "a" * 64,
                content_type="video/mp4",
                task_name="Sew lace fabric",
                excerpt_start_seconds=69.0,
                excerpt_seconds=12.0,
                source_fps=30.0,
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.commit()
    return str(asset_id)


def _an_import_id(factory, ids):
    from uuid import uuid4

    from app.infrastructure.db.models.datasources import (
        DataSourceImportModel,
        DataSourceModel,
    )

    source_id, import_id = uuid4(), uuid4()
    with factory() as session:
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=ids.project_a,
                code=f"hf-{source_id.hex[:8]}",
                name="源",
                kind="HUGGINGFACE",
                repo_id="nvidia/PhysicalAI-Human-1M-100K",
                revision=_SHA,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.flush()
        session.add(
            DataSourceImportModel(
                id=import_id,
                project_id=ids.project_a,
                data_source_id=source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=15,
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.commit()
    return str(source_id), str(import_id)


def test_list_import_assets_returns_seeded_asset(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source_id, import_id = _an_import_id(factory, ids)
    asset_id = _seed_asset(factory, ids, source_id, import_id)

    response = client.get(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/assets"
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["asset_id"] for item in items] == [asset_id]
    assert items[0]["camera"] == "main"
    assert items[0]["excerpt_start_seconds"] == 69.0


def test_playback_url_is_presigned_and_scoped(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source_id, import_id = _an_import_id(factory, ids)
    asset_id = _seed_asset(factory, ids, source_id, import_id)

    response = client.post(f"/api/v1/projects/{ids.project_a}/media-assets/{asset_id}/playback")

    assert response.status_code == 200, response.text
    body = response.json()
    # URL 必须带 SigV4 签名参数。Credential 里含 access key 是 SigV4 标准行为；
    # 真正不能出现的是 secret key。
    assert "X-Amz-Signature" in body["url"]
    assert "X-Amz-Credential=" in body["url"]
    assert "X-Amz-Expires=900" in body["url"]
    assert "aws_secret_access_key" not in body["url"]
    assert body["expires_in_seconds"] == 900
    assert body["content_type"] == "video/mp4"


def test_playback_requires_project_membership(api_db) -> None:
    factory, ids = api_db
    outsider = make_client(factory, ids.outsider)
    source_id, import_id = _an_import_id(factory, ids)
    asset_id = _seed_asset(factory, ids, source_id, import_id)

    response = outsider.post(f"/api/v1/projects/{ids.project_a}/media-assets/{asset_id}/playback")

    assert response.status_code in {403, 404}


def test_playback_for_unknown_asset_is_404(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    response = client.post(f"/api/v1/projects/{ids.project_a}/media-assets/{uuid4()}/playback")

    assert response.status_code == 404


# --- 人工审核与质量报告 ------------------------------------------------------


def test_review_lifecycle_with_segregation_of_duties(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)

    created = editor.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review"
    )
    assert created.status_code == 201, created.text
    review = created.json()
    assert review["status"] == "PENDING"
    assert review["submitted_by"] == str(ids.editor_a)

    # 幂等：重复创建返回同一条
    again = editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")
    assert again.json()["review_id"] == review["review_id"]

    # 提交人不得自审
    self_review = editor.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT"},
    )
    assert self_review.status_code == 422

    approved = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["reviewer_id"] == str(ids.owner_a)

    # 终态不可再裁决
    second = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"2"'},
        json={"decision": "REJECT", "reason": "反悔"},
    )
    assert second.status_code == 422


def test_reject_requires_reason(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")

    response = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "REJECT"},
    )

    assert response.status_code == 422


def test_quality_report_on_real_assets(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source_id, import_id = _an_import_id(factory, ids)
    _seed_asset(factory, ids, source_id, import_id)
    # 再补一个对齐的机位：1.1.0 的 stream_sync_aligned 规则要求至少两路
    # 才能判定"同步"，单路会如实报 SINGLE_STREAM 不通过。
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        path="videos/demo_001_wrist.mp4",
        camera="wrist_left",
    )

    response = client.get(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/quality-report"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # 1.1.0 起新增 stream_sync_aligned 规则；规则集变化必须升版本。
    assert body["rule_version"] == "1.1.0"
    assert body["sample_scope"] == "SAMPLE"
    assert body["total_assets"] == 2
    assert body["passed"] is True
    assert body["failures"] == []
    # 报告必须逐条给出规则结果，不能只给总分
    assert {f["rule_id"] for f in body["findings"]} >= {
        "assets_present",
        "main_camera_present",
        # N3 新增：多路同步偏差
        "stream_sync_aligned",
    }


def test_missing_review_is_404(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source_id, import_id = _an_import_id(factory, ids)

    response = client.get(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review"
    )

    assert response.status_code == 404
