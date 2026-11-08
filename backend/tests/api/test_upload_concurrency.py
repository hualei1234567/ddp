"""并发上传完成修复（SQLite API 条件用例）。

覆盖验收 DDP-308-D01：
- ListParts 收到 NoSuchUpload 后重读会话，返回 409（version_conflict 或
  upload_incomplete），不再把并发信号映射为 503；
- 会话已完成时返回幂等 202，且只有 1 个资产；
- 真正的存储故障仍返回 503。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.ingestion import RawAssetModel, UploadSessionModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.fakes_s3 import FakeMultipartStorage, PartMaterial

MCAP_BYTES = b"\x89MCAP0\r\n"


@dataclass(frozen=True)
class UploadHarness:
    client: Any
    fake: FakeMultipartStorage
    factory: sessionmaker[Session]
    ids: ApiIds
    settings: Settings


@pytest.fixture()
def upload_harness(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> UploadHarness:
    factory, ids = api_db
    fake = FakeMultipartStorage()
    settings = Settings(
        env="local",
        upload_part_size_bytes=4,
        upload_max_size_bytes=64,
        upload_session_ttl_hours=1,
        upload_url_ttl_seconds=60,
    )
    client = make_client(
        factory,
        ids.editor_a,
        object_storage=fake,
        settings_override=settings,
    )
    return UploadHarness(client, fake, factory, ids, settings)


def _sessions_url(project_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/upload-sessions{suffix}"


def _create_session(harness: UploadHarness, project_id: UUID) -> dict[str, Any]:
    response = harness.client.post(
        _sessions_url(project_id),
        json={
            "original_filename": "sample.mcap",
            "declared_content_type": "application/x-mcap",
            "declared_size_bytes": len(MCAP_BYTES),
            "format_hint": "MCAP",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_all(
    harness: UploadHarness,
    project_id: UUID,
    session: dict[str, Any],
) -> list[PartMaterial]:
    materials: list[PartMaterial] = []
    part_size = harness.settings.upload_part_size_bytes
    for index, start in enumerate(range(0, len(MCAP_BYTES), part_size), start=1):
        data = MCAP_BYTES[start : start + part_size]
        material = harness.fake.put_part(session["object_key"], index, data)
        ack = harness.client.post(
            _sessions_url(
                project_id,
                f"/{session['upload_session_id']}/parts/{index}/ack",
            ),
            json={
                "etag": material.etag,
                "size_bytes": material.size_bytes,
                "sha256": material.sha256,
            },
        )
        assert ack.status_code == 200, ack.text
        materials.append(material)
    return materials


def _complete_body(materials: list[PartMaterial]) -> dict[str, Any]:
    return {
        "expected_sha256": f"sha256:{hashlib.sha256(MCAP_BYTES).hexdigest()}",
        "expected_size_bytes": len(MCAP_BYTES),
        "parts": [
            {
                "part_number": item.part_number,
                "etag": item.etag,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in materials
        ],
    }


def _detail(harness: UploadHarness, project_id: UUID, session: dict[str, Any]) -> dict[str, Any]:
    response = harness.client.get(_sessions_url(project_id, f"/{session['upload_session_id']}"))
    assert response.status_code == 200, response.text
    return response.json()


def _complete(
    harness: UploadHarness,
    project_id: UUID,
    session: dict[str, Any],
    body: dict[str, Any],
    *,
    if_match: str | None = None,
) -> Any:
    headers = {"If-Match": if_match or f'"{session["version"]}"'}
    return harness.client.post(
        _sessions_url(project_id, f"/{session['upload_session_id']}/complete"),
        json=body,
        headers=headers,
    )


def test_list_parts_no_such_upload_is_conflict_not_503(upload_harness: UploadHarness) -> None:
    harness = upload_harness
    project = harness.ids.project_a
    session = _create_session(harness, project)
    materials = _upload_all(harness, project, session)
    detail = _detail(harness, project, session)
    body = _complete_body(materials)

    harness.fake.fail_next_list_parts_not_found()
    conflicted = _complete(harness, project, detail, body)
    assert conflicted.status_code == 409, conflicted.text
    assert conflicted.json()["error"]["code"] in {
        "upload_incomplete",
        "version_conflict",
    }
    assert _detail(harness, project, session)["status"] == "UPLOADING"

    completed = _complete(harness, project, _detail(harness, project, session), body)
    assert completed.status_code == 202, completed.text
    assert completed.json()["upload_session"]["status"] == "COMPLETED"
    assert completed.json()["replayed"] is False

    replay = _complete(harness, project, detail, body)
    assert replay.status_code == 202
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["replayed"] is True
    assert replay.json()["raw_asset"]["asset_id"] == completed.json()["raw_asset"]["asset_id"]

    with harness.factory() as db:
        assert db.scalar(select(func.count()).select_from(UploadSessionModel)) == 1
        assert db.scalar(select(func.count()).select_from(RawAssetModel)) == 1


def test_object_storage_outage_still_returns_503(upload_harness: UploadHarness) -> None:
    harness = upload_harness
    project = harness.ids.project_a
    session = _create_session(harness, project)
    materials = _upload_all(harness, project, session)
    detail = _detail(harness, project, session)

    harness.fake.fail_next("list")
    failed = _complete(harness, project, detail, _complete_body(materials))
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "object_storage_unavailable"
    assert _detail(harness, project, session)["status"] == "UPLOADING"
