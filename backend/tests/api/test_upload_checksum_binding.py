"""分片预签名校验和绑定（x-amz-checksum-sha256）API 测试。

覆盖 DDP-307 契约偏差 1：URL 可携带 sha256 绑定、ack 与绑定不一致返回 409、
未携带 sha256 的旧客户端保持兼容。真实 MinIO/S3 由 staging/QA 复验。
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.ingestion import UploadPartModel
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.fakes_s3 import FakeMultipartStorage

MCAP_BYTES = b"\x89MCAP0\r\n"


@dataclass(frozen=True)
class ChecksumHarness:
    client: TestClient
    fake: FakeMultipartStorage
    factory: sessionmaker[Session]
    ids: ApiIds


@pytest.fixture()
def checksum_api(api_db: tuple[sessionmaker[Session], ApiIds]) -> ChecksumHarness:
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
    return ChecksumHarness(client=client, fake=fake, factory=factory, ids=ids)


def _sessions_url(project_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/upload-sessions{suffix}"


def _create(client: TestClient, project_id: UUID) -> dict[str, Any]:
    response = client.post(
        _sessions_url(project_id),
        json={
            "original_filename": "sample.mcap",
            "declared_content_type": "application/x-mcap",
            "declared_size_bytes": 8,
            "format_hint": "MCAP",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sign(
    harness: ChecksumHarness,
    project_id: UUID,
    session: dict[str, Any],
    *,
    sha256: str | None,
    part_number: int = 1,
) -> Any:
    url = _sessions_url(
        project_id,
        f"/{session['upload_session_id']}/parts/{part_number}/url",
    )
    if sha256 is None:
        # 模拟旧客户端：不发送请求体。
        return harness.client.post(url)
    return harness.client.post(url, json={"sha256": sha256})


def _ack(
    harness: ChecksumHarness,
    project_id: UUID,
    session: dict[str, Any],
    material: Any,
    *,
    sha256: str | None = None,
    part_number: int = 1,
) -> Any:
    return harness.client.post(
        _sessions_url(
            project_id,
            f"/{session['upload_session_id']}/parts/{part_number}/ack",
        ),
        json={
            "etag": material.etag,
            "size_bytes": material.size_bytes,
            "sha256": material.sha256 if sha256 is None else sha256,
        },
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def test_url_binds_checksum_and_ack_rejects_mismatch(checksum_api: ChecksumHarness) -> None:
    harness = checksum_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    data = MCAP_BYTES[:4]
    signed_sha = f"sha256:{hashlib.sha256(data).hexdigest()}"

    signed = _sign(harness, project, session, sha256=signed_sha)
    assert signed.status_code == 200, signed.text
    assert signed.json()["headers"]["x-amz-checksum-sha256"] == _b64(data)
    assert harness.fake.presigned_calls[-1]["checksum_sha256_base64"] == _b64(data)
    assert harness.fake.presigned_calls[-1]["part_number"] == 1

    material = harness.fake.put_part(session["object_key"], 1, data)
    other_sha = f"sha256:{hashlib.sha256(b'XXXX').hexdigest()}"
    mismatch = _ack(harness, project, session, material, sha256=other_sha)
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "upload_part_conflict"

    with harness.factory() as db:
        assert db.scalar(select(func.count()).select_from(UploadPartModel)) == 0

    accepted = _ack(harness, project, session, material)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["uploaded_part_count"] == 1


def test_url_without_checksum_keeps_legacy_behavior(checksum_api: ChecksumHarness) -> None:
    harness = checksum_api
    project = harness.ids.project_a
    session = _create(harness.client, project)

    signed = _sign(harness, project, session, sha256=None)
    assert signed.status_code == 200, signed.text
    assert "x-amz-checksum-sha256" not in signed.json()["headers"]
    assert harness.fake.presigned_calls[-1]["checksum_sha256_base64"] is None

    material = harness.fake.put_part(session["object_key"], 1, MCAP_BYTES[:4])
    ack = _ack(harness, project, session, material)
    assert ack.status_code == 200, ack.text


def test_url_rejects_invalid_checksum_and_unknown_fields(
    checksum_api: ChecksumHarness,
) -> None:
    harness = checksum_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    url = _sessions_url(project, f"/{session['upload_session_id']}/parts/1/url")

    invalid = harness.client.post(url, json={"sha256": "not-a-checksum"})
    assert invalid.status_code == 422

    extra = harness.client.post(
        url,
        json={"sha256": f"sha256:{'a' * 64}", "project_id": str(project)},
    )
    assert extra.status_code == 422


def test_rebinding_uses_latest_value_before_ack(checksum_api: ChecksumHarness) -> None:
    harness = checksum_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    first_data = MCAP_BYTES[:4]
    second_data = b"ABCD"
    first_sha = f"sha256:{hashlib.sha256(first_data).hexdigest()}"
    second_sha = f"sha256:{hashlib.sha256(second_data).hexdigest()}"

    first_sign = _sign(harness, project, session, sha256=first_sha)
    assert first_sign.status_code == 200, first_sign.text
    second_sign = _sign(harness, project, session, sha256=second_sha)
    assert second_sign.status_code == 200, second_sign.text
    assert harness.fake.presigned_calls[-1]["checksum_sha256_base64"] == _b64(second_data)

    old_material = harness.fake.put_part(session["object_key"], 1, first_data)
    stale = _ack(harness, project, session, old_material)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "upload_part_conflict"

    new_material = harness.fake.put_part(session["object_key"], 1, second_data)
    accepted = _ack(harness, project, session, new_material)
    assert accepted.status_code == 200, accepted.text

    resigned = _sign(harness, project, session, sha256=first_sha)
    assert resigned.status_code == 409
    assert resigned.json()["error"]["code"] == "upload_part_conflict"
