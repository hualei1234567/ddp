"""上传/RawAsset 领域规则单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.ingestion.enums import RawAssetFormat, RawAssetStatus, UploadSessionStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    InvalidFilenameError,
    InvalidStateTransitionError,
    ResourceImmutableError,
)
from app.domain.ingestion.models import RawAsset, UploadPart, UploadSession
from app.domain.ingestion.state_machines import (
    assert_upload_session_action,
    raw_asset_target_status,
)
from app.domain.ingestion.validation import (
    build_raw_object_key,
    canonical_content_type,
    detect_format,
    expected_part_size,
    normalize_etag,
    normalize_sha256,
    required_part_count,
    sanitize_filename,
    signature_compatible,
    validate_raw_object_key,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA = f"sha256:{'a' * 64}"


def _session(**overrides: object) -> UploadSession:
    project_id = uuid4()
    asset_id = uuid4()
    values: dict[str, object] = {
        "upload_session_id": uuid4(),
        "project_id": project_id,
        "original_filename": "robot.mcap",
        "declared_content_type": "application/x-mcap",
        "declared_size_bytes": 8,
        "part_size_bytes": 4,
        "max_size_bytes": 64,
        "object_key": build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP),
        "multipart_upload_id": "upload-1",
        "expires_at": NOW + timedelta(hours=1),
        "allowed_content_types": ("application/x-mcap",),
        "format_hint": RawAssetFormat.MCAP,
        "asset_id": asset_id,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return UploadSession(**values)  # type: ignore[arg-type]


def test_filename_sanitization_rejects_paths_and_control_characters() -> None:
    assert sanitize_filename("  robot.mcap  ") == "robot.mcap"
    for value in ("", "..", "../a.mcap", "a/b.mcap", "a\\b.mcap", "a\x00b", "a\nb"):
        with pytest.raises(InvalidFilenameError):
            sanitize_filename(value)


def test_object_key_is_server_generated_and_matches_contract() -> None:
    project_id = uuid4()
    asset_id = uuid4()
    key = build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP)
    assert key == f"raw/{project_id}/2026/11/08/{asset_id}/content.mcap"
    assert validate_raw_object_key(key)
    assert "robot.mcap" not in key
    assert not validate_raw_object_key(f"raw/{project_id}/../evil/content.mcap")
    assert not validate_raw_object_key(f"raw/{project_id}/2026/11/08/{asset_id}/content.MCAP")


def test_content_signature_detection_and_compatibility() -> None:
    assert detect_format(b"\x89MCAP0\r\n") is RawAssetFormat.MCAP
    assert detect_format(b"\x89HDF\r\n\x1a\n") is RawAssetFormat.HDF5
    assert detect_format(b"SQLite format 3\x00") is RawAssetFormat.ROSBAG2
    assert detect_format(b"PK\x03\x04") is RawAssetFormat.ZIP
    assert detect_format(b"\x00\x00\x00\x18ftypmp42") is RawAssetFormat.MP4
    assert detect_format(b"PAR1" + b"x" * 8, b"PAR1") is RawAssetFormat.PARQUET
    assert detect_format(b'  {"a":1}') is RawAssetFormat.JSON
    assert detect_format(b"a,b\n1,2\n") is RawAssetFormat.CSV
    assert detect_format(b"\x00\x01\x02") is None

    assert signature_compatible(RawAssetFormat.MCAP, RawAssetFormat.MCAP)
    assert signature_compatible(RawAssetFormat.ROSBAG2, RawAssetFormat.MCAP)
    assert signature_compatible(RawAssetFormat.ROSBAG2, RawAssetFormat.ROSBAG2)
    assert not signature_compatible(RawAssetFormat.MP4, RawAssetFormat.JSON)
    assert signature_compatible(RawAssetFormat.UNKNOWN, RawAssetFormat.MCAP)
    assert not signature_compatible(RawAssetFormat.UNKNOWN, None)
    assert canonical_content_type(RawAssetFormat.MCAP) == "application/x-mcap"


def test_part_layout_and_hash_normalization() -> None:
    assert required_part_count(8, 4) == 2
    assert required_part_count(9, 4) == 3
    assert expected_part_size(1, 9, 4) == 4
    assert expected_part_size(3, 9, 4) == 1
    with pytest.raises(DomainValidationError):
        expected_part_size(4, 9, 4)

    assert normalize_etag('"abc"') == "abc"
    assert normalize_sha256(SHA) == SHA
    with pytest.raises(DomainValidationError):
        normalize_sha256("sha256:ABC")
    with pytest.raises(DomainValidationError):
        normalize_sha256(None)


def test_upload_session_state_machine_and_terminal_rules() -> None:
    session = _session()
    assert session.status is UploadSessionStatus.CREATED

    uploading = session.begin_upload(NOW)
    assert uploading.status is UploadSessionStatus.UPLOADING
    completing = uploading.begin_complete(NOW)
    assert completing.status is UploadSessionStatus.COMPLETING
    completed = completing.mark_completed(
        asset_id=uuid4(),
        deduplicated=False,
        deduplicated_asset_id=None,
        validation_job_id=uuid4(),
        now=NOW,
    )
    assert completed.status is UploadSessionStatus.COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        completed.begin_upload(NOW)

    aborted = _session().abort(reason="取消", now=NOW)
    assert aborted.status is UploadSessionStatus.ABORTED
    with pytest.raises(InvalidStateTransitionError):
        aborted.expire(NOW)

    expired = _session().expire(NOW)
    assert expired.status is UploadSessionStatus.EXPIRED
    with pytest.raises(InvalidStateTransitionError):
        assert_upload_session_action(expired.status, "complete")


def test_raw_asset_immutability_and_transitions() -> None:
    project_id = uuid4()
    asset_id = uuid4()
    key = build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP)
    asset = RawAsset(
        asset_id=asset_id,
        project_id=project_id,
        code="RAW-1",
        source_type="UPLOAD",
        original_filename="robot.mcap",
        object_key=key,
        size_bytes=8,
        content_type="application/x-mcap",
        format=RawAssetFormat.MCAP,
        sha256=SHA,
        uploaded_by=uuid4(),
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    assert asset.status is RawAssetStatus.VALIDATING
    assert raw_asset_target_status(asset.status, "validate-success") is RawAssetStatus.VALID

    changed = replace(asset, size_bytes=9)
    with pytest.raises(ResourceImmutableError):
        asset.assert_content_immutable(changed)

    valid = asset.with_status(RawAssetStatus.VALID, now=NOW)
    assert valid.status is RawAssetStatus.VALID


def test_upload_part_validates_etag_and_hash() -> None:
    project_id = uuid4()
    part = UploadPart(
        project_id=project_id,
        upload_session_id=uuid4(),
        part_number=1,
        size_bytes=4,
        etag='"etag-1"',
        sha256=SHA,
        recorded_at=NOW,
    )
    assert part.etag == "etag-1"
    with pytest.raises(DomainValidationError):
        UploadPart(
            project_id=project_id,
            upload_session_id=uuid4(),
            part_number=0,
            size_bytes=4,
            etag="etag",
            sha256=SHA,
            recorded_at=NOW,
        )
