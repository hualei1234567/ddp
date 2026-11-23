"""Preflight 11 项检查与不可绕过 BLOCKED 门禁测试（FR-004）。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from edge_agent.adapters.filesystem_export import FilesystemExportAdapter
from edge_agent.config import AdapterConfig, ExportSource, PreflightThresholds, SpoolConfig
from edge_agent.errors import PreflightAckError, PreflightBlockedError
from edge_agent.preflight.engine import PreflightEngine, PreflightInput
from edge_agent.preflight.models import CheckStatus
from edge_agent.spool.manager import SourceFileSpec, SpoolManager
from tests.conftest import FakeClock, make_manifest_context

EXPECTED_CHECK_IDS = {
    "identity",
    "agent_version",
    "task_session",
    "devices_sensors",
    "calibration",
    "adapter_health",
    "disk_space",
    "clock_skew",
    "network",
    "spool_writable",
    "previous_session_residue",
}


def _healthy_input(tmp_path: Path, fake_clock: FakeClock) -> PreflightInput:
    export_dir = tmp_path / "exports"
    export_dir.mkdir(exist_ok=True)
    spool_root = tmp_path / "spool"
    spool_root.mkdir(exist_ok=True)
    adapter = FilesystemExportAdapter(
        AdapterConfig(sources=(ExportSource(name="main", root=export_dir),)),
        clock=fake_clock,
    )
    return PreflightInput(
        identity_valid=True,
        agent_version="0.2.0",
        task_status="ACTIVE",
        session_status="READY",
        lease_valid=True,
        devices_available={"arm-01": True},
        sensors_available={"cam-rgb": True},
        calibration_snapshot_id="cal-snap-001",
        calibration_valid=True,
        calibration_expired=False,
        adapter=adapter,
        spool_root=spool_root,
        disk_free_bytes=50 * 1024**3,
        clock_skew_ms=15,
        network_reachable=True,
        network_latency_ms=12,
    )


def test_preflight_all_11_checks_pass(tmp_path: Path, fake_clock: FakeClock) -> None:
    engine = PreflightEngine(PreflightThresholds(min_agent_version="0.1.0"), clock=fake_clock)
    report = engine.run(str(uuid.uuid4()), _healthy_input(tmp_path, fake_clock))
    assert {r.check_id for r in report.results} == EXPECTED_CHECK_IDS
    assert report.overall == CheckStatus.PASS
    assert engine.authorize_start(report) is None


@pytest.mark.parametrize(
    ("field_updates", "expected_blocked"),
    [
        ({"identity_valid": False}, "identity"),
        ({"agent_version": "0.0.9"}, "agent_version"),
        ({"lease_valid": False}, "task_session"),
        ({"session_status": "CLOSED"}, "task_session"),
        ({"devices_available": {"arm-01": False}}, "devices_sensors"),
        ({"calibration_valid": False}, "calibration"),
        ({"adapter": None}, "adapter_health"),
        ({"disk_free_bytes": 100}, "disk_space"),
        ({"clock_skew_ms": 2500}, "clock_skew"),
        ({"network_reachable": False}, "network"),
        ({"spool_writable": False}, "spool_writable"),
    ],
)
def test_preflight_blocked_cannot_be_bypassed(
    tmp_path: Path,
    fake_clock: FakeClock,
    field_updates: dict[str, object],
    expected_blocked: str,
) -> None:
    engine = PreflightEngine(PreflightThresholds(min_agent_version="0.1.0"), clock=fake_clock)
    inp = replace(_healthy_input(tmp_path, fake_clock), **field_updates)  # type: ignore[arg-type]
    report = engine.run(str(uuid.uuid4()), inp)
    assert report.overall == CheckStatus.BLOCKED
    assert expected_blocked in report.ids_with(CheckStatus.BLOCKED)

    # 强制红线：即使传入 allow_warn=True 和所有 check_id，依然必须拒绝启动！
    with pytest.raises(PreflightBlockedError) as exc_info:
        engine.authorize_start(
            report,
            allow_warn=True,
            acknowledged_by="operator-alice",
            acknowledged_check_ids=list(EXPECTED_CHECK_IDS),
        )
    assert expected_blocked in exc_info.value.blocked_check_ids


def test_preflight_warn_requires_ack_and_persists_evidence_with_residue(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    spool_root = tmp_path / "spool"
    mgr = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        src = tmp_path / "prev.mcap"
        src.write_bytes(b"previous-session-unverified")
        mgr.ingest(
            make_manifest_context(capture_session_id=str(uuid.uuid4())),
            [SourceFileSpec(src, "prev.mcap", "application/octet-stream", "MCAP")],
        )

        thresholds = PreflightThresholds(
            min_agent_version="0.1.0", recommended_agent_version="0.3.0"
        )
        engine = PreflightEngine(thresholds, clock=fake_clock)
        inp = replace(
            _healthy_input(tmp_path, fake_clock),
            spool_root=spool_root,
            clock_skew_ms=250,  # WARN
            calibration_expired=True,  # WARN
        )
        current_session = str(uuid.uuid4())
        report = engine.run(current_session, inp, spool_store=mgr.store)
        assert report.overall == CheckStatus.WARN
        warned = set(report.ids_with(CheckStatus.WARN))
        assert warned == {
            "agent_version",
            "calibration",
            "clock_skew",
            "previous_session_residue",
        }

        # 未允许 WARN 或未完整确认必须报错
        with pytest.raises(PreflightAckError):
            engine.authorize_start(report, allow_warn=False)
        with pytest.raises(PreflightAckError):
            engine.authorize_start(
                report, allow_warn=True, acknowledged_by="op", acknowledged_check_ids=["clock_skew"]
            )

        evidence = engine.authorize_start(
            report,
            allow_warn=True,
            acknowledged_by="operator-bob",
            acknowledged_check_ids=sorted(warned),
            persist_store=mgr.store,
        )
        assert evidence is not None
        assert evidence.acknowledged_by == "operator-bob"
        assert evidence.report_hash == report.report_hash()

        stored = mgr.store.list_preflight_evidence(current_session)
        assert len(stored) == 1
        assert stored[0]["evidence_id"] == evidence.evidence_id
    finally:
        mgr.close()
