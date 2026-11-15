"""两个"存在性已知的范围内拒绝"错误码的产生点 API 测试。

- ``dataset_access_denied``：接收方对该数据集确有显式 Grant，但不含所需动作。
  此时存在性对接收方已知，必须按 code 可分支，而不是伪装成 404；
  "完全没有 Grant" 仍走 403 ``forbidden``，不泄漏存在性。
- ``export_output_conflict``：输出前缀下已有产物时不得静默覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.export_support import (
    FakeExportStorage,
    build_frozen_version,
    export_payload,
    run_export_worker,
)
from tests.api.training_support import add_member

NOW = datetime.now(UTC).replace(microsecond=0)


def _grant(client, project_id, dataset_id, payload, *, key: str):
    return client.post(
        url(project_id, f"/datasets/{dataset_id}/grants"),
        json=payload,
        headers={"Idempotency-Key": key},
    )


def test_grant_without_required_scope_yields_access_denied(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """已授权 VIEW，但跨项目导出需要 EXPORT：断言专用码，而不是 403 forbidden。"""
    factory, ids = api_db
    owner_id = add_member(factory, ids.project_a, role="owner")
    grantee_id = add_member(factory, ids.project_b, role="editor")
    owner = make_client(factory, owner_id)
    approver = make_client(factory, add_member(factory, ids.project_a, role="owner"))
    grantee = make_client(factory, grantee_id)
    ctx = build_frozen_version(factory, ids)
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version_id"]

    created = _grant(
        owner,
        ids.project_a,
        dataset_id,
        {
            "dataset_version_id": str(version_id),
            "grantee_project_id": str(ids.project_b),
            "grantee_team": "只读接收方",
            "scope": ["VIEW"],
            "reason": "只读授权",
            "expires_at": (NOW + timedelta(hours=2)).isoformat(),
        },
        key=f"scope-grant-{uuid4().hex[:8]}",
    )
    assert created.status_code == 201, created.text
    grant = created.json()
    approved = approver.post(
        url(
            ids.project_a,
            f"/datasets/{dataset_id}/grants/{grant['grant_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{grant["version"]}"'},
        json={},
    )
    assert approved.status_code == 200, approved.text

    # VIEW 授权可读数据集。
    assert grantee.get(url(ids.project_a, f"/datasets/{dataset_id}")).status_code == 200
    # 但导出需要 EXPORT 授权：同一 Grant 范围不覆盖，必须给可按 code 分支的 403。
    denied = grantee.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": f"scope-export-{uuid4().hex[:8]}"},
    )
    assert denied.status_code == 403, denied.text
    body = denied.json()["error"]
    assert body["code"] == "dataset_access_denied"
    assert body["details"]["required_scope"] == "EXPORT"
    assert body["details"]["dataset_id"] == str(dataset_id)


def test_no_grant_still_reports_forbidden_not_access_denied(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """完全没有 Grant 时不得暴露 dataset_access_denied（存在性未知）。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    outsider_id = add_member(factory, ids.project_b, role="editor")
    outsider = make_client(factory, outsider_id)

    response = outsider.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": f"no-grant-{uuid4().hex[:8]}"},
    )
    assert response.status_code in {403, 404}
    assert response.json()["error"]["code"] != "dataset_access_denied"


def test_export_worker_refuses_to_overwrite_existing_artifacts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同一输出前缀下已有产物时必须确定性失败，不得静默覆盖。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    client = make_client(factory, ids.editor_a, object_storage=storage)

    created = client.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": f"conflict-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["export_job_id"]
    prefix = created.json()["output_prefix"]

    # 模拟"上次已写出产物"：直接占住本次会写的对象键。
    storage.objects[f"{prefix}meta/info.json"] = b'{"stale": true}'

    result = run_export_worker(factory, storage)
    assert result.status == "PUBLISHED"

    detail = client.get(url(ids.project_a, f"/exports/{job_id}")).json()
    assert detail["status"] == "FAILED"
    assert detail["error_code"] == "export_output_conflict"
    # 旧产物内容未被改写。
    assert storage.objects[f"{prefix}meta/info.json"] == b'{"stale": true}'
