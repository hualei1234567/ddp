"""RawAsset 内容校验与状态动作的领域单元测试。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.ingestion.content_validation import (
    ContentDigest,
    evaluate_content,
    object_missing_decision,
)
from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetQuarantineCode,
    RawAssetSourceType,
    RawAssetStatus,
)
from app.domain.ingestion.errors import DomainValidationError, InvalidStateTransitionError
from app.domain.ingestion.models import RawAsset
from app.domain.ingestion.validation import build_raw_object_key

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CONTENT = b"raw-asset-content"


def _digest(content: bytes) -> ContentDigest:
    return ContentDigest(
        sha256=f"sha256:{hashlib.sha256(content).hexdigest()}",
        size_bytes=len(content),
    )


def _asset(project_id: UUID, user_id: UUID) -> RawAsset:
    asset_id = uuid4()
    digest = _digest(CONTENT)
    return RawAsset(
        asset_id=asset_id,
        project_id=project_id,
        code=f"RAW-{asset_id.hex[:20]}",
        source_type=RawAssetSourceType.UPLOAD,
        original_filename="robot.mcap",
        object_key=build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP),
        size_bytes=digest.size_bytes,
        content_type="application/x-mcap",
        format=RawAssetFormat.MCAP,
        sha256=digest.sha256,
        uploaded_by=user_id,
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        created_by=user_id,
        updated_by=user_id,
    )


def test_content_evaluation_accepts_matching_digest() -> None:
    digest = _digest(CONTENT)
    decision = evaluate_content(
        expected_sha256=digest.sha256,
        expected_size_bytes=digest.size_bytes,
        actual=digest,
    )

    assert decision.valid is True
    assert decision.codes == ()
    assert decision.reason is None


def test_content_evaluation_collects_size_and_hash_codes() -> None:
    expected = _digest(CONTENT)
    actual = _digest(b"short")

    decision = evaluate_content(
        expected_sha256=expected.sha256,
        expected_size_bytes=expected.size_bytes,
        actual=actual,
    )

    assert decision.valid is False
    assert decision.codes == (
        RawAssetQuarantineCode.SIZE_MISMATCH,
        RawAssetQuarantineCode.HASH_MISMATCH,
    )
    assert decision.reason is not None


def test_content_digest_rejects_invalid_sha_and_size() -> None:
    with pytest.raises(DomainValidationError):
        ContentDigest(sha256="not-sha", size_bytes=1)
    with pytest.raises(DomainValidationError):
        ContentDigest(sha256=f"sha256:{'a' * 64}", size_bytes=-1)


def test_raw_asset_validation_actions_are_explicit() -> None:
    project_id, user_id = uuid4(), uuid4()
    asset = _asset(project_id, user_id)

    valid = asset.mark_valid(now=NOW)
    assert valid.status is RawAssetStatus.VALID
    assert valid.verified_at == NOW

    quarantined = asset.mark_quarantined(
        codes=[RawAssetQuarantineCode.HASH_MISMATCH],
        reason="Hash 不一致",
        now=NOW,
    )
    assert quarantined.status is RawAssetStatus.QUARANTINED
    assert quarantined.quarantine_codes == ("hash_mismatch",)
    assert quarantined.quarantine_reason == "Hash 不一致"
    assert quarantined.verified_at is None

    with pytest.raises(DomainValidationError):
        asset.mark_quarantined(codes=[], reason="空", now=NOW)
    with pytest.raises(InvalidStateTransitionError):
        valid.mark_valid(now=NOW)


def test_object_missing_decision_is_stable() -> None:
    decision = object_missing_decision()

    assert decision.valid is False
    assert decision.codes == (RawAssetQuarantineCode.OBJECT_MISSING,)
