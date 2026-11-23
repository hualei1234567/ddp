"""Manifest 规范化校验与安全红线测试（FR-006 + ADR-0047 日志脱敏）。"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from edge_agent.errors import ManifestError
from edge_agent.manifest.builder import build_manifest, load_manifest, verify_manifest_document
from edge_agent.manifest.models import (
    ArtifactInfo,
    CalibrationRef,
    ClockInfo,
    ManifestWarning,
    StreamInfo,
)
from edge_agent.redaction import REDACTED, SecretRedactingFilter, redact_mapping
from tests.conftest import make_manifest_context


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_manifest_deterministic_hash_and_required_fields() -> None:
    d1, d2 = str(uuid.uuid4()), str(uuid.uuid4())
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    tz_plus8 = timezone(timedelta(hours=8))

    stream = StreamInfo(stream_id="cam_rgb", kind="video", sensor_id=s1, artifact_paths=["a.mp4"])
    ctx_a = make_manifest_context(
        device_ids=[d1, d2, d1],
        sensor_ids=[s2, s1],
        calibrations=[CalibrationRef(c2, "2"), CalibrationRef(c1, "1")],
        start_time=datetime(2026, 11, 23, 19, 55, 0, tzinfo=tz_plus8),
        end_time=datetime(2026, 11, 23, 20, 0, 0, tzinfo=tz_plus8),
        streams=[stream],
    )
    ctx_b = make_manifest_context(
        capture_session_id=ctx_a.capture_session_id,
        collection_task_id=ctx_a.collection_task_id,
        project_id=ctx_a.project_id,
        robot_id=ctx_a.robot_id,
        device_ids=[d2, d1],
        sensor_ids=[s1, s2],
        calibrations=[CalibrationRef(c1, "1"), CalibrationRef(c2, "2")],
        start_time=ctx_a.start_time.astimezone(UTC),  # type: ignore[union-attr]
        end_time=ctx_a.end_time.astimezone(UTC),  # type: ignore[union-attr]
        streams=[stream],
    )
    art_a = ArtifactInfo(
        path="a.mp4",
        mime_type="video/mp4",
        size_bytes=123,
        sha256=_sha(b"video"),
        format="MP4",
        duration_ms=5000,
    )
    art_b = ArtifactInfo(
        path="b.mcap",
        mime_type="application/octet-stream",
        size_bytes=456,
        sha256=_sha(b"mcap"),
        format="MCAP",
    )
    warn = ManifestWarning(code="LOW_FPS", message="frame drop", artifact_path="a.mp4")

    m1 = build_manifest(ctx_a, [art_b, art_a], warnings=[warn])
    m2 = build_manifest(ctx_b, [art_a, art_b], warnings=[warn])

    assert m1.manifest_hash == m2.manifest_hash
    assert m1.canonical_bytes == m2.canonical_bytes
    doc = m1.document
    for required_key in (
        "schema_version",
        "agent",
        "capture_session_id",
        "collection_task_id",
        "project_id",
        "robot_id",
        "device_ids",
        "sensor_ids",
        "calibrations",
        "start_time",
        "end_time",
        "clock",
        "adapter",
        "streams",
        "artifacts",
        "warnings",
        "manifest_hash",
    ):
        assert required_key in doc

    reloaded = load_manifest(m1.canonical_bytes)
    assert reloaded.manifest_hash == m1.manifest_hash


@pytest.mark.parametrize(
    "bad_sync",
    [
        {"agent_token": "tok_live_1234567890"},
        {"pairing_credential": "pair-secret"},
        {"verified": True},
        {"verification_status": "PASSED"},
        {"note": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OCJ9.sig"},
        {"url": "https://s3.invalid/obj?X-Amz-Signature=deadbeef"},
        {"offset_ms": 1.25},  # 禁止浮点数
    ],
)
def test_manifest_rejects_secrets_self_verified_and_floats(bad_sync: dict[str, object]) -> None:
    ctx = make_manifest_context(clock=ClockInfo(source="ntp", unit="ms", sync=bad_sync))
    art = ArtifactInfo(
        path="episode.mcap",
        mime_type="application/octet-stream",
        size_bytes=64,
        sha256=_sha(b"x" * 64),
        format="MCAP",
    )
    with pytest.raises(ManifestError):
        build_manifest(ctx, [art])


def test_manifest_detects_tampering_after_build() -> None:
    ctx = make_manifest_context()
    art = ArtifactInfo(
        path="episode.mcap",
        mime_type="application/octet-stream",
        size_bytes=16,
        sha256=_sha(b"0123456789abcdef"),
        format="MCAP",
    )
    manifest = build_manifest(ctx, [art])
    tampered = dict(manifest.document)
    tampered["warnings"] = [{"code": "INJECTED", "message": "tampered", "artifact_path": None}]
    with pytest.raises(ManifestError, match="manifest_hash"):
        verify_manifest_document(tampered)


def test_secret_redacting_filter_strips_tokens_and_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pairing_code = "PAIR-9988-SECRET-CODE"
    agent_token = "ddp_agent_tok_abcdef1234567890"
    jwt_val = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudC0xIn0.signaturepart"

    logger = logging.getLogger("edge_agent.test_redaction")
    filt = SecretRedactingFilter(known_secrets=[pairing_code, agent_token])
    logger.addFilter(filt)
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            logger.info(
                "pair=%s auth=Bearer %s jwt=%s url=https://s3.test/p?X-Amz-Signature=abc123",
                pairing_code,
                agent_token,
                jwt_val,
            )
    finally:
        logger.removeFilter(filt)

    output = caplog.text
    assert pairing_code not in output
    assert agent_token not in output
    assert jwt_val not in output
    assert "abc123" not in output
    assert REDACTED in output

    cleaned = redact_mapping(
        {"agent_id": "a1", "agent_token": agent_token, "nested": {"pairing_code": pairing_code}},
        known_secrets=[agent_token, pairing_code],
    )
    assert cleaned["agent_id"] == "a1"
    assert cleaned["agent_token"] == REDACTED
    assert cleaned["nested"]["pairing_code"] == REDACTED
