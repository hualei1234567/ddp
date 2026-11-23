"""Edge Agent API 集成测试：配对、鉴权隔离、心跳、Lease 互斥/续租/强回收审计、撤销（V31-011）。"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from app.core.config import Settings, get_settings
from app.infrastructure.db.models.capture_session import CaptureSessionModel
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.devices import Device, Robot
from app.infrastructure.db.models.edge_agents import (
    AgentTaskLeaseModel,
    EdgeAgentModel,
    EdgeAgentPairingTokenModel,
)
from app.infrastructure.db.session import get_session
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds
from tests.support_clients import managed_client

_TEST_JWT_SECRET = "edge-agent-test-jwt-secret-must-be-at-least-32-chars"


def _make_jwt(user_id: UUID, role: str = "member") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "platform_role": role,
            "iss": "ddp",
            "aud": "ddp-api",
            "iat": now,
            "exp": now + 3600,
        },
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_robot_device_session(
    factory: sessionmaker[Session],
    ids: ApiIds,
) -> tuple[UUID, UUID, UUID]:
    robot_id, device_id, session_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with factory() as db:
        db.add(
            Robot(
                id=robot_id,
                project_id=ids.project_a,
                code=f"rob-{robot_id.hex[:6]}",
                name="Test Robot",
                robot_type="HUMANOID",
                status="ACTIVE",
                version=1,
                created_at=now,
                updated_at=now,
                created_by=ids.owner_a,
                updated_by=ids.owner_a,
            )
        )
        db.flush()
        db.add(
            Device(
                id=device_id,
                project_id=ids.project_a,
                robot_id=robot_id,
                code=f"dev-{device_id.hex[:6]}",
                name="Test Camera",
                device_type="CAMERA_RIG",
                status="ACTIVE",
                version=1,
                created_at=now,
                updated_at=now,
                created_by=ids.owner_a,
                updated_by=ids.owner_a,
            )
        )
        db.add(
            CaptureSessionModel(
                id=session_id,
                project_id=ids.project_a,
                code=f"cs-{session_id.hex[:6]}",
                robot_id=robot_id,
                status="SCHEDULED",
                version=1,
                created_at=now,
                updated_at=now,
                created_by=ids.owner_a,
                updated_by=ids.owner_a,
            )
        )
        db.commit()
    return robot_id, device_id, session_id


@pytest.fixture()
def edge_env(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[TestClient, sessionmaker[Session], ApiIds]:
    factory, ids = api_db
    test_settings = Settings(
        env="local",
        jwt_secret=_TEST_JWT_SECRET,
        rate_limit_enabled=False,
    )
    app = create_app()

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = managed_client(app)
    return client, factory, ids


def _issue_and_pair(
    client: TestClient,
    ids: ApiIds,
    robot_id: UUID,
    *,
    display_name: str = "ws-01",
    device_ids: list[UUID] | None = None,
) -> tuple[str, str]:
    owner_jwt = _make_jwt(ids.owner_a)
    issued = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/pairing-tokens",
        headers=_auth(owner_jwt),
        json={
            "robot_id": str(robot_id),
            "display_name": display_name,
            "device_ids": [str(item) for item in (device_ids or [])],
        },
    )
    assert issued.status_code == 201, issued.text
    pair_token = issued.json()["pairing_token"]

    paired = client.post(
        "/api/v1/edge-agents/pair",
        json={
            "pairing_token": pair_token,
            "agent_version": "0.1.0",
            "os": "linux",
            "arch": "amd64",
        },
    )
    assert paired.status_code == 201, paired.text
    data = paired.json()
    return data["agent_id"], data["agent_token"]


def _send_heartbeat(
    client: TestClient,
    agent_id: str,
    agent_token: str,
    *,
    reported_at: datetime | None = None,
    session_id: UUID | None = None,
    last_error: str | None = None,
) -> dict[str, object]:
    resp = client.post(
        f"/api/v1/edge-agents/{agent_id}/heartbeat",
        headers=_auth(agent_token),
        json={
            "reported_at": (reported_at or datetime.now(UTC)).isoformat(),
            "version": "0.1.0",
            "os": "linux",
            "arch": "amd64",
            "adapter_versions": {"filesystem": "1.0.0"},
            "disk_free_bytes": 10_000_000,
            "network_reachable": True,
            "current_session_id": str(session_id) if session_id else None,
            "spool_pending_count": 2,
            "spool_pending_bytes": 4096,
            "upload_queue_depth": 1,
            "last_error": last_error,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pairing_single_use_expiry_and_no_plaintext_stored(
    edge_env: tuple[TestClient, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = edge_env
    robot_id, device_id, _ = _seed_robot_device_session(factory, ids)
    owner_jwt = _make_jwt(ids.owner_a)

    # RBAC: editor & viewer cannot issue pairing tokens
    for user_id in (ids.editor_a, ids.viewer_a):
        denied = client.post(
            f"/api/v1/projects/{ids.project_a}/edge-agents/pairing-tokens",
            headers=_auth(_make_jwt(user_id)),
            json={"robot_id": str(robot_id), "display_name": "ws-denied"},
        )
        assert denied.status_code == 403

    issued = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/pairing-tokens",
        headers=_auth(owner_jwt),
        json={
            "robot_id": str(robot_id),
            "display_name": "ws-01",
            "device_ids": [str(device_id)],
        },
    )
    assert issued.status_code == 201
    pair_token = issued.json()["pairing_token"]
    assert pair_token.startswith("ddp_pair_")

    # Plaintext pairing token must never be stored in DB
    with factory() as db:
        row = db.scalar(select(EdgeAgentPairingTokenModel))
        assert row is not None
        assert row.token_hash != pair_token
        assert len(row.token_hash) == 64

    first_pair = client.post("/api/v1/edge-agents/pair", json={"pairing_token": pair_token})
    assert first_pair.status_code == 201
    agent_token = first_pair.json()["agent_token"]
    assert agent_token.startswith("ddp_agt_")

    # Plaintext agent token must never be stored in DB
    with factory() as db:
        agent_row = db.scalar(select(EdgeAgentModel))
        assert agent_row is not None
        assert agent_row.token_hash != agent_token

    # Single-use: second redemption must fail with 401
    second_pair = client.post("/api/v1/edge-agents/pair", json={"pairing_token": pair_token})
    assert second_pair.status_code == 401

    # Expired pairing token must fail with 401
    issued_exp = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/pairing-tokens",
        headers=_auth(owner_jwt),
        json={"robot_id": str(robot_id), "display_name": "ws-exp", "ttl_seconds": 60},
    )
    assert issued_exp.status_code == 201
    exp_token = issued_exp.json()["pairing_token"]
    exp_id = UUID(issued_exp.json()["token_id"])
    with factory() as db:
        row = db.get(EdgeAgentPairingTokenModel, exp_id)
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(seconds=120)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=10)
        db.commit()
    expired_pair = client.post("/api/v1/edge-agents/pair", json={"pairing_token": exp_token})
    assert expired_pair.status_code == 401


def test_token_isolation_between_user_jwt_and_agent_token(
    edge_env: tuple[TestClient, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = edge_env
    robot_id, _, _ = _seed_robot_device_session(factory, ids)
    agent_a, token_a = _issue_and_pair(client, ids, robot_id, display_name="agent-a")
    agent_b, _ = _issue_and_pair(client, ids, robot_id, display_name="agent-b")
    owner_jwt = _make_jwt(ids.owner_a)

    # User JWT cannot call Agent endpoints
    resp_user_on_agent = client.get(
        f"/api/v1/edge-agents/{agent_a}/assignments", headers=_auth(owner_jwt)
    )
    assert resp_user_on_agent.status_code == 401
    # Agent token cannot call User admin endpoints
    assert (
        client.get(
            f"/api/v1/projects/{ids.project_a}/edge-agents", headers=_auth(token_a)
        ).status_code
        == 401
    )
    # Agent A cannot access Agent B's resources
    assert (
        client.get(f"/api/v1/edge-agents/{agent_b}/assignments", headers=_auth(token_a)).status_code
        == 403
    )


def test_heartbeat_connectivity_and_dispatch_pause(
    edge_env: tuple[TestClient, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = edge_env
    robot_id, device_id, session_id = _seed_robot_device_session(factory, ids)
    agent_id, token = _issue_and_pair(client, ids, robot_id, device_ids=[device_id])
    owner_jwt = _make_jwt(ids.owner_a)
    viewer_jwt = _make_jwt(ids.viewer_a)

    # Before heartbeat: PAIRED + OFFLINE
    detail = client.get(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_id}",
        headers=_auth(viewer_jwt),
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "PAIRED"
    assert detail.json()["connectivity"] == "OFFLINE"

    # First heartbeat promotes to ACTIVE + ONLINE; duplicate reported_at is idempotent
    ts = datetime.now(UTC)
    hb1 = _send_heartbeat(client, agent_id, token, reported_at=ts, last_error="disk_warn")
    assert hb1["status"] == "ACTIVE"
    hb2 = _send_heartbeat(client, agent_id, token, reported_at=ts, last_error="disk_warn")
    assert hb2["status"] == "ACTIVE"

    listed = client.get(
        f"/api/v1/projects/{ids.project_a}/edge-agents",
        headers=_auth(viewer_jwt),
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["connectivity"] == "ONLINE"
    assert item["latest_heartbeat"]["spool_pending_count"] == 2
    assert item["latest_heartbeat"]["last_error"] == "disk_warn"

    # Simulate delayed (45s ago) and offline (120s ago)
    with factory() as db:
        row = db.get(EdgeAgentModel, UUID(agent_id))
        assert row is not None
        row.last_seen_at = datetime.now(UTC) - timedelta(seconds=45)
        db.commit()
    delayed = client.get(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_id}",
        headers=_auth(viewer_jwt),
    )
    assert delayed.json()["connectivity"] == "DELAYED"

    with factory() as db:
        row = db.get(EdgeAgentModel, UUID(agent_id))
        assert row is not None
        row.last_seen_at = datetime.now(UTC) - timedelta(seconds=120)
        db.commit()
    offline = client.get(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_id}",
        headers=_auth(viewer_jwt),
    )
    assert offline.json()["connectivity"] == "OFFLINE"

    # Assignments visible before pause
    assigns = client.get(f"/api/v1/edge-agents/{agent_id}/assignments", headers=_auth(token))
    assert assigns.status_code == 200
    assert len(assigns.json()["items"]) == 1
    assert assigns.json()["items"][0]["capture_session_id"] == str(session_id)

    # Pause dispatch -> assignments empty & lease acquire rejected (409)
    pause_resp = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_id}/dispatch",
        headers=_auth(owner_jwt),
        json={"paused": True, "reason": "maintenance window"},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["dispatch_paused"] is True

    paused_assigns = client.get(f"/api/v1/edge-agents/{agent_id}/assignments", headers=_auth(token))
    assert paused_assigns.status_code == 200
    assert paused_assigns.json()["items"] == []
    assert paused_assigns.json()["dispatch_paused"] is True

    blocked_lease = client.post(
        f"/api/v1/edge-agents/{agent_id}/leases",
        headers=_auth(token),
        json={"action": "acquire", "capture_session_id": str(session_id)},
    )
    assert blocked_lease.status_code == 409


def test_lease_exclusivity_renewal_expiry_reclaim_audit_and_revoke(
    edge_env: tuple[TestClient, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = edge_env
    robot_id, device_id, session_id = _seed_robot_device_session(factory, ids)
    agent_a, token_a = _issue_and_pair(
        client, ids, robot_id, display_name="agent-a", device_ids=[device_id]
    )
    agent_b, token_b = _issue_and_pair(
        client, ids, robot_id, display_name="agent-b", device_ids=[device_id]
    )
    owner_jwt = _make_jwt(ids.owner_a)

    _send_heartbeat(client, agent_a, token_a)
    _send_heartbeat(client, agent_b, token_b)

    # Agent A acquires lease
    lease_resp = client.post(
        f"/api/v1/edge-agents/{agent_a}/leases",
        headers=_auth(token_a),
        json={"action": "acquire", "capture_session_id": str(session_id)},
    )
    assert lease_resp.status_code == 200, lease_resp.text
    lease_a = lease_resp.json()
    assert lease_a["status"] == "ACTIVE"

    # Agent B trying to acquire same session -> 409 conflict
    conflict = client.post(
        f"/api/v1/edge-agents/{agent_b}/leases",
        headers=_auth(token_b),
        json={"action": "acquire", "capture_session_id": str(session_id)},
    )
    assert conflict.status_code == 409

    # Heartbeat renews active lease
    hb_renew = _send_heartbeat(
        client, agent_a, token_a, reported_at=datetime.now(UTC) + timedelta(seconds=1)
    )
    assert len(hb_renew["renewed_leases"]) == 1
    assert hb_renew["renewed_leases"][0]["lease_id"] == lease_a["lease_id"]

    # Admin force-reclaim requires reason and writes AuditLog
    bad_reclaim = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_a}/leases/{lease_a['lease_id']}/reclaim",
        headers=_auth(owner_jwt),
        json={"reason": "   "},
    )
    assert bad_reclaim.status_code == 422

    reclaim = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_a}/leases/{lease_a['lease_id']}/reclaim",
        headers=_auth(owner_jwt),
        json={"reason": "workstation frozen"},
    )
    assert reclaim.status_code == 200
    assert reclaim.json()["release_reason"] == "ADMIN_RECLAIMED"

    with factory() as db:
        audit_row = db.scalar(
            select(AuditLog).where(
                AuditLog.resource_type == "AGENT_TASK_LEASE",
                AuditLog.resource_id == UUID(lease_a["lease_id"]),
                AuditLog.action == "reclaim",
            )
        )
        assert audit_row is not None
        assert audit_row.reason == "workstation frozen"

    # Now Agent B can acquire lease on the same session
    lease_b_resp = client.post(
        f"/api/v1/edge-agents/{agent_b}/leases",
        headers=_auth(token_b),
        json={"action": "acquire", "capture_session_id": str(session_id)},
    )
    assert lease_b_resp.status_code == 200
    lease_b_id = lease_b_resp.json()["lease_id"]

    # Token rotation invalidates old token and activates new token
    rot = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_b}/rotate-token",
        headers=_auth(owner_jwt),
    )
    assert rot.status_code == 200
    new_token_b = rot.json()["agent_token"]
    assert (
        client.get(f"/api/v1/edge-agents/{agent_b}/assignments", headers=_auth(token_b)).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/v1/edge-agents/{agent_b}/assignments", headers=_auth(new_token_b)
        ).status_code
        == 200
    )

    # Revoking Agent B immediately releases its active lease and invalidates its token
    rev = client.post(
        f"/api/v1/projects/{ids.project_a}/edge-agents/{agent_b}/revoke",
        headers=_auth(owner_jwt),
        json={"reason": "compromised workstation"},
    )
    assert rev.status_code == 200
    assert rev.json()["status"] == "REVOKED"
    assert (
        client.get(
            f"/api/v1/edge-agents/{agent_b}/assignments", headers=_auth(new_token_b)
        ).status_code
        == 401
    )
    with factory() as db:
        lease_row = db.get(AgentTaskLeaseModel, UUID(lease_b_id))
        assert lease_row is not None
        assert lease_row.released_at is not None
        assert lease_row.release_reason == "AGENT_REVOKED"


def test_expired_lease_not_renewed_and_agent_release_validation(
    edge_env: tuple[TestClient, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = edge_env
    robot_id, device_id, session_id = _seed_robot_device_session(factory, ids)
    agent_a, token_a = _issue_and_pair(
        client, ids, robot_id, display_name="agent-exp-a", device_ids=[device_id]
    )
    agent_b, token_b = _issue_and_pair(
        client, ids, robot_id, display_name="agent-exp-b", device_ids=[device_id]
    )
    _send_heartbeat(client, agent_a, token_a)
    _send_heartbeat(client, agent_b, token_b)

    lease_a = client.post(
        f"/api/v1/edge-agents/{agent_a}/leases",
        headers=_auth(token_a),
        json={"action": "acquire", "capture_session_id": str(session_id)},
    ).json()

    # Expire lease A in DB (keeping expires_at > acquired_at)
    with factory() as db:
        row = db.get(AgentTaskLeaseModel, UUID(lease_a["lease_id"]))
        assert row is not None
        row.acquired_at = datetime.now(UTC) - timedelta(seconds=300)
        row.last_renewed_at = datetime.now(UTC) - timedelta(seconds=300)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=10)
        db.commit()

    # Heartbeat must NOT renew expired lease and reports it in expired_lease_ids
    hb = _send_heartbeat(
        client, agent_a, token_a, reported_at=datetime.now(UTC) + timedelta(seconds=5)
    )
    assert hb["renewed_leases"] == []
    assert lease_a["lease_id"] in hb["expired_lease_ids"]

    # Agent B can now acquire lease (auto-reclaiming expired lease A)
    lease_b = client.post(
        f"/api/v1/edge-agents/{agent_b}/leases",
        headers=_auth(token_b),
        json={"action": "acquire", "capture_session_id": str(session_id)},
    )
    assert lease_b.status_code == 200
    lease_b_id = lease_b.json()["lease_id"]

    # Agent cannot release with server-only reason
    bad_rel = client.post(
        f"/api/v1/edge-agents/{agent_b}/leases",
        headers=_auth(token_b),
        json={
            "action": "release",
            "lease_id": lease_b_id,
            "release_reason": "ADMIN_RECLAIMED",
        },
    )
    assert bad_rel.status_code == 422

    # Agent releases with COMPLETED
    ok_rel = client.post(
        f"/api/v1/edge-agents/{agent_b}/leases",
        headers=_auth(token_b),
        json={
            "action": "release",
            "lease_id": lease_b_id,
            "release_reason": "COMPLETED",
            "release_note": "all episodes spooled",
        },
    )
    assert ok_rel.status_code == 200
    assert ok_rel.json()["status"] == "RELEASED"
    assert ok_rel.json()["release_reason"] == "COMPLETED"
