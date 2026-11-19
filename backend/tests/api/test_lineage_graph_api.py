"""通用血缘图查询 API 集成测试（真实 HTTP + 真实 SQLite 仓储）。

覆盖验收要求：多跳、方向（OUT/IN/BOTH）、深度上限、环、分页、项目隔离与
权限。边与节点投影通过真实仓储写入，不 mock 用例层；查询本身只读，测试同时
断言查询没有新增任何血缘行。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import DataLineageEdgeModel
from app.infrastructure.repositories.lineage import SqlLineageRepository
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

_NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)

NodeSpec = tuple[LineageNodeType, UUID, LineageNodeType, UUID, LineageRelation]


def _seed_edges(
    factory: sessionmaker[Session],
    ids: ApiIds,
    specs: list[NodeSpec],
    *,
    project_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> None:
    target_project = project_id or ids.project_a
    scope = ProjectScope(project_id=target_project, actor_id=actor_id or ids.editor_a)
    with factory() as session:
        repo = SqlLineageRepository(session)
        for index, (from_type, from_id, to_type, to_id, relation) in enumerate(specs):
            now = _NOW + timedelta(seconds=index)
            repo.ensure_node(scope, node_type=from_type, node_id=from_id, now=now)
            repo.ensure_node(scope, node_type=to_type, node_id=to_id, now=now)
            repo.ensure_edge(
                scope,
                from_node_type=from_type,
                from_node_id=from_id,
                to_node_type=to_type,
                to_node_id=to_id,
                relation=relation,
                metadata={"seq": index},
                now=now,
            )
        session.commit()


def _graph(
    client: Any,
    project_id: UUID,
    node_type: LineageNodeType,
    node_id: UUID,
    **params: object,
):
    query: dict[str, object] = {
        "node_type": node_type.value,
        "node_id": str(node_id),
        **params,
    }
    return client.get(f"/api/v1/projects/{project_id}/lineage/graph", params=query)


def _chain() -> tuple[UUID, UUID, UUID, UUID, list[NodeSpec]]:
    asset, episode, version, run = uuid4(), uuid4(), uuid4(), uuid4()
    return (
        asset,
        episode,
        version,
        run,
        [
            (
                LineageNodeType.RAW_ASSET,
                asset,
                LineageNodeType.EPISODE,
                episode,
                LineageRelation.SEGMENTED_INTO,
            ),
            (
                LineageNodeType.EPISODE,
                episode,
                LineageNodeType.DATASET_VERSION,
                version,
                LineageRelation.INCLUDED_IN,
            ),
            (
                LineageNodeType.DATASET_VERSION,
                version,
                LineageNodeType.TRAINING_RUN,
                run,
                LineageRelation.TRAINED_ON,
            ),
        ],
    )


def test_multi_hop_out_only_walks_downstream(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    asset, episode, version, run, specs = _chain()
    _seed_edges(factory, ids, specs)
    client = make_client(factory, ids.editor_a)

    response = _graph(
        client,
        ids.project_a,
        LineageNodeType.EPISODE,
        episode,
        direction="OUT",
        depth=2,
        limit=50,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["root_found"] is True
    assert body["direction"] == "OUT"
    assert body["depth"] == 2
    node_ids = {node["node_id"] for node in body["nodes"]}
    assert node_ids == {str(episode), str(version), str(run)}
    assert str(asset) not in node_ids
    triples = {(edge["from_node_type"], edge["to_node_type"]) for edge in body["edges"]}
    assert triples == {("EPISODE", "DATASET_VERSION"), ("DATASET_VERSION", "TRAINING_RUN")}
    assert body["truncated"] is False


def test_in_direction_walks_upstream(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    asset, episode, version, _run, specs = _chain()
    _seed_edges(factory, ids, specs)
    client = make_client(factory, ids.viewer_a)

    response = _graph(
        client,
        ids.project_a,
        LineageNodeType.DATASET_VERSION,
        version,
        direction="IN",
        depth=2,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    node_ids = {node["node_id"] for node in body["nodes"]}
    assert node_ids == {str(asset), str(episode), str(version)}
    triples = {(edge["from_node_type"], edge["to_node_type"]) for edge in body["edges"]}
    assert triples == {("RAW_ASSET", "EPISODE"), ("EPISODE", "DATASET_VERSION")}


def test_both_direction_sees_both_sides(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _asset, episode, _version, _run, specs = _chain()
    _seed_edges(factory, ids, specs)
    client = make_client(factory, ids.editor_a)

    response = _graph(
        client,
        ids.project_a,
        LineageNodeType.EPISODE,
        episode,
        direction="BOTH",
        depth=1,
    )

    assert response.status_code == 200, response.text
    triples = {(edge["from_node_type"], edge["to_node_type"]) for edge in response.json()["edges"]}
    assert triples == {("RAW_ASSET", "EPISODE"), ("EPISODE", "DATASET_VERSION")}


def test_depth_beyond_hard_cap_is_unprocessable(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = _graph(
        client,
        ids.project_a,
        LineageNodeType.EPISODE,
        uuid4(),
        depth=4,
    )
    assert response.status_code == 422, response.text


def test_cycles_terminate_without_duplicates(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    first, second = uuid4(), uuid4()
    _seed_edges(
        factory,
        ids,
        [
            (
                LineageNodeType.EPISODE,
                first,
                LineageNodeType.EPISODE,
                second,
                LineageRelation.INVALIDATES,
            ),
            (
                LineageNodeType.EPISODE,
                second,
                LineageNodeType.EPISODE,
                first,
                LineageRelation.INVALIDATES,
            ),
        ],
    )
    client = make_client(factory, ids.editor_a)

    response = _graph(
        client,
        ids.project_a,
        LineageNodeType.EPISODE,
        first,
        direction="BOTH",
        depth=3,
        limit=100,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [node["node_id"] for node in body["nodes"]].count(str(first)) == 1
    assert [node["node_id"] for node in body["nodes"]].count(str(second)) == 1
    assert len(body["edges"]) == 2


def test_cursor_pagination_covers_each_edge_once(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    root = uuid4()
    targets = [uuid4() for _ in range(5)]
    _seed_edges(
        factory,
        ids,
        [
            (
                LineageNodeType.EPISODE,
                root,
                LineageNodeType.DATASET_VERSION,
                target,
                LineageRelation.INCLUDED_IN,
            )
            for target in targets
        ],
    )
    client = make_client(factory, ids.editor_a)

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, object] = {"direction": "OUT", "depth": 1, "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = _graph(client, ids.project_a, LineageNodeType.EPISODE, root, **params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(edge["edge_id"] for edge in body["edges"])
        cursor = body["next_cursor"]
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_invalid_cursor_is_unprocessable(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = _graph(
        client,
        ids.project_a,
        LineageNodeType.EPISODE,
        uuid4(),
        cursor="%not-a-cursor%",
    )
    assert response.status_code == 422, response.text


def test_cross_project_node_is_not_visible(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    foreign_root = uuid4()
    _seed_edges(
        factory,
        ids,
        [
            (
                LineageNodeType.EPISODE,
                foreign_root,
                LineageNodeType.DATASET_VERSION,
                uuid4(),
                LineageRelation.INCLUDED_IN,
            )
        ],
        project_id=ids.project_b,
        actor_id=ids.owner_a,
    )
    # owner_a 同属 A/B 两个项目：在 B 的路径下查 B 的节点可见，
    # 换到 A 的项目路径后同一节点必须不可见。
    owner = make_client(factory, ids.owner_a)
    visible = _graph(owner, ids.project_b, LineageNodeType.EPISODE, foreign_root, direction="OUT")
    assert visible.status_code == 200, visible.text
    assert visible.json()["root_found"] is True

    hidden = _graph(owner, ids.project_a, LineageNodeType.EPISODE, foreign_root, direction="OUT")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["root_found"] is False
    assert hidden.json()["nodes"] == []
    assert hidden.json()["edges"] == []


def test_non_member_is_forbidden(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    outsider = make_client(factory, ids.outsider)
    response = _graph(outsider, ids.project_a, LineageNodeType.EPISODE, uuid4())
    assert response.status_code == 403, response.text


def test_graph_query_is_read_only(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _asset, episode, _version, _run, specs = _chain()
    _seed_edges(factory, ids, specs)
    with factory() as session:
        before = session.scalar(select(func.count()).select_from(DataLineageEdgeModel))
    client = make_client(factory, ids.editor_a)
    response = _graph(client, ids.project_a, LineageNodeType.EPISODE, episode, depth=2)
    assert response.status_code == 200, response.text
    with factory() as session:
        after = session.scalar(select(func.count()).select_from(DataLineageEdgeModel))
    assert after == before


def _create_data_source(client: Any, project_id: UUID) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/data-sources",
        json={
            "code": f"hf-{uuid4().hex[:8]}",
            "name": "示例 HF 数据集",
            "repo_id": "lerobot/aloha_sim_insertion_human",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "license": "Apache-2.0",
            "access_condition": "PUBLIC",
            "is_gated": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_projected_data_source_import_edge_and_cross_project_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    # 项目 B 里登记一个数据源：owner_a 是 B 的成员，可以创建。
    owner = make_client(factory, ids.owner_a)
    source = _create_data_source(owner, ids.project_b)
    created = owner.post(
        f"/api/v1/projects/{ids.project_b}/data-sources/{source['data_source_id']}/imports",
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 3,
        },
    )
    assert created.status_code == 201, created.text
    import_id = created.json()["import_id"]

    # IN 方向从导入任务回溯：数据源 → 导入任务是只读投影边，不落库。
    traced = _graph(
        owner,
        ids.project_b,
        LineageNodeType.DATA_SOURCE_IMPORT,
        UUID(import_id),
        direction="IN",
        depth=1,
    )
    assert traced.status_code == 200, traced.text
    body = traced.json()
    assert body["root_found"] is True
    assert [(edge["from_node_type"], edge["to_node_type"]) for edge in body["edges"]] == [
        ("DATA_SOURCE", "DATA_SOURCE_IMPORT")
    ]

    # 同一数据源放到项目 A 的路径下查询：跨项目一律 404，不泄露存在性。
    hidden = _graph(
        owner,
        ids.project_a,
        LineageNodeType.DATA_SOURCE,
        UUID(source["data_source_id"]),
        direction="OUT",
    )
    assert hidden.status_code == 404, hidden.text


def test_projected_import_requires_authoritative_scope(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    # 项目 B 的项目行存在，但 editor_a 不是其成员：路径级权限先拒绝。
    client = make_client(factory, ids.editor_a)
    response = _graph(client, ids.project_b, LineageNodeType.DATA_SOURCE_IMPORT, uuid4())
    assert response.status_code == 403, response.text
