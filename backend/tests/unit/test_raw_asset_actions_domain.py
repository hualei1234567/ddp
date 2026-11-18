"""RawAsset 公开状态动作的领域语义。

这些动作此前只在状态机里有边、没有 HTTP 入口。本文件锁定每条动作的
语义（清空隔离结论、时间戳、原因/隔离码必填）与 ``allowed_actions`` 的
对外集合，避免"能力存在但用户永远点不到"再次出现。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.ingestion.enums import RawAssetFormat, RawAssetSourceType, RawAssetStatus
from app.domain.ingestion.errors import DomainValidationError, InvalidStateTransitionError
from app.domain.ingestion.models import RawAsset
from app.domain.ingestion.validation import build_raw_object_key

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
CONTENT = b"raw-asset-actions"


def _asset(project_id: UUID, user_id: UUID, **overrides: object) -> RawAsset:
    asset_id = uuid4()
    base = RawAsset(
        asset_id=asset_id,
        project_id=project_id,
        code=f"RAW-{asset_id.hex[:20]}",
        source_type=RawAssetSourceType.UPLOAD,
        original_filename="robot.mcap",
        object_key=build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP),
        size_bytes=len(CONTENT),
        content_type="application/x-mcap",
        format=RawAssetFormat.MCAP,
        sha256=f"sha256:{hashlib.sha256(CONTENT).hexdigest()}",
        uploaded_by=user_id,
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        created_by=user_id,
        updated_by=user_id,
    )
    return replace(base, **overrides)


@pytest.fixture()
def ids() -> tuple[UUID, UUID]:
    return uuid4(), uuid4()


def test_allowed_actions_excludes_system_actions(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    valid = _asset(project_id, user_id, status=RawAssetStatus.VALID)
    assert valid.allowed_actions == {
        "archive",
        "post-hoc-quarantine",
        "tombstone",
    }
    validating = _asset(project_id, user_id)
    assert validating.allowed_actions == {"tombstone"}
    quarantined = _asset(
        project_id,
        user_id,
        status=RawAssetStatus.QUARANTINED,
        quarantine_codes=("hash_mismatch",),
        quarantine_reason="内容与声明不一致",
    )
    assert quarantined.allowed_actions == {
        "reject-quarantined",
        "release-quarantine-or-revalidate",
        "tombstone",
    }
    tombstoned = _asset(
        project_id,
        user_id,
        status=RawAssetStatus.TOMBSTONED,
        tombstoned_at=NOW,
        quarantine_reason="来源撤回",
    )
    assert tombstoned.allowed_actions == frozenset()


def test_archive_sets_timestamp_and_is_terminal_for_archive(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    asset = _asset(project_id, user_id, status=RawAssetStatus.VALID)
    archived = asset.apply_action("archive", {}, now=NOW)
    assert archived.status is RawAssetStatus.ARCHIVED
    assert archived.archived_at == NOW
    with pytest.raises(InvalidStateTransitionError):
        archived.apply_action("archive", {}, now=NOW)


def test_release_quarantine_clears_previous_verdict(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    asset = _asset(
        project_id,
        user_id,
        status=RawAssetStatus.QUARANTINED,
        quarantine_codes=("hash_mismatch",),
        quarantine_reason="内容与声明不一致",
    )
    released = asset.apply_action("release-quarantine-or-revalidate", {}, now=NOW)
    assert released.status is RawAssetStatus.VALIDATING
    assert released.quarantine_codes == ()
    assert released.quarantine_reason is None
    assert released.verified_at is None


def test_reject_quarantined_requires_reason(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    asset = _asset(
        project_id,
        user_id,
        status=RawAssetStatus.QUARANTINED,
        quarantine_codes=("hash_mismatch",),
        quarantine_reason="内容与声明不一致",
    )
    with pytest.raises(DomainValidationError):
        asset.apply_action("reject-quarantined", {}, now=NOW)
    rejected = asset.apply_action("reject-quarantined", {"reason": "文件损坏，不再重传"}, now=NOW)
    assert rejected.status is RawAssetStatus.REJECTED
    assert rejected.quarantine_reason == "文件损坏，不再重传"


def test_post_hoc_quarantine_requires_reason_and_codes(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    asset = _asset(project_id, user_id, status=RawAssetStatus.VALID)
    with pytest.raises(DomainValidationError):
        asset.apply_action("post-hoc-quarantine", {"quarantine_codes": ["parse_failed"]}, now=NOW)
    with pytest.raises(DomainValidationError):
        asset.apply_action("post-hoc-quarantine", {"reason": "发现隐私画面"}, now=NOW)
    quarantined = asset.apply_action(
        "post-hoc-quarantine",
        {"reason": "发现隐私画面", "quarantine_codes": ["metadata_conflict"]},
        now=NOW,
    )
    assert quarantined.status is RawAssetStatus.QUARANTINED
    assert quarantined.quarantine_codes == ("metadata_conflict",)


def test_tombstone_requires_reason_and_keeps_lineage_fields(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    asset = _asset(project_id, user_id, status=RawAssetStatus.VALID)
    with pytest.raises(DomainValidationError):
        asset.apply_action("tombstone", {}, now=NOW)
    tombstoned = asset.apply_action("tombstone", {"reason": "来源撤回"}, now=NOW)
    assert tombstoned.status is RawAssetStatus.TOMBSTONED
    assert tombstoned.tombstoned_at == NOW
    # 墓碑只改状态：内容地址与 Hash 必须原样保留，血缘才能追溯。
    assert tombstoned.object_key == asset.object_key
    assert tombstoned.sha256 == asset.sha256


def test_system_actions_are_not_public(ids: tuple[UUID, UUID]) -> None:
    project_id, user_id = ids
    asset = _asset(project_id, user_id)
    for action in ("validate-success", "validate-quarantine", "system-fail"):
        with pytest.raises(DomainValidationError):
            asset.apply_action(action, {}, now=NOW)
