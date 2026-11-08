"""DatasetGrant 跨项目授权、范围、到期与撤销 API 测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import app.application.datasets.dataset_grants as grant_module
from app.infrastructure.db.models.training import DatasetGrantModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version, export_payload
from tests.api.training_support import add_member

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _grant_payload(dataset_version_id, *, scope: list[str], expires_at: datetime, reason: str):
    return {
        "dataset_version_id": str(dataset_version_id),
        "grantee_project_id": None,
        "grantee_team": "跨项目算法组",
        "scope": scope,
        "reason": reason,
        "expires_at": expires_at.isoformat(),
    }


def _create_grant(client, project_id, dataset_id, payload, *, key: str | None = None):
    body = dict(payload)
    return client.post(
        url(project_id, f"/datasets/{dataset_id}/grants"),
        json=body,
        headers={"Idempotency-Key": key or f"grant-{uuid4().hex[:8]}"},
    )


def test_grant_scope_expiry_revocation_and_cross_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch,
) -> None:
    factory, ids = api_db
    approver_id = add_member(factory, ids.project_a, role="owner")
    grantee_id = add_member(factory, ids.project_b, role="editor")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    approver = make_client(factory, approver_id)
    grantee = make_client(factory, grantee_id)
    ctx = build_frozen_version(factory, ids)
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version_id"]

    assert grantee.get(
        url(ids.project_a, f"/datasets/{dataset_id}")
    ).status_code == 403
    assert grantee.get(url(ids.project_a, "/datasets")).status_code == 403

    payload = _grant_payload(
        version_id,
        scope=["VIEW"],
        expires_at=NOW + timedelta(hours=1),
        reason="联合训练只读",
    )
    payload["grantee_project_id"] = str(ids.project_b)
    forbidden = _create_grant(editor, ids.project_a, dataset_id, payload)
    assert forbidden.status_code == 403

    created = _create_grant(owner, ids.project_a, dataset_id, payload)
    assert created.status_code == 201, created.text
    grant = created.json()
    assert grant["status"] == "PENDING_APPROVAL"
    assert grant["scope"] == ["VIEW"]

    self_approve = owner.post(
        url(
            ids.project_a,
            f"/datasets/{dataset_id}/grants/{grant['grant_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{grant["version"]}"'},
        json={},
    )
    assert self_approve.status_code == 403
    assert self_approve.json()["error"]["code"] == "dataset_grant_forbidden"

    approved = approver.post(
        url(
            ids.project_a,
            f"/datasets/{dataset_id}/grants/{grant['grant_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{grant["version"]}"'},
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ACTIVE"

    assert grantee.get(
        url(ids.project_a, f"/datasets/{dataset_id}")
    ).status_code == 200
    version_detail = grantee.get(
        url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}")
    )
    assert version_detail.status_code == 200
    versions = grantee.get(url(ids.project_a, f"/datasets/{dataset_id}/versions"))
    assert versions.status_code == 200

    export_denied = grantee.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": f"grantee-export-{uuid4().hex[:8]}"},
    )
    assert export_denied.status_code == 403

    export_payload_body = _grant_payload(
        version_id,
        scope=["EXPORT"],
        expires_at=NOW + timedelta(hours=1),
        reason="联合训练导出",
    )
    export_payload_body["grantee_project_id"] = str(ids.project_b)
    export_grant = _create_grant(owner, ids.project_a, dataset_id, export_payload_body)
    assert export_grant.status_code == 201, export_grant.text
    export_grant_body = export_grant.json()
    export_grant_approved = approver.post(
        url(
            ids.project_a,
            f"/datasets/{dataset_id}/grants/{export_grant_body['grant_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{export_grant_body["version"]}"'},
        json={},
    )
    assert export_grant_approved.status_code == 200, export_grant_approved.text

    export_created = grantee.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": f"grantee-export-{uuid4().hex[:8]}"},
    )
    assert export_created.status_code == 202, export_created.text

    revoked = approver.post(
        url(
            ids.project_a,
            f"/datasets/{dataset_id}/grants/{export_grant_body['grant_id']}/actions/revoke",
        ),
        headers={"If-Match": f'"{export_grant_approved.json()["version"]}"'},
        json={"reason": "合作结束"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"

    revoked_export = grantee.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": f"grantee-export-{uuid4().hex[:8]}"},
    )
    assert revoked_export.status_code == 410
    assert revoked_export.json()["error"]["code"] == "dataset_grant_revoked"

    future = NOW + timedelta(hours=2)
    monkeypatch.setattr(grant_module, "_utcnow", lambda: future)
    expired_access = grantee.get(url(ids.project_a, f"/datasets/{dataset_id}"))
    assert expired_access.status_code == 410
    assert expired_access.json()["error"]["code"] == "dataset_grant_expired"

    detail = owner.get(
        url(ids.project_a, f"/datasets/{dataset_id}/grants/{grant['grant_id']}")
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "EXPIRED"

    with factory() as session:
        rows = session.scalars(
            select(DatasetGrantModel).where(
                DatasetGrantModel.dataset_id == UUID(dataset_id)
            )
        ).all()
        assert {row.status for row in rows} <= {"ACTIVE", "REVOKED"}
        assert any(row.status == "REVOKED" for row in rows)


def test_grant_validation_rejects_self_project_and_past_expiry(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    dataset = owner.post(
        url(ids.project_a, "/datasets"),
        json={
            "code": f"grant-ds-{uuid4().hex[:8]}",
            "name": "授权校验数据集",
            "purpose": "TRAINING",
            "owner_id": str(ids.owner_a),
            "usage_policy": "TRAINING_ALLOWED",
        },
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["dataset_id"]

    same_project = _grant_payload(
        None,
        scope=["VIEW"],
        expires_at=NOW + timedelta(hours=1),
        reason="错误授权",
    )
    same_project["grantee_project_id"] = str(ids.project_a)
    response = _create_grant(owner, ids.project_a, dataset_id, same_project)
    assert response.status_code == 422

    past = _grant_payload(
        None,
        scope=["VIEW"],
        expires_at=NOW - timedelta(hours=1),
        reason="已过期",
    )
    past["grantee_project_id"] = str(ids.project_b)
    response = _create_grant(owner, ids.project_a, dataset_id, past)
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "dataset_grant_expired"

    invalid_scope = _grant_payload(
        None,
        scope=["ADMIN"],
        expires_at=NOW + timedelta(hours=1),
        reason="非法范围",
    )
    invalid_scope["grantee_project_id"] = str(ids.project_b)
    response = _create_grant(owner, ids.project_a, dataset_id, invalid_scope)
    assert response.status_code == 422
