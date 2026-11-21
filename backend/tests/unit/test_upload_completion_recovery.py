"""并发 complete 的 ListParts NoSuchUpload 收敛（应用层条件分支）。

真实 API + SQLite 用例覆盖 409 分支；本用例通过可控仓储快照覆盖“重读后
会话已 COMPLETED → 幂等 202”与“版本已推进 → 409 version_conflict”。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.ingestion.ports import (
    CompletePartSpec,
    CompleteUploadCommand,
    StoredPart,
)
from app.application.ingestion.upload_completion import UploadCompletionService
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetSourceType,
    RawAssetStatus,
    StorageClass,
    UploadSessionStatus,
)
from app.domain.ingestion.errors import (
    MultipartUploadNotFoundError,
    UploadIncompleteError,
    VersionConflictError,
)
from app.domain.ingestion.models import RawAsset, UploadPart, UploadSession
from app.domain.ingestion.validation import build_raw_object_key

SHA = f"sha256:{'b' * 64}"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


class _SessionRepo:
    def __init__(self, snapshots: list[UploadSession]) -> None:
        self._snapshots = snapshots
        self.calls = 0

    def get(self, scope: ProjectScope, session_id: UUID) -> UploadSession | None:
        index = min(self.calls, len(self._snapshots) - 1)
        self.calls += 1
        return self._snapshots[index]


class _PartRepo:
    def __init__(self, part: UploadPart) -> None:
        self._part = part

    def list(self, scope: ProjectScope, session_id: UUID) -> list[UploadPart]:
        return [self._part]


class _AssetRepo:
    def __init__(self, asset: RawAsset) -> None:
        self._asset = asset

    def get(self, scope: ProjectScope, asset_id: UUID) -> RawAsset | None:
        return self._asset if asset_id == self._asset.asset_id else None

    def find_active_by_content(self, *args: object, **kwargs: object) -> None:
        return None

    def attach_capture_session_if_absent(self, *args: object, **kwargs: object) -> bool:
        return False


class _Storage:
    def list_parts(self, object_key: str, upload_id: str) -> list[StoredPart]:
        raise MultipartUploadNotFoundError("multipart 已不存在")


class _NoopAudit:
    def record(self, scope: ProjectScope, record: object) -> None:
        return None


class _NoopOutbox:
    def enqueue(self, scope: ProjectScope, event: object) -> None:
        return None


class _NoopIdempotency:
    def get(self, scope: ProjectScope, key: str) -> None:
        return None

    def save(self, scope: ProjectScope, record: object) -> None:
        return None

    def delete(self, scope: ProjectScope, key: str) -> None:
        return None


class _NoopUow:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _session(status: UploadSessionStatus, *, version: int, **overrides: object) -> UploadSession:
    project_id = uuid4()
    session_id = uuid4()
    values: dict[str, object] = {
        "upload_session_id": session_id,
        "project_id": project_id,
        "original_filename": "sample.mcap",
        "declared_content_type": "application/x-mcap",
        "declared_size_bytes": 8,
        "part_size_bytes": 8,
        "max_size_bytes": 64,
        "object_key": build_raw_object_key(project_id, session_id, NOW, RawAssetFormat.MCAP),
        "multipart_upload_id": "upload-1",
        "expires_at": NOW + timedelta(days=365),
        "allowed_content_types": ("application/x-mcap",),
        "status": status,
        "format_hint": RawAssetFormat.MCAP,
        "expected_sha256": SHA,
        "version": version,
        "uploaded_bytes": 8,
        "uploaded_part_count": 1,
    }
    values.update(overrides)
    return UploadSession(**values)  # type: ignore[arg-type]


def _asset(project_id: UUID) -> RawAsset:
    asset_id = uuid4()
    return RawAsset(
        asset_id=asset_id,
        project_id=project_id,
        code="RAW-1",
        source_type=RawAssetSourceType.UPLOAD,
        original_filename="sample.mcap",
        object_key=build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP),
        size_bytes=8,
        content_type="application/x-mcap",
        format=RawAssetFormat.MCAP,
        sha256=SHA,
        status=RawAssetStatus.VALID,
        ingested_at=NOW,
        storage_class=StorageClass.STANDARD,
        uploaded_by=uuid4(),
    )


def _service(session_repo: _SessionRepo, part: UploadPart) -> UploadCompletionService:
    return UploadCompletionService(
        sessions=session_repo,  # type: ignore[arg-type]
        parts=_PartRepo(part),  # type: ignore[arg-type]
        assets=_AssetRepo(_asset(part.project_id)),  # type: ignore[arg-type]
        links=object(),  # type: ignore[arg-type]
        storage=_Storage(),  # type: ignore[arg-type]
        audit=_NoopAudit(),  # type: ignore[arg-type]
        outbox=_NoopOutbox(),  # type: ignore[arg-type]
        idempotency=_NoopIdempotency(),  # type: ignore[arg-type]
        uow=_NoopUow(),  # type: ignore[arg-type]
    )


def _command() -> CompleteUploadCommand:
    return CompleteUploadCommand(
        expected_sha256=SHA,
        expected_size_bytes=8,
        parts=(
            CompletePartSpec(
                part_number=1,
                etag="etag-1",
                size_bytes=8,
                sha256=SHA,
            ),
        ),
    )


def test_missing_multipart_reloads_completed_session_as_idempotent_replay() -> None:
    uploading = _session(UploadSessionStatus.UPLOADING, version=1)
    asset = _asset(uploading.project_id)
    completed = replace(
        uploading,
        status=UploadSessionStatus.COMPLETED,
        version=3,
        asset_id=asset.asset_id,
        validation_job_id=uuid4(),
        completed_at=NOW,
    )
    part = UploadPart(
        project_id=uploading.project_id,
        upload_session_id=uploading.upload_session_id,
        part_number=1,
        size_bytes=8,
        etag="etag-1",
        sha256=SHA,
        recorded_at=NOW,
    )
    session_repo = _SessionRepo([uploading, completed])
    service = UploadCompletionService(
        sessions=session_repo,  # type: ignore[arg-type]
        parts=_PartRepo(part),  # type: ignore[arg-type]
        assets=_AssetRepo(asset),  # type: ignore[arg-type]
        links=object(),  # type: ignore[arg-type]
        storage=_Storage(),  # type: ignore[arg-type]
        audit=_NoopAudit(),  # type: ignore[arg-type]
        outbox=_NoopOutbox(),  # type: ignore[arg-type]
        idempotency=_NoopIdempotency(),  # type: ignore[arg-type]
        uow=_NoopUow(),  # type: ignore[arg-type]
    )

    result = service.complete(
        ProjectScope(uploading.project_id, actor_id=uuid4()),
        uploading.upload_session_id,
        _command(),
        expected_version=1,
        path="/complete",
    )
    assert result.replayed is True
    assert result.session.status is UploadSessionStatus.COMPLETED
    assert result.raw_asset is not None
    assert result.raw_asset.asset_id == asset.asset_id
    assert session_repo.calls == 2


def test_missing_multipart_with_advanced_version_returns_version_conflict() -> None:
    uploading = _session(UploadSessionStatus.UPLOADING, version=1)
    advanced = replace(uploading, status=UploadSessionStatus.COMPLETING, version=2)
    part = UploadPart(
        project_id=uploading.project_id,
        upload_session_id=uploading.upload_session_id,
        part_number=1,
        size_bytes=8,
        etag="etag-1",
        sha256=SHA,
        recorded_at=NOW,
    )
    service = _service(_SessionRepo([uploading, advanced]), part)

    with pytest.raises(VersionConflictError):
        service.complete(
            ProjectScope(uploading.project_id, actor_id=uuid4()),
            uploading.upload_session_id,
            _command(),
            expected_version=1,
            path="/complete",
        )


def test_missing_multipart_without_progress_returns_upload_incomplete() -> None:
    uploading = _session(UploadSessionStatus.UPLOADING, version=1)
    part = UploadPart(
        project_id=uploading.project_id,
        upload_session_id=uploading.upload_session_id,
        part_number=1,
        size_bytes=8,
        etag="etag-1",
        sha256=SHA,
        recorded_at=NOW,
    )
    service = _service(_SessionRepo([uploading]), part)

    with pytest.raises(UploadIncompleteError):
        service.complete(
            ProjectScope(uploading.project_id, actor_id=uuid4()),
            uploading.upload_session_id,
            _command(),
            expected_version=1,
            path="/complete",
        )
