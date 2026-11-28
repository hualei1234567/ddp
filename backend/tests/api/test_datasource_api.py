"""数据源 API 集成测试：登记、许可守卫、项目隔离、幂等与导入状态机。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.api.v1.dataset_pagination import encode_cursor
from app.api.v1.datasource_routes import get_remote_source_verifier
from app.application.datasources.verification import (
    RemoteCheckError,
    RemoteCheckFailureReason,
    RemoteCheckResult,
)
from app.domain.datasources.enums import ImportLogLevel, ImportStage
from app.domain.datasources.models import DataSourceImportLog
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.datasources import DataReviewModel
from app.infrastructure.repositories.datasources import (
    SqlDataReviewRepository,
    SqlDataSourceImportLogRepository,
)
from sqlalchemy import select, update

from tests.api.conftest import make_client

_SHA = "0123456789abcdef0123456789abcdef01234567"
_OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


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


# --- 远端校验接线 ------------------------------------------------------------


class _FakeVerifier:
    """测试替身：禁止任何用例真连外网。"""

    def __init__(
        self,
        result: RemoteCheckResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def describe(self, repo_id: str, *, revision: str) -> RemoteCheckResult:
        self.calls.append((repo_id, revision))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _remote_result(**overrides: object) -> RemoteCheckResult:
    base: dict[str, object] = {
        "repo_id": "lerobot/aloha_sim_insertion_human",
        "resolved_revision": _SHA,
        "license": "Apache-2.0",
        "is_gated": False,
        "dataset_card_url": "https://huggingface.co/datasets/lerobot/aloha_sim_insertion_human",
    }
    base.update(overrides)
    return RemoteCheckResult(**base)  # type: ignore[arg-type]


def _override_verifier(client, verifier: _FakeVerifier) -> None:
    client.app.dependency_overrides[get_remote_source_verifier] = lambda: verifier


def test_register_with_remote_verification_records_summary(api_db) -> None:
    """显式 verify_remote 时记录规范化结论；不落远端原始字段。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    verifier = _FakeVerifier(_remote_result())
    _override_verifier(client, verifier)

    created = client.post(_url(ids.project_a), json=_payload(verify_remote=True))

    assert created.status_code == 201, created.text
    assert verifier.calls == [("lerobot/aloha_sim_insertion_human", _SHA)]
    body = created.json()
    check = body["metadata"]["remote_check"]
    assert check["status"] == "VERIFIED"
    assert check["resolved_revision"] == _SHA
    assert check["license"] == "Apache-2.0"
    assert check["is_gated"] is False
    assert datetime.fromisoformat(check["checked_at"]).tzinfo is not None

    detail = client.get(_url(ids.project_a, f"/{body['data_source_id']}/detail"))
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["data_source"]["data_source_id"] == body["data_source_id"]
    assert payload["imports"] == []
    assert payload["remote_check"]["resolved_revision"] == _SHA
    assert payload["remote_check"]["status"] == "VERIFIED"


def test_register_without_verify_remote_never_calls_remote(api_db) -> None:
    """默认不校验：避免每次登记都访问外网。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    verifier = _FakeVerifier(_remote_result())
    _override_verifier(client, verifier)

    created = client.post(_url(ids.project_a), json=_payload())

    assert created.status_code == 201, created.text
    assert verifier.calls == []
    assert "remote_check" not in created.json()["metadata"]


def test_remote_gated_mismatch_rejects_registration(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _override_verifier(
        client,
        _FakeVerifier(_remote_result(is_gated=True)),
    )

    response = client.post(
        _url(ids.project_a),
        json=_payload(verify_remote=True, access_condition="PUBLIC", is_gated=False),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    listed = client.get(_url(ids.project_a)).json()["items"]
    assert listed == []


def test_remote_timeout_rejects_registration(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _override_verifier(
        client,
        _FakeVerifier(
            error=RemoteCheckError("读取元信息超时", reason=RemoteCheckFailureReason.TIMEOUT)
        ),
    )

    response = client.post(_url(ids.project_a), json=_payload(verify_remote=True))

    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "validation_failed"
    # 失败必须显式记录状态与原因，供前端区分超时/404/5xx，而不是笼统报错。
    assert body["details"]["remote_check"] == {
        "status": "FAILED",
        "reason": "TIMEOUT",
    }


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (RemoteCheckFailureReason.NOT_FOUND, "NOT_FOUND"),
        (RemoteCheckFailureReason.UNAUTHORIZED, "UNAUTHORIZED"),
        (RemoteCheckFailureReason.SERVER_ERROR, "SERVER_ERROR"),
        (RemoteCheckFailureReason.INVALID_RESPONSE, "INVALID_RESPONSE"),
        (RemoteCheckFailureReason.RESPONSE_TOO_LARGE, "RESPONSE_TOO_LARGE"),
    ],
)
def test_remote_failure_surfaces_structured_reason(api_db, reason, expected) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _override_verifier(client, _FakeVerifier(error=RemoteCheckError("远端不可用", reason=reason)))

    response = client.post(_url(ids.project_a), json=_payload(verify_remote=True))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["remote_check"]["reason"] == expected


def test_remote_failure_never_echoes_credentials(api_db) -> None:
    """远端报错里混入的凭据/URL 不得出现在响应体。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _override_verifier(
        client,
        _FakeVerifier(
            error=RemoteCheckError(
                "500 for https://huggingface.co/api/x?token=hf_leak Authorization: Bearer hf_leak2",
                reason=RemoteCheckFailureReason.SERVER_ERROR,
            )
        ),
    )

    response = client.post(_url(ids.project_a), json=_payload(verify_remote=True))

    assert response.status_code == 422, response.text
    text = response.text
    assert "hf_leak" not in text
    assert "huggingface.co" not in text
    # 响应只含稳定枚举原因，不含远端原文。
    assert response.json()["error"]["details"]["remote_check"]["reason"] == "SERVER_ERROR"


def test_remote_revision_mismatch_rejects_registration(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _override_verifier(
        client,
        _FakeVerifier(_remote_result(resolved_revision=_OTHER_SHA)),
    )

    response = client.post(_url(ids.project_a), json=_payload(verify_remote=True))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_client_cannot_spoof_remote_check_metadata(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    response = client.post(
        _url(ids.project_a),
        json=_payload(metadata={"remote_check": {"status": "VERIFIED"}}),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_detail_lists_recent_imports(api_db) -> None:
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
    )
    assert job.status_code == 201, job.text

    detail = client.get(_url(ids.project_a, f"/{source['data_source_id']}/detail"))

    assert detail.status_code == 200, detail.text
    imports = detail.json()["imports"]
    assert [item["import_id"] for item in imports] == [job.json()["import_id"]]


def test_detail_is_project_scoped_and_missing_is_404(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    outsider = make_client(factory, ids.outsider)
    created = editor.post(_url(ids.project_a), json=_payload()).json()

    outsider_response = outsider.get(_url(ids.project_a, f"/{created['data_source_id']}/detail"))
    assert outsider_response.status_code in {403, 404}

    missing = editor.get(_url(ids.project_a, f"/{uuid4()}/detail"))
    assert missing.status_code == 404


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

    # 成功事件是投递类：客户端宣布成功也必须触发 Episode 自动物化（V31-014），
    # 否则这条路径的导入永远停在 RawAsset 阶段。
    with factory() as session:
        succeeded_event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == UUID(job["import_id"]),
                OutboxEvent.event_type == "datasource.import.succeed",
            )
        )
        assert succeeded_event is not None
        assert succeeded_event.dispatch is True
        assert succeeded_event.status == "PENDING"

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


def test_import_retry_is_system_only_and_rejected_over_http(api_db) -> None:
    """``retry`` 是 Worker 的系统动作，HTTP 客户端调用必须 4xx 且状态不变。

    FAILED 对客户端是终态：任何客户端动作都 409/422，不允许借 ``retry``
    把任务打回 PENDING（那会绕过 Worker 的 ``attempt < max_attempts`` 判断）。
    """
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
    failed = client.post(
        f"{import_url}/actions/fail",
        headers={"If-Match": '"2"'},
        json={"error_message": "网络抖动"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["allowed_actions"] == []

    response = client.post(f"{import_url}/actions/retry", headers={"If-Match": '"3"'}, json={})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"

    unchanged = client.get(import_url)
    assert unchanged.status_code == 200
    body = unchanged.json()
    assert body["status"] == "FAILED"
    assert body["version"] == 3
    assert body["allowed_actions"] == []


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


def test_review_evidence_is_persisted_and_returned(api_db) -> None:
    """evidence 与审核结论同事务落库，并在审核记录响应中回读。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")

    evidence = {
        "timestamp_seconds": 69.0,
        "camera": "main",
        "note": "抽查画面正常",
        "episode_key": "demo_001",
    }
    decided = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "reason": "抽查通过", "evidence": evidence},
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["evidence"] == evidence

    fetched = owner.get(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["evidence"] == evidence


def test_review_evidence_rejects_unknown_key(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")

    response = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "evidence": {"arbitrary": "payload"}},
    )

    assert response.status_code == 422


def test_review_evidence_rejects_oversized_payload(api_db) -> None:
    """Pydantic 只限单字段长度；整体字节上限由领域层兜底。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")

    response = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={
            "decision": "ACCEPT",
            "evidence": {
                "timestamp_seconds": 1.0,
                "camera": "机" * 64,
                "episode_key": "片" * 128,
                "note": "审" * 500,
            },
        },
    )

    assert response.status_code == 422


def test_review_evidence_is_project_scoped(api_db) -> None:
    """跨项目路径不得读到或改写别家的审核证据。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")
    owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "evidence": {"note": "项目 A 的证据"}},
    )

    # owner_a 是项目 B 的负责人，但 import 属于项目 A。
    cross_read = owner.get(
        f"/api/v1/projects/{ids.project_b}/data-source-imports/{import_id}/review"
    )
    cross_write = owner.post(
        f"/api/v1/projects/{ids.project_b}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "evidence": {"note": "越权"}},
    )

    assert cross_read.status_code == 404
    assert "项目 A 的证据" not in cross_read.text
    assert cross_write.status_code == 404


def test_review_evidence_is_stored_in_dedicated_column_and_audit(api_db) -> None:
    """N2-009：新决定同时写审核表 evidence 列与审计详情，读取命中列。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")

    evidence = {"timestamp_seconds": 12.5, "camera": "main", "note": "列内证据"}
    decided = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "evidence": evidence},
    )
    assert decided.status_code == 200, decided.text
    review_id = UUID(decided.json()["review_id"])

    with factory() as session:
        row = session.get(DataReviewModel, review_id)
        assert row is not None
        # 证据独立成列：不再只寄存在审计里。
        assert row.evidence == evidence
        audit_actions = session.scalars(
            select(AuditLog).where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_id == review_id,
            )
        ).all()
        assert any(
            action.action == "decide:ACCEPT" and dict(action.details).get("evidence") == evidence
            for action in audit_actions
        )

    fetched = owner.get(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")
    assert fetched.status_code == 200, fetched.text
    # 响应形状不变：契约字段齐全（未提供的键回显 None）。
    assert fetched.json()["evidence"] == {**evidence, "episode_key": None}


def test_legacy_review_without_column_falls_back_to_audit(api_db) -> None:
    """迁移前的存量行（evidence 列 NULL）仍从审计留痕回读，行为不变。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")

    evidence = {"note": "仅存于审计的历史证据"}
    decided = owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "evidence": evidence},
    )
    assert decided.status_code == 200, decided.text
    review_id = UUID(decided.json()["review_id"])

    # 模拟迁移前的行：列尚未有值，证据只在 audit_logs.details。
    with factory() as session:
        session.execute(
            update(DataReviewModel).where(DataReviewModel.id == review_id).values(evidence=None)
        )
        session.commit()

    fetched = owner.get(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["evidence"] == {
        "timestamp_seconds": None,
        "camera": None,
        **evidence,
        "episode_key": None,
    }


def test_review_evidence_column_is_project_scoped(api_db) -> None:
    """仓储层同样以服务端项目作用域取证据，跨项目读返回 None。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    source_id, import_id = _an_import_id(factory, ids)
    editor.post(f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review")
    owner.post(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{import_id}/review/decisions",
        headers={"If-Match": '"1"'},
        json={"decision": "ACCEPT", "evidence": {"note": "项目 A 列内证据"}},
    )

    with factory() as session:
        repo = SqlDataReviewRepository(session)
        foreign = repo.get_by_import(
            ProjectScope(project_id=ids.project_b, actor_id=ids.owner_a), UUID(import_id)
        )

    assert foreign is None


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


def test_import_can_claim_collection_task_ownership(api_db) -> None:
    """导入可以声明「数据是为哪个采集任务采的」，并在响应里回读。

    没有这个字段时，导入产出的资产与 Episode 的 `collection_task_id` 永远
    是 NULL，按任务统计产出永远是 0——「导入到指定任务下」在产品上就不存在。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = client.post(_url(ids.project_a), json=_payload()).json()

    task = client.post(
        f"/api/v1/projects/{ids.project_a}/collection-tasks",
        json={
            "code": "task-import-owner",
            "name": "导入归属测试任务",
            "priority": "P2",
            "target_episode_count": 5,
            "target_duration_sec": 60,
        },
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["task_id"]

    created = client.post(
        _url(ids.project_a, f"/{source['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 5,
            "collection_task_id": task_id,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["collection_task_id"] == task_id


def test_import_rejects_unknown_ownership_field(api_db) -> None:
    """归属字段之外的未知键仍然被拒绝（`extra=forbid` 不放宽）。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = client.post(_url(ids.project_a), json=_payload()).json()

    created = client.post(
        _url(ids.project_a, f"/{source['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 5,
            "unexpected": "x",
        },
    )
    assert created.status_code == 422


def test_import_action_body_is_optional(api_db) -> None:
    """契约 ``requestBody.required: false``：省略请求体必须被接受。

    此前路由参数写成 ``payload: ImportActionRequest | None``（无默认值），
    FastAPI 仍判为必填，客户端不带 body 会拿到 422「请求参数校验失败」，
    与契约冲突且无法从错误里看出"其实不用带 body"。
    """
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

    # 不带请求体：cancel 是合法动作，应该成功而不是 422。
    cancelled = client.post(f"{import_url}/actions/cancel", headers={"If-Match": '"1"'})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"


def _create_import_for_logs(client, project_id, *, sample_limit: int = 5) -> dict:
    source = client.post(_url(project_id), json=_payload()).json()
    created = client.post(
        _url(project_id, f"/{source['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": sample_limit,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _seed_import_logs(
    factory,
    *,
    project_id: UUID,
    actor_id: UUID,
    import_id: UUID,
    count: int,
    step_seconds: int = 60,
) -> None:
    """直接写入步骤日志，验证只读端点的游标行为（不依赖 worker 执行）。"""
    scope = ProjectScope(project_id=project_id, actor_id=actor_id)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with factory() as session:
        repository = SqlDataSourceImportLogRepository(session)
        for index in range(count):
            repository.add(
                scope,
                DataSourceImportLog(
                    log_id=uuid4(),
                    project_id=project_id,
                    import_id=import_id,
                    stage=ImportStage.DOWNLOAD,
                    level=ImportLogLevel.INFO,
                    message=f"步骤 {index}",
                    occurred_at=base + timedelta(seconds=index * step_seconds),
                    sequence_no=index + 1,
                ),
            )
        session.commit()


def _logs_url(project_id: object, import_id: object) -> str:
    return f"/api/v1/projects/{project_id}/data-source-imports/{import_id}/logs"


def test_import_progress_is_unmeasured_before_worker_runs(api_db) -> None:
    """新建导入的进度必须是 null（未测量），不是 0。

    迁移 0055 的语义：NULL = 没测量过。前端据此显示"未测量"；把 0 当作分母
    会渲染成"已完成 0%"，把"没记录"伪装成"测量过且没进展"。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    job = _create_import_for_logs(client, ids.project_a)

    assert job["stage"] is None, job
    assert job["processed_items"] is None, job
    assert job["total_items"] is None, job

    detail = client.get(f"/api/v1/projects/{ids.project_a}/data-source-imports/{job['import_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["stage"] is None

    listed = client.get(f"/api/v1/projects/{ids.project_a}/data-source-imports")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["items"] if item["import_id"] == job["import_id"])
    assert row["processed_items"] is None, row
    assert row["total_items"] is None, row

    logs = client.get(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{job['import_id']}/logs"
    )
    assert logs.status_code == 200, logs.text
    assert logs.json() == {"items": [], "truncated": False, "next_cursor": None}, logs.json()


def test_import_logs_cursor_pagination_round_trip(api_db) -> None:
    """游标往返：不带 cursor 取最近一页，带 cursor 继续向更早翻，顺序一致。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    job = _create_import_for_logs(client, ids.project_a)
    import_id = UUID(job["import_id"])
    _seed_import_logs(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        import_id=import_id,
        count=5,
    )
    url = _logs_url(ids.project_a, import_id)

    first = client.get(url, params={"limit": 2})
    assert first.status_code == 200, first.text
    first_body = first.json()
    # 最近两条仍按时间正序返回；truncated 表示"还有更早的"。
    assert [item["message"] for item in first_body["items"]] == ["步骤 3", "步骤 4"]
    assert first_body["truncated"] is True
    first_cursor = first_body["next_cursor"]
    assert isinstance(first_cursor, str) and first_cursor

    second = client.get(url, params={"limit": 2, "cursor": first_cursor})
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert [item["message"] for item in second_body["items"]] == ["步骤 1", "步骤 2"]
    assert second_body["truncated"] is True
    second_cursor = second_body["next_cursor"]
    assert isinstance(second_cursor, str) and second_cursor != first_cursor

    last = client.get(url, params={"limit": 2, "cursor": second_cursor})
    assert last.status_code == 200, last.text
    assert [item["message"] for item in last.json()["items"]] == ["步骤 0"]
    assert last.json()["truncated"] is False
    assert last.json()["next_cursor"] is None


def test_import_logs_exactly_one_page_has_no_cursor(api_db) -> None:
    """刚好整页不是截断：没有更早日志时 next_cursor 必须为 null。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    job = _create_import_for_logs(client, ids.project_a)
    import_id = UUID(job["import_id"])
    _seed_import_logs(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        import_id=import_id,
        count=2,
    )

    response = client.get(_logs_url(ids.project_a, import_id), params={"limit": 2})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["message"] for item in body["items"]] == ["步骤 0", "步骤 1"]
    assert body["truncated"] is False
    assert body["next_cursor"] is None


def test_import_logs_cursor_paginates_same_timestamp_without_gaps(api_db) -> None:
    """时间戳相同的日志依靠序号形成全序，翻页不得丢行或重复。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    job = _create_import_for_logs(client, ids.project_a)
    import_id = UUID(job["import_id"])
    _seed_import_logs(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        import_id=import_id,
        count=3,
        step_seconds=0,
    )
    url = _logs_url(ids.project_a, import_id)

    first = client.get(url, params={"limit": 2}).json()
    assert [item["message"] for item in first["items"]] == ["步骤 1", "步骤 2"]
    second = client.get(url, params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert [item["message"] for item in second["items"]] == ["步骤 0"]
    assert second["truncated"] is False


def test_import_logs_reject_malformed_cursor(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    job = _create_import_for_logs(client, ids.project_a)

    response = client.get(
        _logs_url(ids.project_a, job["import_id"]), params={"cursor": "not-a-cursor"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_import_logs_cursor_from_elsewhere_returns_empty_page(api_db) -> None:
    """合法格式但不属于本次导入的游标返回空页，不泄露其他导入的日志。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    job = _create_import_for_logs(client, ids.project_a)

    response = client.get(
        _logs_url(ids.project_a, job["import_id"]),
        params={"cursor": encode_cursor(uuid4())},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "truncated": False, "next_cursor": None}


def test_import_logs_are_project_scoped_and_missing_import_is_404(api_db) -> None:
    """日志读取沿用项目隔离：跨项目/不存在一律 4xx，不靠空列表泄露存在性。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    outsider = make_client(factory, ids.outsider)
    job = _create_import_for_logs(editor, ids.project_a)

    outsider_response = outsider.get(
        f"/api/v1/projects/{ids.project_a}/data-source-imports/{job['import_id']}/logs"
    )
    assert outsider_response.status_code in {403, 404}, outsider_response.text

    # 同一 import_id 放到另一个项目路径下：同样不可读。
    cross_project = editor.get(
        f"/api/v1/projects/{ids.project_b}/data-source-imports/{job['import_id']}/logs"
    )
    assert cross_project.status_code in {403, 404}, cross_project.text

    missing = editor.get(f"/api/v1/projects/{ids.project_a}/data-source-imports/{uuid4()}/logs")
    assert missing.status_code == 404, missing.text


# ------------------------------------------------ 许可门禁（V61-023）


def _import_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "scope": "SAMPLE",
        "tool_name": "huggingface_hub",
        "tool_version": "0.27.1",
        "sample_limit": 5,
    }
    base.update(overrides)
    return base


def _register_source(client, project_id: object, **overrides: object) -> dict:
    created = client.post(_url(project_id), json=_payload(**overrides))
    assert created.status_code == 201, created.text
    return created.json()


def _post_import(client, project_id: object, source_id: str, **overrides: object):
    return client.post(
        _url(project_id, f"/{source_id}/imports"), json=_import_payload(**overrides)
    )


def test_gated_source_import_requires_licence_acknowledgement(api_db) -> None:
    """GATED 数据源未确认 → 422，且不落半成品导入记录。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = _register_source(
        client, ids.project_a, license="Apache-2.0", access_condition="GATED", is_gated=True
    )

    response = _post_import(client, ids.project_a, source["data_source_id"])

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    detail = client.get(_url(ids.project_a, f"/{source['data_source_id']}/detail")).json()
    assert detail["imports"] == []


def test_gated_source_import_succeeds_after_acknowledgement(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = _register_source(
        client, ids.project_a, license="Apache-2.0", access_condition="GATED", is_gated=True
    )

    response = _post_import(
        client, ids.project_a, source["data_source_id"], license_acknowledged=True
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PENDING"


def test_restricted_license_requires_acknowledgement(api_db) -> None:
    """CC-BY-NC 属非宽松许可：即使非 GATED 也必须确认。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = _register_source(client, ids.project_a, license="cc-by-nc-4.0")

    rejected = _post_import(client, ids.project_a, source["data_source_id"])
    accepted = _post_import(
        client, ids.project_a, source["data_source_id"], license_acknowledged=True
    )

    assert rejected.status_code == 422, rejected.text
    assert accepted.status_code == 201, accepted.text


def test_permissive_license_import_needs_no_acknowledgement(api_db) -> None:
    """Apache-2.0 宽松许可：不传 license_acknowledged 也能导入（默认值不误伤）。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    source = _register_source(client, ids.project_a, license="Apache-2.0")

    response = _post_import(client, ids.project_a, source["data_source_id"])

    assert response.status_code == 201, response.text


def test_licence_gate_is_project_scoped(api_db) -> None:
    """项目隔离：项目 A 的 GATED 数据源不能从项目 B 的路径导入（跨项目不可达）。"""
    factory, ids = api_db
    editor_a = make_client(factory, ids.editor_a)
    source = _register_source(
        editor_a, ids.project_a, license="cc-by-nc-4.0", access_condition="GATED", is_gated=True
    )

    # 同一身份在项目 B 的路径下既无写权限、也看不到项目 A 的数据源。
    response = _post_import(
        editor_a, ids.project_b, source["data_source_id"], license_acknowledged=True
    )

    assert response.status_code in {403, 404}, response.text
