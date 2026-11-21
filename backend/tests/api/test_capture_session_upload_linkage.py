"""上传完成链路把采集会话归属真实落到 RawAsset（V31-001）。

此前的同名测试只断言 UploadSession 响应字段，没有真正走完分片上传 →
complete → RawAsset 查询 → 会话产物回读，属于「伪贯通」。本文件用真实
应用链（真实 SQLite + 真实仓储 + 真实路由 + Fake 对象存储端口）补齐证据。

对象存储仍是 `FakeMultipartStorage`：它明确声明只替代 S3 传输层，
不替代业务链路。真实 MinIO 验收见 V31-002。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from app.core.config import Settings
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.fakes_s3 import FakeMultipartStorage, PartMaterial

MCAP_BYTES = b"\x89MCAP0\r\n"


@dataclass(frozen=True)
class _Harness:
    client: TestClient
    fake: FakeMultipartStorage
    factory: sessionmaker[Session]
    ids: ApiIds
    settings: Settings


@pytest.fixture()
def capture_upload(api_db: tuple[sessionmaker[Session], ApiIds]) -> _Harness:
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
    return _Harness(client=client, fake=fake, factory=factory, ids=ids, settings=settings)


def _sessions_url(project_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/upload-sessions{suffix}"


def _session_url(project_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/capture-sessions{suffix}"


def _artifacts_url(project_id: UUID, session_id: str) -> str:
    return _session_url(project_id, f"/{session_id}/artifacts")


def _create_capture_session(client: TestClient, project_id: UUID, code: str) -> dict[str, Any]:
    response = client.post(_session_url(project_id), json={"code": code})
    assert response.status_code == 201, response.text
    return response.json()


def _create_upload_session(
    client: TestClient,
    project_id: UUID,
    *,
    capture_session_id: str | None,
    filename: str = "sample.mcap",
    size: int = len(MCAP_BYTES),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "original_filename": filename,
        "declared_content_type": "application/x-mcap",
        "declared_size_bytes": size,
        "format_hint": "MCAP",
        "expected_sha256": f"sha256:{hashlib.sha256(MCAP_BYTES).hexdigest()}",
    }
    if capture_session_id is not None:
        payload["capture_session_id"] = capture_session_id
    response = client.post(_sessions_url(project_id), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _upload_all(
    harness: _Harness,
    project_id: UUID,
    session: dict[str, Any],
    content: bytes,
) -> list[PartMaterial]:
    part_size = harness.settings.upload_part_size_bytes
    materials: list[PartMaterial] = []
    for index, start in enumerate(range(0, len(content), part_size), start=1):
        chunk = content[start : start + part_size]
        url = harness.client.post(
            _sessions_url(
                project_id,
                f"/{session['upload_session_id']}/parts/{index}/url",
            )
        )
        assert url.status_code == 200, url.text
        material = harness.fake.put_part(session["object_key"], index, chunk)
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


def _complete(
    harness: _Harness,
    project_id: UUID,
    session: dict[str, Any],
    materials: list[PartMaterial],
) -> Any:
    # 每次分片 ACK 都会推进会话版本，完成前必须重读取权威 ETag。
    current = harness.client.get(_sessions_url(project_id, f"/{session['upload_session_id']}"))
    assert current.status_code == 200, current.text
    body = {
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
    return harness.client.post(
        _sessions_url(project_id, f"/{session['upload_session_id']}/complete"),
        json=body,
        headers={"If-Match": f'"{current.json()["version"]}"'},
    )


def _complete_flow(
    harness: _Harness,
    project_id: UUID,
    capture_session_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    upload = _create_upload_session(
        harness.client,
        project_id,
        capture_session_id=capture_session_id,
    )
    materials = _upload_all(harness, project_id, upload, MCAP_BYTES)
    completed = _complete(harness, project_id, upload, materials)
    assert completed.status_code == 202, completed.text
    return upload, completed.json()


def _mark_valid(harness: _Harness, asset_id: str) -> None:
    """内容去重只对可用态生效；模拟校验任务把资产推进到 VALID。"""
    from app.infrastructure.db.models.ingestion import RawAssetModel
    from sqlalchemy import update as sa_update

    with harness.factory() as session:
        session.execute(
            sa_update(RawAssetModel)
            .where(RawAssetModel.id == UUID(asset_id))
            .values(status="VALID")
            .execution_options(synchronize_session=False)
        )
        session.commit()


def test_upload_completion_writes_capture_session_to_raw_asset(
    capture_upload: _Harness,
) -> None:
    """真实完成上传后，RawAsset 必须带上本次上传的 capture_session_id。"""
    harness = capture_upload
    ids = harness.ids
    created = _create_capture_session(harness.client, ids.project_a, "session-upload")

    upload, body = _complete_flow(harness, ids.project_a, created["session_id"])

    assert upload["capture_session_id"] == created["session_id"]
    asset = body["raw_asset"]
    assert asset is not None
    assert body["deduplicated"] is False
    assert asset["capture_session_id"] == created["session_id"]

    # 服务端权威值，不依赖客户端回显：重新查询 RawAsset。
    asset_id = asset["asset_id"]
    fetched = harness.client.get(f"/api/v1/projects/{ids.project_a}/raw-assets/{asset_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["capture_session_id"] == created["session_id"]

    # 会话产物列表必须能读回该 RawAsset。
    artifacts = harness.client.get(_artifacts_url(ids.project_a, created["session_id"]))
    assert artifacts.status_code == 200, artifacts.text
    items = artifacts.json()["items"]
    assert [item["artifact_id"] for item in items] == [asset_id]
    assert items[0]["kind"] == "RAW_ASSET"


def test_deduplicated_upload_still_links_capture_session(
    capture_upload: _Harness,
) -> None:
    """内容去重命中既有资产时，本次上传的会话归属不能丢（V31-001 缺陷回归）。

    既有行为：`_register_asset` 内容重复时直接返回旧资产，`capture_session_id`
    仍是最早那次上传的值，导致第二次会话的产物列表读不到本批素材。
    这里要求：去重后旧资产若尚无会话归属，则补写本次会话归属。
    """
    harness = capture_upload
    ids = harness.ids

    # 第一次上传：不带会话归属。
    _, first_body = _complete_flow(harness, ids.project_a, None)
    first_asset = first_body["raw_asset"]
    assert first_asset["capture_session_id"] is None
    _mark_valid(harness, first_asset["asset_id"])

    # 第二次上传相同内容，但带会话归属 → 内容去重命中同一资产。
    created = _create_capture_session(harness.client, ids.project_a, "session-dedup")
    _, second_body = _complete_flow(harness, ids.project_a, created["session_id"])

    assert second_body["deduplicated"] is True
    assert second_body["raw_asset"]["asset_id"] == first_asset["asset_id"]
    assert second_body["raw_asset"]["capture_session_id"] == created["session_id"]

    artifacts = harness.client.get(_artifacts_url(ids.project_a, created["session_id"]))
    assert artifacts.status_code == 200, artifacts.text
    assert [item["artifact_id"] for item in artifacts.json()["items"]] == [first_asset["asset_id"]]


def test_deduplicated_upload_does_not_steal_existing_capture_session(
    capture_upload: _Harness,
) -> None:
    """去重命中已有归属的资产时，不覆盖原有会话归属（资产保护）。

    同一份内容被两个会话先后上传时，资产只能记一个归属。已有时保留最早
    归属，并把它视为该资产的权威来源；这里明确锁定该语义，避免后续改动
    静默改变可追溯性。
    """
    harness = capture_upload
    ids = harness.ids
    first = _create_capture_session(harness.client, ids.project_a, "session-first")
    _, first_body = _complete_flow(harness, ids.project_a, first["session_id"])
    asset_id = first_body["raw_asset"]["asset_id"]
    _mark_valid(harness, asset_id)

    second = _create_capture_session(harness.client, ids.project_a, "session-second")
    _, second_body = _complete_flow(harness, ids.project_a, second["session_id"])

    assert second_body["deduplicated"] is True
    assert second_body["raw_asset"]["asset_id"] == asset_id
    assert second_body["raw_asset"]["capture_session_id"] == first["session_id"]

    second_artifacts = harness.client.get(_artifacts_url(ids.project_a, second["session_id"]))
    assert second_artifacts.status_code == 200, second_artifacts.text
    assert second_artifacts.json()["items"] == []
