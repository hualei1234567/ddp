"""数据源导入 Worker：真实 SQLite + 真实队列 + 真实 ImportRunner。

锁定的缺陷（2026-11-17 修复）：Worker 曾用 ``actor_id=project_id`` 当系统
执行身份，而 ``data_source_imports.updated_by`` / ``raw_assets.created_by``
都有 FK 到 ``users``。任何一次状态推进都会 ``FOREIGN KEY constraint failed``，
表现为"导入任务反复重试却永不成功"（与 ingestion_jobs Worker 修复前的缺陷
同源）。修复后身份取导入任务的创建人。

外部系统只有两处替身（显式标注）：HF 网络与 MinIO 写入。状态机、审计、
outbox、RawAsset 生命周期、媒体资产落库全部是生产实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.datasources import DataSourceImportModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.e2e.support import (
    EXPECTED_ASSETS,
    EXPECTED_EPISODES,
    InMemoryObjectStore,
    MediaTransport,
    install_object_store,
    register_import_chain,
    run_import_worker,
)


def test_import_worker_publishes_event_and_stamps_real_actor(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    code = f"hf-worker-{UUID(int=0).hex[:6]}"
    source, job = register_import_chain(editor, ids.project_a, code=code)

    store = InMemoryObjectStore()
    install_object_store(monkeypatch, store)
    result: dict[str, Any] = run_import_worker(
        factory, MediaTransport(), now=datetime.now(UTC) + timedelta(seconds=1)
    )

    assert result["status"] == "PUBLISHED", result
    assert result["import_id"] == job["import_id"]
    assert result["episodes"] == EXPECTED_EPISODES
    assert result["assets"] == EXPECTED_ASSETS

    imported = editor.get(url(ids.project_a, f"/data-source-imports/{job['import_id']}"))
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["status"] == "SUCCEEDED", body
    assert body["imported_row_count"] == EXPECTED_EPISODES
    assert body["imported_file_count"] == EXPECTED_ASSETS

    assets = editor.get(url(ids.project_a, f"/data-source-imports/{job['import_id']}/assets"))
    assert assets.status_code == 200, assets.text
    assert len(assets.json()["items"]) == EXPECTED_ASSETS
    assert len(store.objects) == EXPECTED_ASSETS

    with factory() as session:
        row = session.get(DataSourceImportModel, UUID(job["import_id"]))
        assert row is not None
        # 回归锚点：系统执行身份必须是真实用户（这里是导入的创建人），
        # 不是 project_id——后者会直接违反 users 外键。
        assert row.created_by == ids.editor_a
        assert row.updated_by == ids.editor_a
        assert row.updated_by != row.project_id

        raw_assets = editor.get(url(ids.project_a, "/raw-assets?limit=100")).json()["items"]
        imported_assets = [
            item for item in raw_assets if item["raw_metadata"].get("import_id") == job["import_id"]
        ]
        assert len(imported_assets) == EXPECTED_ASSETS
        for item in imported_assets:
            assert item["source_type"] == "IMPORT"
            assert item["status"] == "VALID", item
            assert item["created_by"] == str(ids.editor_a)
            assert item["raw_metadata"]["data_source_id"] == source["data_source_id"]

        events = list(
            session.scalars(
                select(OutboxEvent.event_type)
                .where(OutboxEvent.project_id == ids.project_a)
                .order_by(OutboxEvent.created_at, OutboxEvent.event_type)
            )
        )
        assert "datasource.import_requested" in events
        assert "datasource.import.start" in events
        assert "datasource.import.succeed" in events

        audit_actions = list(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.project_id == ids.project_a,
                    AuditLog.resource_type == "data_source_import",
                )
            )
        )
        assert {"create", "start", "succeed"} <= set(audit_actions), audit_actions

    # 重复投递：事件已被发布，再次处理必须 IDLE，不得重复导入。
    again = run_import_worker(
        factory, MediaTransport(), now=datetime.now(UTC) + timedelta(seconds=2)
    )
    assert again["status"] == "IDLE", again


def test_import_then_materialize_auto_readies_episodes(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """导入 → 物化 → Episode 自动就绪：Manifest 由平台生成，不由人补。

    这是主链唯一断点（``docs/testing/主链端到端与可追溯性验收-2026-11-17.md``
    §6.2：Canonical Manifest 生成器 ``MISSING``）的回归用例。

    两种机位形态都必须能就绪：单机位与多机位（``demo_001``）都声明了帧率，
    缺标定只记 notice（能否训练由数据集准入判断），因此物化后全部 ``READY``，
    落 ``manifest_sha256`` 与 ``frame_count``。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    code = f"hf-auto-{UUID(int=0).hex[:6]}"
    _, job = register_import_chain(editor, ids.project_a, code=code)

    install_object_store(monkeypatch, InMemoryObjectStore())
    result: dict[str, Any] = run_import_worker(
        factory, MediaTransport(), now=datetime.now(UTC) + timedelta(seconds=1)
    )
    assert result["status"] == "PUBLISHED", result

    materialized = editor.post(
        url(ids.project_a, f"/data-source-imports/{job['import_id']}/episodes"),
        json={"scene_labels": []},
    )
    assert materialized.status_code == 201, materialized.text
    created = materialized.json()
    assert created["episode_count"] == EXPECTED_EPISODES, created

    # 物化响应里只有 episode_key；Episode 的 code 由物化器确定性生成（EP-{key}）。
    episodes = {
        f"EP-{item['episode_key']}": editor.get(
            url(ids.project_a, f"/episodes/{item['episode_id']}")
        ).json()
        for item in created["episodes"]
    }
    for episode_code, episode in episodes.items():
        assert episode["status"] == "READY", (episode_code, episode)
        assert episode["manifest_sha256"], episode
        assert episode["readiness_blockers"] == [], episode
        assert str(episode["manifest_sha256"]).startswith("sha256:"), episode
        assert episode["manifest_key"], episode
        assert episode["readiness_blockers"] == [], episode
        assert episode["frame_count"] > 0, episode

    # 两种结果可区分：Episode 就绪被拒不影响导入任务的结论。
    imported = editor.get(url(ids.project_a, f"/data-source-imports/{job['import_id']}"))
    assert imported.json()["status"] == "SUCCEEDED", imported.text

    # 幂等：重复物化不产生新 Episode；已就绪的不会被重写，被拒的重新判一次。
    again = editor.post(
        url(ids.project_a, f"/data-source-imports/{job['import_id']}/episodes"),
        json={"scene_labels": []},
    )
    assert again.status_code == 201, again.text
    assert again.json()["episode_count"] == 0, again.text
    assert set(again.json()["skipped_episode_keys"]) == {
        item["episode_key"] for item in created["episodes"]
    }
    demo_002 = episodes["EP-demo_002"]
    still_ready = editor.get(url(ids.project_a, f"/episodes/{demo_002['episode_id']}"))
    assert still_ready.json()["status"] == "READY", still_ready.text
    # 已就绪的 Episode 一次都没有被再次写入（版本号不变）。
    assert still_ready.json()["version"] == demo_002["version"], still_ready.text
