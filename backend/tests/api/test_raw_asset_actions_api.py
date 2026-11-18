"""RawAsset 状态动作 API：隔离放行/驳回、事后隔离、归档、墓碑。

这些动作在领域状态机里一直有边，但此前没有 HTTP 入口——隔离资产永远放不
出来，资产也无法归档或墓碑。本用例验证它们真的可达，并且权限、版本、
幂等与审计都按统一口径工作。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "c" * 64


def _url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/raw-assets{suffix}"


def _seed_asset(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    status: str = "VALID",
    codes: list[str] | None = None,
) -> UUID:
    asset_id = uuid4()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=project_id,
                code=f"RAW-{asset_id.hex[:12]}",
                source_type="UPLOAD",
                original_filename="sample.mcap",
                object_key=(f"raw/{project_id}/2026/11/18/{asset_id}/content.mcap"),
                size_bytes=1024,
                content_type="application/x-mcap",
                format="MCAP",
                sha256=SHA,
                # DB 守卫要求新建资产从 VALIDATING 起；随后用 UPDATE 推进
                # 到目标状态，不绕过守卫。
                status="VALIDATING",
                ingested_at=NOW,
                storage_class="STANDARD",
                quarantine_codes=codes or [],
                quarantine_reason="内容签名与声明不一致" if codes else None,
                raw_metadata={},
                uploaded_by=actor_id,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        if status != "VALIDATING":
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status=status)
                .execution_options(synchronize_session=False)
            )
        session.commit()
    return asset_id


def _act(
    client: Any,
    project_id: UUID,
    asset_id: UUID,
    action: str,
    version: int,
    *,
    body: dict[str, object] | None = None,
    key: str | None = None,
) -> Any:
    headers = {"If-Match": f'"{version}"'}
    if key:
        headers["Idempotency-Key"] = key
    return client.post(
        _url(project_id, f"/{asset_id}/actions/{action}"),
        json=body or {},
        headers=headers,
    )


def test_archive_then_tombstone_changes_state_and_blocks_download(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(factory, ids.project_a, ids.editor_a)

    detail = client.get(_url(ids.project_a, f"/{asset_id}")).json()
    assert detail["status"] == "VALID"
    assert detail["allowed_actions"] == ["archive", "post-hoc-quarantine", "tombstone"]

    archived = _act(client, ids.project_a, asset_id, "archive", 1)
    assert archived.status_code == 200, archived.text
    body = archived.json()
    assert body["status"] == "ARCHIVED"
    assert body["archived_at"] is not None
    assert body["allowed_actions"] == ["tombstone"]

    # ARCHIVED 仍可下载（正常生命周期终点），TOMBSTONED 不可下载。
    # 下载地址是"无项目路径前缀"的端点（靠 query 声明项目），与前端
    # `rawAssetsApi.getDownloadUrl` 的调用方式一致。
    download = client.get(
        f"/api/v1/raw-assets/{asset_id}/download-url",
        params={"project_id": str(ids.project_a)},
    )
    assert download.status_code == 200, download.text

    tombstoned = _act(
        client,
        ids.project_a,
        asset_id,
        "tombstone",
        int(body["version"]),
        body={"reason": "来源数据被撤回"},
    )
    assert tombstoned.status_code == 200, tombstoned.text
    final = tombstoned.json()
    assert final["status"] == "TOMBSTONED"
    assert final["tombstoned_at"] is not None
    assert final["allowed_actions"] == []

    blocked = client.get(
        f"/api/v1/raw-assets/{asset_id}/download-url",
        params={"project_id": str(ids.project_a)},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "asset_not_usable"

    with factory() as session:
        events = set(session.scalars(select(OutboxEvent.event_type)))
        assert "ingestion.raw_asset.status_changed" in events
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 2


def test_quarantine_release_and_reject(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(
        factory, ids.project_a, ids.editor_a, status="QUARANTINED", codes=["hash_mismatch"]
    )

    detail = client.get(_url(ids.project_a, f"/{asset_id}")).json()
    assert detail["allowed_actions"] == [
        "reject-quarantined",
        "release-quarantine-or-revalidate",
        "tombstone",
    ]

    # 驳回隔离资产必须给原因。
    missing = _act(client, ids.project_a, asset_id, "reject-quarantined", 1)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_failed"

    rejected = _act(
        client,
        ids.project_a,
        asset_id,
        "reject-quarantined",
        int(detail["version"]),
        body={"reason": "文件损坏且无法重新上传"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["allowed_actions"] == ["tombstone"]

    # 另一条走放行：回到 VALIDATING 重新校验，并清空上一轮隔离结论。
    other_id = _seed_asset(
        factory, ids.project_a, ids.editor_a, status="QUARANTINED", codes=["hash_mismatch"]
    )
    released = _act(client, ids.project_a, other_id, "release-quarantine-or-revalidate", 1)
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["status"] == "VALIDATING"
    assert body["quarantine_codes"] == []
    assert body["quarantine_reason"] is None
    assert body["verified_at"] is None

    # 放行必须投递校验事件，否则资产会永远停在 VALIDATING（假动作）。
    with factory() as session:
        events = list(
            session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "ingestion.raw_asset.validation_requested"
                )
            )
        )
        assert events, "放行后没有投递校验事件"
        assert events[-1].dispatch is True
        assert events[-1].payload["asset_id"] == str(other_id)


def test_post_hoc_quarantine_requires_reason_and_codes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(factory, ids.project_a, ids.editor_a)

    no_reason = _act(
        client,
        ids.project_a,
        asset_id,
        "post-hoc-quarantine",
        1,
        body={"quarantine_codes": ["metadata_conflict"]},
    )
    assert no_reason.status_code == 422

    no_codes = _act(
        client,
        ids.project_a,
        asset_id,
        "post-hoc-quarantine",
        1,
        body={"reason": "发现隐私画面"},
    )
    assert no_codes.status_code == 422

    quarantined = _act(
        client,
        ids.project_a,
        asset_id,
        "post-hoc-quarantine",
        1,
        body={"reason": "发现隐私画面", "quarantine_codes": ["metadata_conflict"]},
    )
    assert quarantined.status_code == 200, quarantined.text
    assert quarantined.json()["status"] == "QUARANTINED"
    assert quarantined.json()["quarantine_codes"] == ["metadata_conflict"]


def test_action_permissions_isolation_version_and_idempotency(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(factory, ids.project_a, ids.editor_a)

    viewer = make_client(factory, ids.viewer_a)
    forbidden = _act(viewer, ids.project_a, asset_id, "archive", 1)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    outsider = make_client(factory, ids.outsider)
    cross_project = _act(outsider, ids.project_b, asset_id, "archive", 1)
    assert cross_project.status_code in (403, 404)

    missing_match = editor.post(_url(ids.project_a, f"/{asset_id}/actions/archive"), json={})
    assert missing_match.status_code == 428

    unknown = _act(editor, ids.project_a, asset_id, "system-fail", 1)
    assert unknown.status_code == 422

    stale = _act(editor, ids.project_a, asset_id, "archive", 99)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    archived = _act(editor, ids.project_a, asset_id, "archive", 1, key="asset-archive-0001")
    assert archived.status_code == 200, archived.text
    replay = _act(editor, ids.project_a, asset_id, "archive", 1, key="asset-archive-0001")
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["asset_id"] == str(asset_id)

    with factory() as session:
        status = session.get(RawAssetModel, asset_id)
        assert status is not None and status.status == "ARCHIVED"
