"""上传会话链（``RAW_ASSET --UPLOADED_BY--> UPLOAD_SESSION``）投影的血缘回归测试。

背景（FR-040 E2E 发现的真缺口）：``UPLOADED_BY`` 有领域枚举、数据库 CHECK
约束与 OpenAPI 契约，但**生产代码里没有任何写入者**——``upload_completion``
只写 ``raw_asset_links``，从不写血缘边。结果是「Dataset → Session」永远查不到，
FR-040 的 Session 段无法验收。

修法与数据源链一致：不新增持久化边、不加迁移，读时把 ``raw_asset_links``
投影成 ``RAW_ASSET --UPLOADED_BY--> UPLOAD_SESSION``。本文件锁住：

1. 正向：资产 → 会话 能查到投影边，且 ``metadata.derived=true``；
2. 反向：会话 → 资产（``UPLOAD_SESSION`` 作根，``IN`` 方向）；
3. 边 id 确定性（两次查询同一 id，分页不会重复/漏项）；
4. ``REVOKED`` 的链接不产边（撤销的归属不是有效事实）；
5. 跨项目一律 404，不泄露会话存在性（含 graph 端点）；
6. ``capture_session_id`` 经 metadata 透出（节点类型没有 CAPTURE_SESSION）；
7. 逐节点血缘端点的 ``limit`` 不再被投影边突破。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.infrastructure.db.models.capture_session import CaptureSessionModel
from app.infrastructure.db.models.ingestion import (
    RawAssetLinkModel,
    RawAssetModel,
    UploadSessionModel,
)
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import episode_payload, project_url

_NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
_SESSION_SHA = "sha256:" + "e" * 64


def _seed_session_asset_chain(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    project_id: UUID | None = None,
    actor_id: UUID | None = None,
    link_status: str = "ACTIVE",
    with_capture_session: bool = True,
    code_suffix: str | None = None,
) -> tuple[UUID, UUID, UUID | None]:
    """写入「上传会话 + RawAsset + 链接行」，返回 (asset_id, session_id, capture_session_id)。"""
    target_project = project_id or ids.project_a
    actor = actor_id or ids.editor_a
    asset_id, session_id = uuid4(), uuid4()
    suffix = code_suffix or asset_id.hex[:8]
    capture_session_id = uuid4() if with_capture_session else None
    with factory() as session:
        if capture_session_id is not None:
            # upload_sessions.capture_session_id 有指向 capture_sessions 的复合外键，
            # 必须先建采集会话。
            session.add(
                CaptureSessionModel(
                    id=capture_session_id,
                    project_id=target_project,
                    code=f"cs-{suffix}",
                    status="SCHEDULED",
                    created_at=_NOW,
                    updated_at=_NOW,
                    created_by=actor,
                    updated_by=actor,
                )
            )
            session.flush()
        session.add(
            UploadSessionModel(
                id=session_id,
                project_id=target_project,
                capture_session_id=capture_session_id,
                # 初始状态受触发器约束，只能是 CREATED；后续用 UPDATE 推进。
                status="CREATED",
                verification_status="PENDING",
                verified_at=_NOW,
                original_filename=f"robot-{suffix}.mcap",
                declared_content_type="application/x-mcap",
                declared_size_bytes=1024,
                expected_sha256=_SESSION_SHA,
                format_hint="MCAP",
                part_size_bytes=1024,
                max_size_bytes=1024 * 1024,
                allowed_content_types=["application/x-mcap"],
                object_key=f"raw/{target_project}/2026/11/24/{asset_id}/content.mcap",
                multipart_upload_id=f"upload-{suffix}",
                expires_at=_NOW,
                completed_at=_NOW,
                asset_id=asset_id,
                uploaded_bytes=1024,
                uploaded_part_count=1,
                version=2,
                created_at=_NOW,
                updated_at=_NOW,
                created_by=actor,
                updated_by=actor,
            )
        )
        # 上传会话必须先落库：raw_assets.upload_session_id 有指向它的复合外键。
        session.flush()
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=target_project,
                code=f"RAW-{suffix}",
                source_type="UPLOAD",
                upload_session_id=session_id,
                capture_session_id=capture_session_id,
                original_filename=f"robot-{suffix}.mcap",
                object_key=f"raw/{target_project}/2026/11/24/{asset_id}/content.mcap",
                size_bytes=1024,
                content_type="application/x-mcap",
                format="MCAP",
                sha256=f"sha256:{uuid4().hex}{uuid4().hex}"[:71],
                # 初始状态受触发器约束：必须先 VALIDATING 再转 VALID。
                status="VALIDATING",
                ingested_at=_NOW,
                storage_class="STANDARD",
                quarantine_codes=[],
                raw_metadata={},
                uploaded_by=actor,
                version=2,
                created_at=_NOW,
                updated_at=_NOW,
                created_by=actor,
                updated_by=actor,
            )
        )
        session.flush()
        # 上传会话状态机由 DB 触发器守护：CREATED → UPLOADING → COMPLETING → COMPLETED。
        for target in ("UPLOADING", "COMPLETING", "COMPLETED"):
            session.execute(
                update(UploadSessionModel)
                .where(UploadSessionModel.id == session_id)
                .values(status=target)
                .execution_options(synchronize_session=False)
            )
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(status="VALID")
            .execution_options(synchronize_session=False)
        )
        session.add(
            RawAssetLinkModel(
                id=uuid4(),
                project_id=target_project,
                asset_id=asset_id,
                upload_session_id=session_id,
                link_type="UPLOAD_SESSION",
                link_status=link_status,
                created_by=actor,
                created_at=_NOW,
            )
        )
        session.commit()
    return asset_id, session_id, capture_session_id


def _create_episode(client: Any, project_id: UUID, asset_id: UUID, code: str) -> UUID:
    response = client.post(
        project_url(project_id, "/episodes"),
        json=episode_payload(asset_id, code=code),
        headers={"Idempotency-Key": f"session-lineage-{code}"},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["episode_id"])


def _asset_lineage(
    client: Any, project_id: UUID, asset_id: UUID, **params: object
) -> dict[str, Any]:
    query: dict[str, object] = {"project_id": str(project_id), **params}
    response = client.get(f"/api/v1/raw-assets/{asset_id}/lineage", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def _graph(client: Any, project_id: UUID, **params: object) -> Any:
    return client.get(f"/api/v1/projects/{project_id}/lineage/graph", params=params)


def test_asset_forward_reaches_upload_session(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """资产正向 1 跳必须给出会话投影边，并标明是派生边。"""
    factory, ids = api_db
    asset_id, session_id, capture_session_id = _seed_session_asset_chain(factory, ids)
    client = make_client(factory, ids.editor_a)

    body = _asset_lineage(client, ids.project_a, asset_id, direction="forward", depth=1)

    edges = [item for item in body["items"] if item["relation"] == "UPLOADED_BY"]
    assert len(edges) == 1, body
    edge = edges[0]
    assert edge["from_node_type"] == "RAW_ASSET"
    assert edge["from_node_id"] == str(asset_id)
    assert edge["to_node_type"] == "UPLOAD_SESSION"
    assert edge["to_node_id"] == str(session_id)
    assert edge["metadata"]["derived"] is True
    assert edge["metadata"]["derived_from"] == "raw_asset_links"
    # 采集会话没有自己的节点类型，权威 ID 只能经 metadata 透出。
    assert edge["metadata"]["capture_session_id"] == str(capture_session_id)
    assert edge["metadata"]["upload_session_status"] == "COMPLETED"


def test_upload_session_root_walks_back_to_assets(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``UPLOAD_SESSION`` 作根：``IN`` 回到资产，``BOTH`` 同时看到 Episode。

    ``IN`` 只走上游（会话 ← 资产），Episode 在资产下游，必须用 ``BOTH`` 才能
    同时看到两侧——方向语义按边的入/出定义，不按"业务直觉"。
    """
    factory, ids = api_db
    asset_id, session_id, _capture = _seed_session_asset_chain(factory, ids)
    client = make_client(factory, ids.editor_a)
    episode_id = _create_episode(client, ids.project_a, asset_id, "EP-SESSION-001")

    upstream = _graph(
        client,
        ids.project_a,
        node_type="UPLOAD_SESSION",
        node_id=str(session_id),
        direction="IN",
        depth=2,
        limit=50,
    )
    assert upstream.status_code == 200, upstream.text
    upstream_body = upstream.json()
    assert upstream_body["root_found"] is True
    upstream_ids = {node["node_id"] for node in upstream_body["nodes"]}
    assert {str(asset_id), str(session_id)} <= upstream_ids
    assert str(episode_id) not in upstream_ids
    upstream_triples = {
        (edge["from_node_type"], edge["relation"], edge["to_node_type"])
        for edge in upstream_body["edges"]
    }
    assert ("RAW_ASSET", "UPLOADED_BY", "UPLOAD_SESSION") in upstream_triples

    both = _graph(
        client,
        ids.project_a,
        node_type="UPLOAD_SESSION",
        node_id=str(session_id),
        direction="BOTH",
        depth=2,
        limit=50,
    )
    assert both.status_code == 200, both.text
    both_body = both.json()
    both_ids = {node["node_id"] for node in both_body["nodes"]}
    assert {str(asset_id), str(session_id), str(episode_id)} <= both_ids
    both_triples = {
        (edge["from_node_type"], edge["relation"], edge["to_node_type"])
        for edge in both_body["edges"]
    }
    assert ("RAW_ASSET", "UPLOADED_BY", "UPLOAD_SESSION") in both_triples
    assert ("RAW_ASSET", "SEGMENTED_INTO", "EPISODE") in both_triples


def test_projected_session_edge_id_is_deterministic(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """投影边 id 必须由端点确定性派生；否则分页会重复/漏项。"""
    factory, ids = api_db
    asset_id, _session, _capture = _seed_session_asset_chain(factory, ids)
    client = make_client(factory, ids.editor_a)

    first = _asset_lineage(client, ids.project_a, asset_id, direction="forward", depth=1)
    second = _asset_lineage(client, ids.project_a, asset_id, direction="forward", depth=1)

    first_ids = {item["edge_id"] for item in first["items"]}
    second_ids = {item["edge_id"] for item in second["items"]}
    assert first_ids == second_ids


def test_revoked_link_produces_no_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``REVOKED`` 的使用关系不是有效归属，不能投影成边。"""
    factory, ids = api_db
    asset_id, session_id, _capture = _seed_session_asset_chain(factory, ids, link_status="REVOKED")
    client = make_client(factory, ids.editor_a)

    body = _asset_lineage(client, ids.project_a, asset_id, direction="forward", depth=2)
    assert [item for item in body["items"] if item["relation"] == "UPLOADED_BY"] == []

    response = _graph(
        client,
        ids.project_a,
        node_type="UPLOAD_SESSION",
        node_id=str(session_id),
        direction="IN",
        depth=1,
    )
    assert response.status_code == 200, response.text
    assert response.json()["edges"] == []


def test_cross_project_session_is_not_visible(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """跨项目会话一律 404（不泄露存在性），且不能靠错误码/响应体探测。"""
    factory, ids = api_db
    _asset_id, foreign_session, _capture = _seed_session_asset_chain(
        factory,
        ids,
        project_id=ids.project_b,
        actor_id=ids.owner_a,
        code_suffix="foreign",
    )
    owner = make_client(factory, ids.owner_a)

    visible = _graph(
        owner,
        ids.project_b,
        node_type="UPLOAD_SESSION",
        node_id=str(foreign_session),
        direction="IN",
        depth=1,
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["root_found"] is True

    hidden = _graph(
        owner,
        ids.project_a,
        node_type="UPLOAD_SESSION",
        node_id=str(foreign_session),
        direction="IN",
        depth=1,
    )
    assert hidden.status_code == 404, hidden.text
    assert str(foreign_session) not in hidden.text


def test_unknown_session_root_is_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """会话不存在返回 404；投影节点的存在性必须由权威仓储判定。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = _graph(
        client,
        ids.project_a,
        node_type="UPLOAD_SESSION",
        node_id=str(uuid4()),
        direction="IN",
    )
    assert response.status_code == 404, response.text


def test_node_lineage_limit_is_not_exceeded_by_projection_edges(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``limit`` 是本页边数上限，投影边也必须计入。

    回归：此前 ``trace`` 只按持久化边计数、且单层内每个节点都会再塞一条边，
    实测 ``limit=20`` 返回 22 条（同一资产挂着重复导入时更明显）。这里造
    多资产 + 多会话 + 多 Episode，用多个 ``limit`` 断言返回条数严格不超过它。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    for index in range(5):
        asset_id, _session, _capture = _seed_session_asset_chain(
            factory, ids, code_suffix=f"limit-{index}"
        )
        _create_episode(client, ids.project_a, asset_id, f"EP-LIMIT-{index}")

    target_asset, _target_session, _target_capture = _seed_session_asset_chain(
        factory, ids, code_suffix="limit-target"
    )
    episode_id = _create_episode(client, ids.project_a, target_asset, "EP-LIMIT-TARGET")

    for limit in (1, 3, 5, 20):
        response = client.get(
            f"/api/v1/episodes/{episode_id}/lineage",
            params={
                "project_id": str(ids.project_a),
                "direction": "reverse",
                "depth": 3,
                "limit": limit,
            },
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) <= limit, f"limit={limit} 返回 {len(items)} 条边"
