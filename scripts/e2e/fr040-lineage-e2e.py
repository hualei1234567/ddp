#!/usr/bin/env python3
"""FR-040 全链路 Lineage E2E：在真实本地栈上验证「Dataset → Raw → 来源」双向可追溯。

**这条脚本证明什么**

在**真实 PostgreSQL（迁移到 head）+ 真实 MinIO** 上，用**已有真实数据**（不新建、
不写入、不改库）走完 4 个血缘查询端点，逐段断言双向可追溯：

1. ``DatasetVersion → Episode``：反向 1 跳的边集合必须与版本条目集合逐一相等；
2. ``DatasetVersion → RawAsset``：反向 2 跳必须拿到「这个版本用了哪些原始资产」；
3. ``RawAsset → Episode/DatasetVersion``：正向必须能回答「这条资产被谁用了」；
4. ``RawAsset → DATA_SOURCE_IMPORT → DATA_SOURCE``：反向 2~3 跳必须追到数据源链；
5. graph 端点：节点/边自洽（无悬空边、无重复边、``level`` 与边方向一致），
   以及**它到不了的那一跳**（``DATA_SOURCE`` 在 4 跳之外，硬上限 3）如实记录；
6. ``Session`` 段：采集会话（``capture_session_id``）与上传会话
   （``upload_session_id``/``raw_asset_links``）能否从 Dataset 追到，**按真实数据
   判定**，追不到就明确 FAIL 并打印缺口数字，不粉饰。

**这条脚本不证明什么**

- 不证明 staging / 生产可用（本机 compose 栈）；
- 不证明数据内容语义正确（只证明血缘关系与库内权威事实一致）；
- 不新建业务数据：只读。想验证「新产生的数据也有血缘」请跑
  ``backend/tests/e2e`` 或 ``scripts/seed/run.py``。

**用法**::

    backend/.venv/bin/python scripts/e2e/fr040-lineage-e2e.py

前置：``docker compose up -d postgres minio`` 且 ``alembic upgrade head``。
可选环境变量：``DDP_DATABASE_URL`` / ``DDP_LINEAGE_PROJECT_ID``（锁定项目）。

退出码：0 = 全段 PASS（含 Session 链路可用）；1 = 存在真实缺陷（FAIL）；
2 = 无缺陷但有段无数据可验（PARTIAL，不得据此宣称完整闭环）。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data-platform"))


def _apply_default_env() -> None:
    """默认环境变量只能在 main() 里设置，避免污染导入本模块的进程。"""
    os.environ.setdefault("DDP_ENV", "local")
    os.environ.setdefault(
        "DDP_DATABASE_URL", "postgresql+psycopg://ddp:ddp@localhost:55432/ddp"
    )
    os.environ.setdefault("DDP_S3_ENDPOINT", "http://127.0.0.1:9000")
    os.environ.setdefault("DDP_S3_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("DDP_S3_SECRET_KEY", "minioadmin")
    os.environ.setdefault("DDP_S3_BUCKET", "ddp-raw")
    os.environ.setdefault("DDP_RATE_LIMIT_ENABLED", "false")


# ---------------------------------------------------------------------------
# 断言与报告骨架
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """一段可追溯性结论：PASS / FAIL / UNVERIFIABLE（缺数据）。"""

    key: str
    title: str
    status: str = "PASS"
    detail: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.status = "FAIL"
        self.detail.append(message)

    def unverifiable(self, message: str) -> None:
        # 缺数据不是通过：状态单列，退出码仍然非零，避免"没数据=绿灯"。
        if self.status != "FAIL":
            self.status = "UNVERIFIABLE"
        self.detail.append(message)

    def info(self, message: str) -> None:
        self.detail.append(message)


@dataclass
class Report:
    segments: list[Segment] = field(default_factory=list)

    def segment(self, key: str, title: str) -> Segment:
        item = Segment(key=key, title=title)
        self.segments.append(item)
        return item

    @property
    def failed(self) -> list[Segment]:
        return [item for item in self.segments if item.status == "FAIL"]

    @property
    def unverifiable(self) -> list[Segment]:
        return [item for item in self.segments if item.status == "UNVERIFIABLE"]


def _pairs(edges: list[dict[str, Any]], relation: str) -> set[tuple[str, str]]:
    return {
        (str(edge["from_node_id"]), str(edge["to_node_id"]))
        for edge in edges
        if edge["relation"] == relation
    }


def _dangling_edges(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """边的两端都必须在本页节点里；否则客户端拼图会断线。"""
    keys = {(node["node_type"], node["node_id"]) for node in nodes}
    return [
        edge
        for edge in edges
        if (edge["from_node_type"], edge["from_node_id"]) not in keys
        or (edge["to_node_type"], edge["to_node_id"]) not in keys
    ]


# ---------------------------------------------------------------------------
# 发现一条真实链路
# ---------------------------------------------------------------------------


def _dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://")


@dataclass(frozen=True)
class Chain:
    """一条真实链路的关键 ID。"""

    project_id: UUID
    project_code: str
    dataset_id: UUID
    version_id: UUID
    episode_ids: list[UUID]
    asset_ids: list[UUID]
    user_id: UUID
    source_type: str | None


def _pick_user(conn: Any, project_id: UUID) -> UUID | None:
    row = conn.execute(
        """
        select user_id from project_members
        where project_id = %s
        order by case role when 'owner' then 0 when 'editor' then 1 else 2 end, user_id
        limit 1
        """,
        (project_id,),
    ).fetchone()
    return UUID(str(row[0])) if row else None


def _discover_chain(conn: Any, *, project_id: UUID | None) -> Chain | None:
    """找一条「版本 → Episode → RawAsset」齐全且属于同一项目的真实链路。

    优先挑**来源边最少**的版本：导入历史里有重复导入，同一资产可能挂着几十条
    ``data_source_media_assets``；边数少的链路才能在 ``limit`` 预算内追到数据源。
    """
    sql = """
        select dv.project_id, p.code, dv.dataset_id, dv.id as version_id,
               count(distinct inc.from_node_id) as episode_count
        from dataset_versions dv
        join projects p on p.id = dv.project_id
        join data_lineage_edges inc
          on inc.project_id = dv.project_id
         and inc.to_node_id = dv.id
         and inc.relation = 'INCLUDED_IN'
        where dv.status = 'FROZEN'
          and (%(project)s::uuid is null or dv.project_id = %(project)s::uuid)
        group by dv.project_id, p.code, dv.dataset_id, dv.id
        having count(distinct inc.from_node_id) > 0
        order by episode_count asc, dv.created_at desc
        limit 40
    """
    rows = conn.execute(sql, {"project": project_id}).fetchall()
    best: Chain | None = None
    best_score: tuple[int, int] | None = None
    for project, code, dataset_id, version_id, _episode_count in rows:
        episodes = [
            UUID(str(row[0]))
            for row in conn.execute(
                """
                select distinct from_node_id from data_lineage_edges
                where project_id = %s and to_node_id = %s and relation = 'INCLUDED_IN'
                """,
                (project, version_id),
            ).fetchall()
        ]
        assets: set[UUID] = set()
        for episode in episodes:
            for row in conn.execute(
                """
                select distinct from_node_id from data_lineage_edges
                where project_id = %s and to_node_id = %s and relation = 'SEGMENTED_INTO'
                """,
                (project, episode),
            ).fetchall():
                assets.add(UUID(str(row[0])))
        if not assets:
            continue
        media_rows = conn.execute(
            "select count(*) from data_source_media_assets where raw_asset_id = any(%s)",
            (list(assets),),
        ).fetchone()
        score = (int(media_rows[0]) if media_rows else 0, len(assets))
        if best_score is None or score < best_score:
            user_id = _pick_user(conn, UUID(str(project)))
            if user_id is None:
                continue
            source_row = conn.execute(
                "select distinct source_type from raw_assets where id = any(%s)",
                (list(assets),),
            ).fetchall()
            source_type = str(source_row[0][0]) if len(source_row) == 1 else None
            best_score = score
            best = Chain(
                project_id=UUID(str(project)),
                project_code=str(code),
                dataset_id=UUID(str(dataset_id)),
                version_id=UUID(str(version_id)),
                episode_ids=sorted(episodes, key=str),
                asset_ids=sorted(assets, key=str),
                user_id=user_id,
                source_type=source_type,
            )
    return best


# ---------------------------------------------------------------------------
# 各段验证
# ---------------------------------------------------------------------------


def _make_client(chain: Chain):
    from app.core.config import get_settings
    from app.main import create_app

    import jwt
    from fastapi.testclient import TestClient

    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(chain.user_id),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    client = TestClient(create_app())
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, settings


def _version_lineage(
    client: Any, chain: Chain, *, direction: str, depth: int, limit: int = 100
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/projects/{chain.project_id}/datasets/{chain.dataset_id}"
        f"/versions/{chain.version_id}/lineage",
        params={"direction": direction, "depth": depth, "limit": limit},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _episode_lineage(
    client: Any,
    chain: Chain,
    episode_id: UUID,
    *,
    direction: str,
    depth: int,
    limit: int = 100,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/episodes/{episode_id}/lineage",
        params={
            "project_id": str(chain.project_id),
            "direction": direction,
            "depth": depth,
            "limit": limit,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _asset_lineage(
    client: Any,
    chain: Chain,
    asset_id: UUID,
    *,
    direction: str,
    depth: int,
    limit: int = 100,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/raw-assets/{asset_id}/lineage",
        params={
            "project_id": str(chain.project_id),
            "direction": direction,
            "depth": depth,
            "limit": limit,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _graph(
    client: Any,
    chain: Chain,
    *,
    node_type: str,
    node_id: UUID,
    direction: str,
    depth: int,
    limit: int = 100,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/projects/{chain.project_id}/lineage/graph",
        params={
            "node_type": node_type,
            "node_id": str(node_id),
            "direction": direction,
            "depth": depth,
            "limit": limit,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _check_dataset_to_episode(client: Any, chain: Chain, report: Report) -> None:
    segment = report.segment(
        "dataset_to_episode", "DatasetVersion → Episode（反向 1 跳）"
    )
    body = _version_lineage(client, chain, direction="reverse", depth=1)
    edges = body["items"]
    reached = _pairs(edges, "INCLUDED_IN")
    expected = {(str(episode), str(chain.version_id)) for episode in chain.episode_ids}
    segment.facts.update(expected_episodes=len(expected), returned_edges=len(edges))
    if {edge[0] for edge in reached} != {item[0] for item in expected}:
        segment.fail(
            "反向 1 跳的 Episode 集合与版本条目不一致："
            f"血缘 {len({e[0] for e in reached})} 个 / 库内 {len(expected)} 个"
        )
    if {item["relation"] for item in edges} != {"INCLUDED_IN"}:
        segment.fail(
            f"方向语义错误：反向 1 跳出现了非 INCLUDED_IN 的关系 {sorted({i['relation'] for i in edges})}"
        )
    segment.info(f"返回 {len(edges)} 条 INCLUDED_IN 边，覆盖 {len(reached)} 个 Episode")


def _check_dataset_to_raw(client: Any, chain: Chain, report: Report) -> None:
    segment = report.segment("dataset_to_raw", "DatasetVersion → RawAsset（反向 2 跳）")
    body = _version_lineage(client, chain, direction="reverse", depth=2)
    edges = body["items"]
    raw_pairs = _pairs(edges, "SEGMENTED_INTO")
    reached_assets = {item[0] for item in raw_pairs}
    segment.facts.update(
        expected_assets=len(chain.asset_ids), returned_assets=len(reached_assets)
    )
    missing = {str(item) for item in chain.asset_ids} - reached_assets
    if missing:
        segment.fail(f"反向 2 跳漏掉 {len(missing)} 个原始资产：{sorted(missing)[:3]}")
    # 段内必须每一步都能续上：EPISODE→DATASET_VERSION 与 RAW_ASSET→EPISODE 的端点要自洽。
    included = _pairs(edges, "INCLUDED_IN")
    episode_nodes = {item[0] for item in included}
    if not {item[1] for item in raw_pairs} <= episode_nodes:
        segment.fail("RAW_ASSET 挂到了不属于本版本的 Episode 上（反向 2 跳端点不连续）")
    # 版本血缘端点必须带回 raw_asset_sources：这是「这些资产最初从哪来」的直接
    # 答案，丢掉它就得让调用方自己从投影边 metadata 反推（FR-040 的
    # Dataset → 数据源追溯依赖该字段）。
    origins = {
        str(item["raw_asset_id"]): item for item in body.get("raw_asset_sources") or []
    }
    segment.facts["version_endpoint_origins"] = len(origins)
    missing_origin = {str(asset) for asset in chain.asset_ids} - set(origins)
    if missing_origin:
        segment.fail(
            f"版本血缘端点反向 2 跳漏掉 {len(missing_origin)} 个资产的 raw_asset_sources："
            f"{sorted(missing_origin)[:3]}"
        )
    unresolved = [
        asset
        for asset in chain.asset_ids
        if origins.get(str(asset), {}).get("status")
        not in {"RESOLVED", "NO_EXTERNAL_SOURCE"}
    ]
    if unresolved:
        segment.fail(
            f"{len(unresolved)} 个资产的来源结论不是 RESOLVED/NO_EXTERNAL_SOURCE"
        )
    segment.info(
        f"返回 {len(edges)} 条边，覆盖 {len(reached_assets)} 个原始资产 / {len(episode_nodes)} 个 Episode，"
        f"raw_asset_sources {len(origins)} 条（全部有来源结论）"
    )


def _check_dataset_to_source(client: Any, chain: Chain, report: Report) -> None:
    """Dataset → 数据源链：从 DatasetVersion 直接遍历**追不到**，用两段组合验证。

    跳数账：``DATASET_VERSION ← EPISODE ← RAW_ASSET ← DATA_SOURCE_IMPORT ←
    DATA_SOURCE`` 共 4 跳，而逐节点端点的 ``depth`` 上限是 5、``limit`` 上限
    100。理论上够，实际不够：同一资产在导入历史里挂着几十条
    ``data_source_media_assets``（重复导入），第 3 层一跳就有 80+ 条同名导入边，
    把 100 的边预算吃光，数据源那一层被挤掉。

    因此本段如实拆成两段组合（与 Session 段同一处理方式）：
    ``版本 --反向2跳--> 资产`` + ``资产 --反向2跳--> 数据源``。两段都真实执行、
    真实断言，并在报告里明确标出"单次遍历追不到"，不把组合说成一步。
    """
    segment = report.segment(
        "dataset_to_source",
        "DatasetVersion → DATA_SOURCE（两段组合：版本→资产→数据源）",
    )
    single = _version_lineage(client, chain, direction="reverse", depth=5, limit=100)
    single_types = {item["from_node_type"] for item in single["items"]} | {
        item["to_node_type"] for item in single["items"]
    }
    segment.facts["single_traversal_node_types"] = sorted(single_types)
    segment.facts["single_traversal_edges"] = len(single["items"])
    if "DATA_SOURCE" in single_types:
        segment.info(
            "从 DatasetVersion 单次遍历（depth=5/limit=100）已追到 DATA_SOURCE"
        )
    else:
        segment.info(
            "从 DatasetVersion 单次遍历（depth=5/limit=100）只到 DATA_SOURCE_IMPORT："
            f"需 4 跳，第 3 层被 {len(single['items'])} 条同名导入边吃满预算；"
            "改用「版本→资产」「资产→数据源」两段组合验证"
        )

    if chain.source_type != "IMPORT":
        segment.unverifiable(
            f"该链路资产的 source_type={chain.source_type}（非 IMPORT），没有数据源链可追"
        )
        return

    # 第一段：版本反向 2 跳 → 资产（dataset_to_raw 段已完整验证集合，这里取交集口径）。
    version_body = _version_lineage(
        client, chain, direction="reverse", depth=2, limit=100
    )
    reached_assets = {
        item[0] for item in _pairs(version_body["items"], "SEGMENTED_INTO")
    }
    expected_assets = {str(item) for item in chain.asset_ids}
    if expected_assets - reached_assets:
        segment.fail(
            f"第一段（版本→资产）漏掉 {len(expected_assets - reached_assets)} 个资产"
        )
        return

    # 第二段：每个资产反向 2 跳 → 导入 → 数据源。
    failures: list[str] = []
    sources: set[str] = set()
    imports: set[str] = set()
    for asset in chain.asset_ids:
        body = _asset_lineage(
            client, chain, asset, direction="reverse", depth=2, limit=100
        )
        items = body["items"]
        import_edges = [
            item
            for item in items
            if item["from_node_type"] == "DATA_SOURCE_IMPORT"
            and item["to_node_type"] == "RAW_ASSET"
            and item["to_node_id"] == str(asset)
        ]
        source_edges = [
            item
            for item in items
            if item["from_node_type"] == "DATA_SOURCE"
            and item["to_node_type"] == "DATA_SOURCE_IMPORT"
        ]
        if not import_edges:
            failures.append(
                f"资产 {asset} 反向 2 跳没有 DATA_SOURCE_IMPORT → RAW_ASSET 边"
            )
            continue
        if not source_edges:
            failures.append(
                f"资产 {asset} 反向 2 跳没有 DATA_SOURCE → DATA_SOURCE_IMPORT 边"
            )
            continue
        import_ids = {item["from_node_id"] for item in import_edges}
        if not {item["to_node_id"] for item in source_edges} & import_ids:
            failures.append(f"资产 {asset} 的数据源边挂在不属于该资产的导入任务上")
        imports |= import_ids
        sources |= {item["from_node_id"] for item in source_edges}
        # 投影边必须标明派生，否则与持久化边无法区分。
        if not all(
            item["metadata"].get("derived") is True
            for item in import_edges + source_edges
        ):
            failures.append(f"资产 {asset} 的数据源链边缺少 derived 标记")
        origins = {
            str(item["raw_asset_id"]): item
            for item in body.get("raw_asset_sources") or []
        }
        origin = origins.get(str(asset))
        if origin is None or origin["status"] != "RESOLVED":
            failures.append(f"资产 {asset} 的来源结论不是 RESOLVED：{origin}")
        elif not origin["data_sources"]:
            failures.append(f"资产 {asset} 的 RESOLVED 结论没有 data_sources 明细")

    segment.facts.update(
        assets_checked=len(chain.asset_ids),
        distinct_data_sources=len(sources),
        distinct_imports=len(imports),
        data_source_ids=sorted(sources)[:5],
    )
    if failures:
        for message in failures:
            segment.fail(message)
        return
    segment.info(
        f"{len(chain.asset_ids)} 个资产各自反向 2 跳追到 {len(imports)} 个导入任务 / "
        f"{len(sources)} 个数据源，来源结论全部 RESOLVED"
    )

    # graph 端点在同一根上也要能到 DATA_SOURCE（depth=2 上限内）。
    graph = _graph(
        client,
        chain,
        node_type="RAW_ASSET",
        node_id=chain.asset_ids[0],
        direction="IN",
        depth=2,
        limit=100,
    )
    graph_types = {node["node_type"] for node in graph["nodes"]}
    segment.facts["graph_asset_root_node_types"] = sorted(graph_types)
    if "DATA_SOURCE" not in graph_types:
        segment.fail("graph 以资产为根、depth=2 也追不到 DATA_SOURCE")
    else:
        segment.info("graph 以资产为根、direction=IN、depth=2 可达 DATA_SOURCE")


def _check_raw_to_downstream(client: Any, chain: Chain, report: Report) -> None:
    segment = report.segment(
        "raw_to_downstream", "RawAsset → Episode / DatasetVersion（正向）"
    )
    failures: list[str] = []
    for asset in chain.asset_ids:
        body = _asset_lineage(
            client, chain, asset, direction="forward", depth=2, limit=100
        )
        edges = body["items"]
        episodes = {item[1] for item in _pairs(edges, "SEGMENTED_INTO")}
        if not episodes:
            failures.append(f"资产 {asset} 正向查不到任何 Episode")
            continue
        if not episodes & {str(item) for item in chain.episode_ids}:
            failures.append(f"资产 {asset} 正向的 Episode 不属于目标版本")
        versions = {item[1] for item in _pairs(edges, "INCLUDED_IN")}
        if str(chain.version_id) not in versions:
            failures.append(
                f"资产 {asset} 正向 depth=2 没到目标版本（到了 {len(versions)} 个别处版本）"
            )
        origins = body.get("raw_asset_sources") or []
        if len(origins) != 1 or origins[0]["status"] not in {
            "RESOLVED",
            "NO_EXTERNAL_SOURCE",
        }:
            failures.append(f"资产 {asset} 的来源结论异常：{origins}")
    segment.facts.update(assets_checked=len(chain.asset_ids))
    if failures:
        for message in failures:
            segment.fail(message)
    else:
        segment.info(
            f"{len(chain.asset_ids)} 个资产正向都能追到 Episode 与目标 DatasetVersion"
        )


def _check_graph_consistency(client: Any, chain: Chain, report: Report) -> None:
    segment = report.segment("graph_consistency", "graph 端点自洽性与硬上限")
    for depth, limit in ((2, 100), (3, 100)):
        body = _graph(
            client,
            chain,
            node_type="DATASET_VERSION",
            node_id=chain.version_id,
            direction="IN",
            depth=depth,
            limit=limit,
        )
        nodes, edges = body["nodes"], body["edges"]
        dangling = _dangling_edges(nodes, edges)
        if dangling:
            segment.fail(
                f"depth={depth} 存在 {len(dangling)} 条悬空边（端点不在 nodes 里）"
            )
        ids = [edge["edge_id"] for edge in edges]
        if len(ids) != len(set(ids)):
            segment.fail(f"depth={depth} 存在重复边")
        if len(edges) > limit:
            segment.fail(f"depth={depth} 返回 {len(edges)} 条边，超过 limit={limit}")
        if not body["root_found"]:
            segment.fail(f"depth={depth} root_found=false，起点未被识别")
        levels = {node["node_id"]: node["level"] for node in nodes}
        if levels.get(str(chain.version_id)) != 0:
            segment.fail(f"depth={depth} 起点 level 不是 0")
        for edge in edges:
            from_level = levels.get(edge["from_node_id"])
            to_level = levels.get(edge["to_node_id"])
            if (
                from_level is not None
                and to_level is not None
                and abs(from_level - to_level) > 1
            ):
                segment.fail(f"depth={depth} 边跨层：{from_level} -> {to_level}")
        segment.facts[f"depth{depth}"] = {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": sorted({node["node_type"] for node in nodes}),
            "truncated": body["truncated"],
            "next_cursor": body["next_cursor"] is not None,
        }
    dense = _graph(
        client,
        chain,
        node_type="DATASET_VERSION",
        node_id=chain.version_id,
        direction="IN",
        depth=3,
        limit=100,
    )
    node_types = {node["node_type"] for node in dense["nodes"]}
    segment.facts["reachable_node_types_at_depth3"] = sorted(node_types)
    if "DATA_SOURCE" in node_types:
        segment.info("graph depth=3 已能追到 DATA_SOURCE")
    else:
        segment.info(
            "graph 硬上限 depth=3，从 DatasetVersion 向上一共需要 4 跳才能到 DATA_SOURCE，"
            "因此该端点按设计追不到数据源；数据源段由逐节点端点（depth≤5）承担"
        )
    if (
        not dense["truncated"]
        and dense["next_cursor"] is None
        and len(dense["edges"]) >= 100
    ):
        segment.fail(
            "边数触到 limit 却既没有 truncated 也没有 next_cursor：截断被伪装成完整图"
        )
    print(
        "  graph depth=3: "
        f"nodes={len(dense['nodes'])} edges={len(dense['edges'])} "
        f"truncated={dense['truncated']} next_cursor={dense['next_cursor'] is not None}"
    )


def _check_session_traceability(
    client: Any, chain: Chain, conn: Any, report: Report
) -> None:
    """Session 段：Dataset 能不能追到采集会话 / 上传会话。

    Session 节点在血缘图里位于**资产下游**（``RAW_ASSET --UPLOADED_BY-->
    UPLOAD_SESSION``，沿用领域既定方向"资产由会话登记"）。因此从
    DatasetVersion 追会话是**两段组合**：
    ``版本 --反向--> 资产 --正向--> 会话``。

    本段分两层结论，**不合并**：

    - 机制层：库里存在带上传会话的资产时，投影边必须真实返回（含
      ``derived`` 标记、确定性 id、采集会话 ID 透出）；这部分有数据就严格断言；
    - 链路层：``DatasetVersion → Session`` 需要该**数据集链路上的**资产或 Episode
      带会话归属；本库没有这样的数据就如实标 UNVERIFIABLE，不拿机制层的 PASS
      冒充链路层通过。
    """
    segment = report.segment(
        "dataset_to_session", "DatasetVersion → Session（采集/上传会话）"
    )

    asset_list = list(chain.asset_ids)
    capture_rows = conn.execute(
        "select id, capture_session_id from episodes where id = any(%s)",
        (list(chain.episode_ids),),
    ).fetchall()
    episodes_with_capture = [row for row in capture_rows if row[1] is not None]
    chain_asset_sessions = conn.execute(
        """
        select distinct a.id, l.upload_session_id
        from raw_assets a
        join raw_asset_links l
          on l.project_id = a.project_id and l.asset_id = a.id
         and l.upload_session_id is not null and l.link_status <> 'REVOKED'
        where a.id = any(%s)
        """,
        (asset_list,),
    ).fetchall()
    segment.facts.update(
        version_episodes=len(chain.episode_ids),
        episodes_with_capture_session=len(episodes_with_capture),
        chain_assets_with_upload_session=len(chain_asset_sessions),
        assets_checked=len(asset_list),
    )

    # ---- 机制层：库内任意带会话的真实资产（不限定在本链路上）----
    project_session_assets = conn.execute(
        """
        select distinct a.project_id, a.id, l.upload_session_id
        from raw_assets a
        join raw_asset_links l
          on l.project_id = a.project_id and l.asset_id = a.id
         and l.upload_session_id is not null and l.link_status <> 'REVOKED'
        where a.project_id = %s
        order by a.id
        limit 5
        """,
        (chain.project_id,),
    ).fetchall()
    segment.facts["project_assets_with_upload_session"] = len(project_session_assets)
    mechanism_checked = 0
    for _project, asset_id, session_id in project_session_assets:
        body = _asset_lineage(
            client, chain, asset_id, direction="forward", depth=1, limit=100
        )
        edges = [item for item in body["items"] if item["relation"] == "UPLOADED_BY"]
        matching = [item for item in edges if item["to_node_id"] == str(session_id)]
        if not matching:
            segment.fail(
                f"资产 {asset_id} 在 raw_asset_links 里有会话 {session_id}，"
                "但血缘正向 1 跳没有对应的 UPLOADED_BY 投影边"
            )
            return
        edge = matching[0]
        if edge["metadata"].get("derived") is not True:
            segment.fail(f"资产 {asset_id} 的 UPLOADED_BY 边缺少 derived 标记")
            return
        if (
            edge["from_node_type"] != "RAW_ASSET"
            or edge["to_node_type"] != "UPLOAD_SESSION"
        ):
            segment.fail(f"UPLOADED_BY 边端点类型错误：{edge}")
            return
        again = _asset_lineage(
            client, chain, asset_id, direction="forward", depth=1, limit=100
        )
        again_ids = {
            item["edge_id"]
            for item in again["items"]
            if item["relation"] == "UPLOADED_BY"
            and item["to_node_id"] == str(session_id)
        }
        if edge["edge_id"] not in again_ids:
            segment.fail("UPLOADED_BY 投影边 id 不确定：同一端点两次查询给出不同 id")
            return
        mechanism_checked += 1
    segment.facts["projection_mechanism_checked"] = mechanism_checked

    # ---- 链路层：本数据集链路上是否真的有会话归属 ----
    if not episodes_with_capture and not chain_asset_sessions:
        if mechanism_checked:
            segment.info(
                f"机制层 PASS：项目内 {mechanism_checked} 个资产的 UPLOADED_BY 投影边真实返回"
                "（含 derived、确定性 id）"
            )
        segment.unverifiable(
            "链路层无数据：该数据集链路的 Episode 与 RawAsset 都没有 Session 归属"
            f"（Episode.capture_session_id 非空 0/{len(chain.episode_ids)}；"
            f"链路上 RawAsset 关联上传会话 0/{len(asset_list)}），"
            "因此「DatasetVersion → Session」这一段在本库无法验证，"
            "不能拿机制层的通过冒充链路层通过"
        )
        return

    # 链路层有数据 → 必须经「版本反向→资产，资产正向→会话」两段组合查到。
    version_body = _version_lineage(
        client, chain, direction="reverse", depth=2, limit=100
    )
    reached_assets = {
        item[0] for item in _pairs(version_body["items"], "SEGMENTED_INTO")
    }
    session_edges: list[dict[str, Any]] = []
    for asset in reached_assets:
        body = _asset_lineage(
            client, chain, UUID(asset), direction="forward", depth=1, limit=100
        )
        session_edges.extend(
            [item for item in body["items"] if item["relation"] == "UPLOADED_BY"]
        )
    segment.facts["uploaded_by_edges_via_chain"] = len(session_edges)
    expected = {str(row[1]) for row in chain_asset_sessions}
    got = {item["to_node_id"] for item in session_edges}
    if expected - got:
        segment.fail(f"链路层漏掉 {len(expected - got)} 个会话的投影边")
        return
    capture_ids = {
        item["metadata"].get("capture_session_id")
        for item in session_edges
        if item["metadata"].get("capture_session_id")
    }
    segment.facts["capture_sessions_via_metadata"] = len(capture_ids)
    segment.info(
        f"链路层 PASS：{len(reached_assets)} 个资产正向得到 "
        f"{len(session_edges)} 条 UPLOADED_BY 边，覆盖 {len(got)} 个上传会话，"
        f"其中 {len(capture_ids)} 个带采集会话 ID（经 metadata 透出）"
    )


def _check_cross_project(client: Any, chain: Chain, conn: Any, report: Report) -> None:
    segment = report.segment("cross_project", "跨项目隔离（不泄露存在性）")
    row = conn.execute(
        "select id from projects where id <> %s limit 1", (chain.project_id,)
    ).fetchone()
    if row is None:
        segment.unverifiable("库里只有一个项目，无法验证跨项目隔离")
        return
    other = UUID(str(row[0]))
    response = client.get(
        f"/api/v1/projects/{other}/datasets/{chain.dataset_id}"
        f"/versions/{chain.version_id}/lineage"
    )
    # 403 = 不是该项目的成员（无成员资格先于资源校验）；404 = 是成员但资源
    # 不在该项目。两者都**不泄露资源是否存在**，按契约语义分别合法。
    if response.status_code not in {403, 404}:
        segment.fail(
            f"跨项目版本血缘期望 403/404，实际 {response.status_code}：{response.text[:200]}"
        )
    for identifier in (str(chain.version_id), str(chain.dataset_id)):
        if identifier in response.text:
            segment.fail("错误响应体泄露了本项目资源 ID")
    graph_response = client.get(
        f"/api/v1/projects/{other}/lineage/graph",
        params={
            "node_type": "DATASET_VERSION",
            "node_id": str(chain.version_id),
            "direction": "IN",
            "depth": 1,
        },
    )
    if graph_response.status_code not in {403, 404}:
        segment.fail(f"跨项目图查询期望 403/404，实际 {graph_response.status_code}")
    segment.info(
        f"项目 {other} 下查本项目版本血缘 → {response.status_code}；"
        f"graph → {graph_response.status_code}"
    )


def _check_minio_objects(chain: Chain, conn: Any, report: Report) -> None:
    """真实 MinIO：血缘里的 RawAsset 对象必须真的在对象存储里。"""
    segment = report.segment("object_storage", "RawAsset → MinIO 对象存在")
    keys = conn.execute(
        "select id, object_key, size_bytes from raw_assets where id = any(%s)",
        (list(chain.asset_ids),),
    ).fetchall()
    segment.facts.update(objects=len(keys))
    try:
        from app.core.config import get_settings
        from app.infrastructure.storage.s3 import S3MultipartStorage

        settings = get_settings()
        storage = S3MultipartStorage(settings)
        for asset_id, object_key, size_bytes in keys:
            stored = storage.object_size(object_key)
            if stored is None:
                segment.fail(f"资产 {asset_id} 的对象 {object_key} 在存储里不存在")
                continue
            if int(size_bytes) and stored != int(size_bytes):
                segment.fail(
                    f"资产 {asset_id} 的库内大小 {size_bytes} 与存储 {stored} 不一致"
                )
    except Exception as exc:  # noqa: BLE001 - 验收脚本需要原始失败原因
        segment.unverifiable(f"对象存储不可用：{exc}")
        return
    if not segment.detail:
        segment.info(f"{len(keys)} 个资产对象均在 MinIO，且大小与库内一致")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _print_report(report: Report) -> None:
    print()
    print("=" * 78)
    print("FR-040 全链路 Lineage E2E 结论")
    print("=" * 78)
    for item in report.segments:
        print(f"[{item.status:12}] {item.title}")
        for line in item.detail:
            print(f"               · {line}")
        if item.facts:
            print(
                f"               facts: {json.dumps(item.facts, ensure_ascii=False, default=str)}"
            )
    print("-" * 78)
    passed = [item for item in report.segments if item.status == "PASS"]
    print(
        f"PASS {len(passed)} / FAIL {len(report.failed)} / "
        f"UNVERIFIABLE {len(report.unverifiable)}（共 {len(report.segments)} 段）"
    )
    if report.failed:
        print("失败段：" + "、".join(item.key for item in report.failed))
    if report.unverifiable:
        print("无数据段：" + "、".join(item.key for item in report.unverifiable))


def main() -> int:
    _apply_default_env()
    import psycopg
    from app.core.config import get_settings

    settings = get_settings()
    print("=" * 78)
    print("FR-040 全链路 Lineage E2E（真实 PostgreSQL + 真实 MinIO）")
    print(f"  database = {settings.database_url.split('@')[-1]}")
    print(f"  s3       = {settings.s3_endpoint} / bucket={settings.s3_bucket}")
    print("=" * 78)

    locked = os.environ.get("DDP_LINEAGE_PROJECT_ID", "").strip()
    project_id = UUID(locked) if locked else None
    report = Report()
    with psycopg.connect(_dsn(settings.database_url), connect_timeout=10) as conn:
        migration = conn.execute("select version_num from alembic_version").fetchone()
        print(f"迁移版本 = {migration[0] if migration else 'N/A'}")
        chain = _discover_chain(conn, project_id=project_id)
        if chain is None:
            print(
                "[FAIL] 库里没有任何「Frozen DatasetVersion → Episode → RawAsset」完整链路"
            )
            print(
                "       请先跑 scripts/seed/run.py 或 tests/e2e（真实 PG 模式）造链。"
            )
            return 2
        print(
            f"目标链路：项目 {chain.project_code} ({chain.project_id}) / "
            f"版本 {chain.version_id} / {len(chain.episode_ids)} Episode / "
            f"{len(chain.asset_ids)} RawAsset / 资产来源 {chain.source_type}"
        )
        client, _settings = _make_client(chain)

        _check_dataset_to_episode(client, chain, report)
        _check_dataset_to_raw(client, chain, report)
        _check_raw_to_downstream(client, chain, report)
        _check_dataset_to_source(client, chain, report)
        _check_graph_consistency(client, chain, report)
        _check_session_traceability(client, chain, conn, report)
        _check_cross_project(client, chain, conn, report)
        _check_minio_objects(chain, conn, report)

    _print_report(report)
    if report.failed:
        print("\n结论：FAIL —— 存在真实缺陷（见上面的失败段）。退出码 1。")
        return 1
    if report.unverifiable:
        print(
            "\n结论：PARTIAL —— 已验证的段全部 PASS，但以下段在本库无数据可验："
            + "、".join(item.key for item in report.unverifiable)
            + "。不能据此宣称完整闭环。退出码 2。"
        )
        return 2
    print("\n结论：PASS —— Dataset → Raw → 数据源 双向可追溯，且 Session 段可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
