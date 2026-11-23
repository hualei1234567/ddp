"""Edge Agent 领域模型、策略、心跳、Lease 与凭据工具单元测试（V31-011）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.edge_agents import (
    AgentAuthenticationError,
    AgentStatus,
    Connectivity,
    DomainValidationError,
    EdgeAgent,
    EdgeAgentPolicy,
    HeartbeatReport,
    Lease,
    LeaseReleaseReason,
    LeaseStatus,
    PairingToken,
    StateTransitionError,
)
from app.infrastructure.auth.agent_tokens import (
    AGENT_TOKEN_PREFIX,
    PAIRING_TOKEN_PREFIX,
    generate_agent_token,
    generate_pairing_token,
    has_prefix,
    hash_token,
)

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def _policy() -> EdgeAgentPolicy:
    return EdgeAgentPolicy(
        heartbeat_interval_seconds=30,
        offline_after_seconds=90,
        lease_ttl_seconds=120,
        capture_grace_seconds=60,
        pairing_ttl_default_seconds=900,
        pairing_ttl_max_seconds=3600,
        heartbeat_retention_hours=168,
    )


def test_policy_rejects_invalid_relationships() -> None:
    with pytest.raises(DomainValidationError):
        EdgeAgentPolicy(30, 30, 120, 60, 900, 3600, 168)
    with pytest.raises(DomainValidationError):
        EdgeAgentPolicy(30, 90, 120, 60, 4000, 3600, 168)
    with pytest.raises(DomainValidationError):
        EdgeAgentPolicy(0, 90, 120, 60, 900, 3600, 168)


def test_pairing_token_issue_and_redeem_rules() -> None:
    policy = _policy()
    dev_id = uuid4()
    token = PairingToken.issue(
        token_id=uuid4(),
        project_id=uuid4(),
        robot_id=uuid4(),
        display_name="  workstation-01  ",
        created_by=uuid4(),
        now=NOW,
        ttl_seconds=600,
        policy=policy,
        device_ids=(dev_id,),
    )
    assert token.display_name == "workstation-01"
    assert token.device_ids == (dev_id,)
    token.ensure_redeemable(NOW + timedelta(seconds=599))

    with pytest.raises(AgentAuthenticationError):
        token.ensure_redeemable(NOW + timedelta(seconds=600))

    with pytest.raises(DomainValidationError):
        PairingToken.issue(
            token_id=uuid4(),
            project_id=uuid4(),
            robot_id=uuid4(),
            display_name="dup",
            created_by=uuid4(),
            now=NOW,
            ttl_seconds=600,
            policy=policy,
            device_ids=(dev_id, dev_id),
        )


def test_edge_agent_lifecycle_and_connectivity() -> None:
    policy = _policy()
    token = PairingToken.issue(
        token_id=uuid4(),
        project_id=uuid4(),
        robot_id=uuid4(),
        display_name="edge-1",
        created_by=uuid4(),
        now=NOW,
        ttl_seconds=None,
        policy=policy,
    )
    agent = EdgeAgent.from_pairing(token, agent_id=uuid4(), now=NOW)
    assert agent.status is AgentStatus.PAIRED
    assert agent.connectivity(NOW, policy) is Connectivity.OFFLINE

    with pytest.raises(StateTransitionError):
        agent.ensure_can_hold_lease()

    active = agent.record_heartbeat(NOW + timedelta(seconds=5), "0.1.0", os_arch="linux/amd64")
    assert active.status is AgentStatus.ACTIVE
    assert active.os_arch == "linux/amd64"
    assert active.connectivity(NOW + timedelta(seconds=20), policy) is Connectivity.ONLINE
    assert active.connectivity(NOW + timedelta(seconds=50), policy) is Connectivity.DELAYED
    assert active.connectivity(NOW + timedelta(seconds=100), policy) is Connectivity.OFFLINE

    paused = active.set_dispatch_paused(True)
    with pytest.raises(StateTransitionError):
        paused.ensure_can_hold_lease()

    revoked = paused.revoke(NOW + timedelta(seconds=10), actor_id=uuid4(), reason="decommissioned")
    assert revoked.status is AgentStatus.REVOKED
    assert revoked.connectivity(NOW + timedelta(seconds=11), policy) is Connectivity.OFFLINE
    with pytest.raises(StateTransitionError):
        revoked.record_heartbeat(NOW + timedelta(seconds=12), "0.1.0")
    with pytest.raises(StateTransitionError):
        revoked.rotate_token(NOW + timedelta(seconds=12))


def test_heartbeat_report_validation() -> None:
    report = HeartbeatReport(
        reported_at=NOW,
        agent_version="0.1.0",
        os="linux",
        arch="amd64",
        network_reachable=True,
        disk_free_bytes=1024,
        spool_pending_count=0,
        spool_pending_bytes=0,
        upload_queue_depth=0,
        adapter_versions={"filesystem": "1.0.0"},
    )
    assert report.agent_version == "0.1.0"

    with pytest.raises(DomainValidationError):
        HeartbeatReport(
            reported_at=datetime(2026, 11, 23, 12, 0, 0),  # naive datetime
            agent_version="0.1.0",
            os="linux",
            arch="amd64",
            network_reachable=True,
            disk_free_bytes=0,
            spool_pending_count=0,
            spool_pending_bytes=0,
            upload_queue_depth=0,
        )


def test_lease_lifecycle_rules() -> None:
    lease = Lease.acquire(
        lease_id=uuid4(),
        project_id=uuid4(),
        agent_id=uuid4(),
        session_id=uuid4(),
        device_ids=(uuid4(),),
        now=NOW,
        ttl_seconds=60,
    )
    assert lease.status(NOW + timedelta(seconds=30)) is LeaseStatus.ACTIVE
    renewed = lease.renew(NOW + timedelta(seconds=30), ttl_seconds=60)
    assert renewed.expires_at == NOW + timedelta(seconds=90)

    assert renewed.status(NOW + timedelta(seconds=90)) is LeaseStatus.EXPIRED
    with pytest.raises(StateTransitionError):
        renewed.renew(NOW + timedelta(seconds=90), ttl_seconds=60)

    released = renewed.release(
        NOW + timedelta(seconds=95),
        reason=LeaseReleaseReason.ADMIN_RECLAIMED,
        note="operator reassigned",
    )
    assert released.status(NOW + timedelta(seconds=96)) is LeaseStatus.RELEASED
    with pytest.raises(StateTransitionError):
        released.release(NOW + timedelta(seconds=97), reason=LeaseReleaseReason.COMPLETED)


def test_agent_token_generation_and_prefix_isolation() -> None:
    pair_tok = generate_pairing_token()
    agent_tok = generate_agent_token()
    assert has_prefix(pair_tok, PAIRING_TOKEN_PREFIX)
    assert not has_prefix(pair_tok, AGENT_TOKEN_PREFIX)
    assert has_prefix(agent_tok, AGENT_TOKEN_PREFIX)
    assert not has_prefix(agent_tok, PAIRING_TOKEN_PREFIX)
    assert not has_prefix("eyJhbGciOiJIUzI1NiJ9.payload.sig", AGENT_TOKEN_PREFIX)
    assert len(hash_token(agent_tok)) == 64
    assert hash_token(agent_tok) != agent_tok
