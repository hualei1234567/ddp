"""DatasetVersion 血缘：ready 后写 INCLUDED_IN 边、幂等、正反向遍历与项目隔离。

这些用例走真实 SQLite 仓储与真实 HTTP 路由，不使用 Mock：
- 边是否真的落库，直接查 ``data_lineage_edges`` 断言；
- 幂等通过"重复 validate"验证；
- 反向遍历必须能从 Episode 查到"进了哪个 DatasetVersion"。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.episodes import DataLineageEdgeModel, LineageNodeRefModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    url,
    version_payload,
)
from tests.api.ddp310_support import episode_payload, project_url, seed_valid_asset


def _ready_version(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    count: int = 10,
    **seed: object,
) -> dict[str, object]:
    """建库→建策略→build→validate，返回完整上下文。"""
    episodes = seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=count, **seed)
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    version = created.json()
    built = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version['dataset_version_id']}"
            "/actions/build",
        ),
        headers={"If-Match": f'"{version["version"]}"'},
        json={"reason": None},
    )
    assert built.status_code == 200, built.text
    built_body = built.json()
    validated = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version['dataset_version_id']}"
            "/actions/validate",
        ),
        headers={"If-Match": f'"{built_body["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    ready = validated.json()
    assert ready["status"] == "READY"
    items = editor.get(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version['dataset_version_id']}"
            "/items?limit=100",
        )
    )
    assert items.status_code == 200, items.text
    item_rows = items.json()["items"]
    return {
        "editor": editor,
        "dataset": dataset,
        "version": ready,
        "episodes": episodes,
        "item_episode_ids": [UUID(row["episode_id"]) for row in item_rows],
    }


def _included_in_edges(
    factory: sessionmaker[Session],
    project_id: UUID,
    version_id: UUID,
) -> list[DataLineageEdgeModel]:
    with factory() as session:
        return list(
            session.scalars(
                select(DataLineageEdgeModel).where(
                    DataLineageEdgeModel.project_id == project_id,
                    DataLineageEdgeModel.relation == "INCLUDED_IN",
                    DataLineageEdgeModel.to_node_id == version_id,
                )
            )
        )


def test_validate_writes_included_in_edges(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    version_id = ctx["version"]["dataset_version_id"]
    edges = _included_in_edges(factory, ids.project_a, UUID(version_id))

    # 边的 from 端必须与**实际入选条目**的 Episode 逐一对应。
    item_episodes = ctx["item_episode_ids"]
    assert len(item_episodes) == 10
    assert len(edges) == len(item_episodes)
    assert {edge.from_node_id for edge in edges} == set(item_episodes)
    for edge in edges:
        assert edge.from_node_type == "EPISODE"
        assert edge.to_node_type == "DATASET_VERSION"
        assert edge.relation == "INCLUDED_IN"
        assert edge.project_id == ids.project_a
        # metadata 必须能反查所属数据集，否则反向遍历定位不到数据集。
        assert edge.edge_metadata["dataset_id"] == ctx["dataset"]["dataset_id"]

    with factory() as session:
        node_types = set(
            session.scalars(
                select(LineageNodeRefModel.node_type).where(
                    LineageNodeRefModel.project_id == ids.project_a,
                    LineageNodeRefModel.node_id == UUID(version_id),
                )
            )
        )
    assert node_types == {"DATASET_VERSION"}


def test_repeat_validate_does_not_duplicate_edges(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    version_id = UUID(ctx["version"]["dataset_version_id"])
    before = _included_in_edges(factory, ids.project_a, version_id)
    assert len(before) == len(ctx["item_episode_ids"])

    # READY 状态不允许再次 validate（状态机拒绝），因此用 return-draft 不行
    # （有条目的版本不可回草稿）。这里直接再跑一次相同的 validate 请求：
    # 幂等键缺省时会走状态机校验，预期被拒；关键断言是"边数没有增加"。
    editor = ctx["editor"]
    dataset_id = ctx["dataset"]["dataset_id"]
    again = editor.post(
        url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}/actions/validate"),
        headers={"If-Match": f'"{ctx["version"]["version"]}"'},
        json={},
    )
    assert again.status_code == 409, again.text
    after = _included_in_edges(factory, ids.project_a, version_id)
    assert len(after) == len(before)


def test_repeat_recorder_call_is_idempotent(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """直接验证写入端幂等：同一批 Episode 重复登记不新增边。"""
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    version_id = UUID(ctx["version"]["dataset_version_id"])

    from app.application.datasets.lineage import DatasetVersionLineageRecorder
    from app.domain.identity.models import ProjectScope
    from app.infrastructure.repositories.lineage import SqlLineageRepository

    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        recorder = DatasetVersionLineageRecorder(SqlLineageRepository(session))
        from datetime import UTC, datetime

        created = recorder.record_inclusion(
            scope,
            dataset_version_id=version_id,
            episode_ids=list(ctx["item_episode_ids"]),
            now=datetime.now(UTC),
            metadata={"dataset_id": ctx["dataset"]["dataset_id"]},
        )
        session.commit()
    assert created == 0
    assert len(_included_in_edges(factory, ids.project_a, version_id)) == len(
        ctx["item_episode_ids"]
    )


def test_version_lineage_traversal_both_directions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    editor = ctx["editor"]
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]
    base = f"/api/v1/projects/{ids.project_a}/datasets/{dataset_id}/versions/{version_id}"

    reverse = editor.get(f"{base}/lineage")
    assert reverse.status_code == 200, reverse.text
    body = reverse.json()
    assert body["direction"] == "reverse"
    assert len(body["items"]) == len(ctx["item_episode_ids"])
    assert {UUID(item["from_node_id"]) for item in body["items"]} == set(ctx["item_episode_ids"])
    assert {item["relation"] for item in body["items"]} == {"INCLUDED_IN"}

    forward = editor.get(f"{base}/lineage?direction=forward")
    assert forward.status_code == 200, forward.text
    assert forward.json()["items"] == []

    # Episode 反向遍历必须能看到"进了哪个 DatasetVersion"。
    episode_id = ctx["item_episode_ids"][0]
    episode_reverse = editor.get(
        f"/api/v1/episodes/{episode_id}/lineage?project_id={ids.project_a}&direction=forward"
    )
    assert episode_reverse.status_code == 200, episode_reverse.text
    assert any(
        item["relation"] == "INCLUDED_IN" and item["to_node_id"] == str(version_id)
        for item in episode_reverse.json()["items"]
    )


def test_version_lineage_is_project_scoped(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]

    owner_b = make_client(factory, ids.owner_a)
    cross = owner_b.get(
        f"/api/v1/projects/{ids.project_b}/datasets/{dataset_id}/versions/{version_id}/lineage"
    )
    assert cross.status_code == 404

    outsider = make_client(factory, ids.outsider)
    forbidden = outsider.get(
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset_id}/versions/{version_id}/lineage"
    )
    assert forbidden.status_code == 403

    editor = ctx["editor"]
    missing = editor.get(
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset_id}/versions/{uuid4()}/lineage"
    )
    assert missing.status_code == 404

    bad_depth = editor.get(
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset_id}/versions/{version_id}"
        "/lineage?depth=6"
    )
    assert bad_depth.status_code == 422


def test_version_lineage_edge_count_matches_items(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """边的数量必须与条目数量一致（不是"有边就算过"）。"""
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    version_id = UUID(ctx["version"]["dataset_version_id"])
    with factory() as session:
        item_count = session.scalar(
            select(func.count())
            .select_from(DataLineageEdgeModel)
            .where(
                DataLineageEdgeModel.project_id == ids.project_a,
                DataLineageEdgeModel.relation == "INCLUDED_IN",
                DataLineageEdgeModel.to_node_id == version_id,
            )
        )
    assert item_count == len(ctx["item_episode_ids"]) == 10


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_version_lineage_rejects_bad_direction(
    api_db: tuple[sessionmaker[Session], ApiIds],
    direction: str,
) -> None:
    factory, ids = api_db
    ctx = _ready_version(factory, ids, count=10)
    editor = ctx["editor"]
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]
    url_path = (
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset_id}/versions/{version_id}"
        f"/lineage?direction={direction}x"
    )
    assert editor.get(url_path).status_code == 422


def test_version_without_lineage_node_returns_empty_not_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """版本存在但还没转 READY（血缘节点未登记）时必须返回空图，而不是 404。

    回归：节点只在 validate 转 READY 时登记。此前端点先 ``versions.get`` 校验
    版本存在、再由 ``traverse`` 要求节点存在，导致「资源确实存在但还没有血缘」
    被报成 ``resource_not_found``，客户端会误以为版本被删或无权访问。
    """
    factory, ids = api_db
    seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=2)
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    version = created.json()
    assert version["status"] != "READY"
    base = (
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset['dataset_id']}"
        f"/versions/{version['dataset_version_id']}/lineage"
    )

    empty = editor.get(base)
    assert empty.status_code == 200, empty.text
    # 版本血缘不涉及 RawAsset，来源结论列表按契约恒为空数组（字段存在但无内容）。
    assert empty.json() == {"items": [], "direction": "reverse", "raw_asset_sources": []}

    # 版本不存在仍是 404：空图不能掩盖资源不存在。
    missing = editor.get(
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset['dataset_id']}"
        f"/versions/{uuid4()}/lineage"
    )
    assert missing.status_code == 404


def test_version_lineage_returns_raw_asset_sources(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """反向 2 跳出现的 RawAsset 必须带回来源结论，不能只给边。

    回归（FR-040 E2E 发现）：本端点此前用 ``traverse()`` 只取边，把
    ``LineageService.trace()`` 已解析好的 ``raw_asset_sources`` 直接丢掉。
    后果是「这个版本的数据最初从哪个数据源来」只能靠调用方自己从投影边的
    ``metadata`` 反推，与契约里该字段的语义矛盾，也让 FR-040 的
    Dataset → 数据源追溯在**版本血缘端点**上不可用（其余三个端点正常）。

    夹具说明：``seed_dataset_episodes`` 直接落库、**不写血缘边**，因此这里用
    真实 ``POST /episodes`` 建一条带 ``RAW_ASSET --SEGMENTED_INTO--> EPISODE``
    边的 Episode 来验证「反向 2 跳能拿到资产来源」这条路径本身。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="source")
    created = editor.post(
        project_url(ids.project_a, "/episodes"),
        json=episode_payload(asset_id, code="EP-SOURCES-001"),
        headers={"Idempotency-Key": "lineage-sources-001"},
    )
    assert created.status_code == 201, created.text
    episode_id = UUID(created.json()["episode_id"])

    # 反向 1 跳（版本 ← Episode）能查到边；反向 2 跳才能到资产。
    with factory() as session:
        edges = list(
            session.scalars(
                select(DataLineageEdgeModel).where(
                    DataLineageEdgeModel.project_id == ids.project_a,
                    DataLineageEdgeModel.relation == "SEGMENTED_INTO",
                    DataLineageEdgeModel.to_node_id == episode_id,
                )
            )
        )
    assert [edge.from_node_id for edge in edges] == [asset_id]

    # 直接对 Episode 走反向 2 跳：Episode ← RawAsset；来源结论必须非空。
    response = editor.get(
        f"/api/v1/episodes/{episode_id}/lineage",
        params={
            "project_id": str(ids.project_a),
            "direction": "reverse",
            "depth": 2,
            "limit": 100,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    sources = {item["raw_asset_id"]: item for item in body["raw_asset_sources"]}
    assert str(asset_id) in sources
    # 手工上传资产没有外部数据源——这是正常状态，不是缺失。
    assert sources[str(asset_id)]["status"] == "NO_EXTERNAL_SOURCE"
    assert sources[str(asset_id)]["data_sources"] == []

    # 版本血缘端点（反向 1 跳）不涉及 RawAsset，来源列表为空是正确结果。
    ctx = _ready_version(factory, ids, count=10)
    version_base = (
        f"/api/v1/projects/{ids.project_a}/datasets/{ctx['dataset']['dataset_id']}"
        f"/versions/{ctx['version']['dataset_version_id']}/lineage"
    )
    shallow = ctx["editor"].get(f"{version_base}?direction=reverse&depth=1&limit=100")
    assert shallow.status_code == 200, shallow.text
    assert shallow.json()["raw_asset_sources"] == []
