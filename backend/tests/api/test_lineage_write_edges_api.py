"""血缘写边补齐的 API 集成测试：标注通过、质量运行两条真实链路。

全部走真实 SQLite 仓储与真实 HTTP 路由，不 mock 用例层：

- 标注提交被复核通过后，``GET /episodes/{id}/lineage`` 必须能查到
  ``Episode --ANNOTATED_BY--> ANNOTATION``，且元数据指回提交；
- 提交触发的质量检查必须留下 ``Episode --EVALUATED_BY--> QUALITY_RUN``，
  节点 ID 就是真实的质量检查记录 ID；
- 边归属于正确项目：跨项目查询 404/403，跨项目直接写边被复合外键拒绝；
- 重复通过不新增边。

``DatasetVersion → TRAINING_RUN`` 不在本文件：当前冻结契约只有
``TRAINING_RUN`` 节点类型，没有自 ``DATASET_VERSION`` 出发的关系值，
新增关系需要迁移与契约评审（见交付说明）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import LineageConflictError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_quality_rules import AnnotationQualityRun
from app.infrastructure.db.models.episodes import DataLineageEdgeModel, LineageNodeRefModel
from app.infrastructure.repositories.lineage import SqlLineageRepository
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def _annotation_flow(
    factory: sessionmaker[Session],
    ids: ApiIds,
    code: str,
) -> dict[str, object]:
    """建 Episode → Schema → 样本集 → 任务 → 提交（提交会触发质量检查）。"""
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        f"task-{code}",
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    return {
        "episode_id": episode_id,
        "editor": editor,
        "owner": owner,
        "submission": submission,
    }


def _episode_lineage(
    client: TestClient,
    project_id: UUID,
    episode_id: UUID,
    *,
    direction: str = "forward",
) -> list[dict[str, object]]:
    response = client.get(
        f"/api/v1/episodes/{episode_id}/lineage"
        f"?project_id={project_id}&direction={direction}&depth=1&limit=100"
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _edges(
    factory: sessionmaker[Session],
    project_id: UUID,
    *,
    relation: str,
) -> list[DataLineageEdgeModel]:
    with factory() as session:
        return list(
            session.scalars(
                select(DataLineageEdgeModel).where(
                    DataLineageEdgeModel.project_id == project_id,
                    DataLineageEdgeModel.relation == relation,
                )
            )
        )


def test_submit_writes_evaluated_by_quality_run_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    flow = _annotation_flow(factory, ids, "lineage-quality")
    episode_id = flow["episode_id"]
    submission = flow["submission"]
    submission_id = UUID(submission["submission_id"])

    # 质量检查在提交时自动执行；节点 ID 必须是真实的质量检查记录。
    with factory() as session:
        run_id = session.scalar(
            select(AnnotationQualityRun.id).where(
                AnnotationQualityRun.project_id == ids.project_a,
                AnnotationQualityRun.submission_id == submission_id,
            )
        )
    assert run_id is not None

    items = _episode_lineage(flow["editor"], ids.project_a, episode_id)
    quality_edges = [item for item in items if item["relation"] == "EVALUATED_BY"]
    assert len(quality_edges) == 1
    edge = quality_edges[0]
    assert edge["project_id"] == str(ids.project_a)
    assert edge["from_node_type"] == "EPISODE"
    assert edge["from_node_id"] == str(episode_id)
    assert edge["to_node_type"] == "QUALITY_RUN"
    assert edge["to_node_id"] == str(run_id)
    metadata = edge["metadata"]
    assert metadata["annotation_submission_id"] == str(submission_id)
    assert metadata["quality_run_id"] == str(run_id)
    assert metadata["quality_status"] in {"PASSED", "FAILED"}
    assert str(metadata["rule_set_version"]).startswith("sha256:")

    # 节点投影必须真实存在，不能只有边。
    with factory() as session:
        node_types = set(
            session.scalars(
                select(LineageNodeRefModel.node_type).where(
                    LineageNodeRefModel.project_id == ids.project_a,
                    LineageNodeRefModel.node_id == episode_id,
                )
            )
        )
    assert node_types == {"EPISODE"}


def test_review_approval_writes_annotated_by_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    flow = _annotation_flow(factory, ids, "lineage-annotation")
    submission = flow["submission"]
    submission_id = submission["submission_id"]
    records = submission["records"]
    episode_id = flow["episode_id"]
    owner = flow["owner"]

    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    claimed = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    approved = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/approve"),
        headers={"If-Match": '"2"'},
        json={"checklist": [{"key": "labels_complete", "passed": True}]},
    )
    assert approved.status_code == 200, approved.text

    items = _episode_lineage(flow["editor"], ids.project_a, episode_id)
    annotation_edges = [item for item in items if item["relation"] == "ANNOTATED_BY"]
    assert len(annotation_edges) == len(records)
    assert {edge["to_node_id"] for edge in annotation_edges} == {
        record["annotation_id"] for record in records
    }
    for edge in annotation_edges:
        assert edge["project_id"] == str(ids.project_a)
        assert edge["from_node_type"] == "EPISODE"
        assert edge["from_node_id"] == str(episode_id)
        assert edge["to_node_type"] == "ANNOTATION"
        assert edge["metadata"]["annotation_submission_id"] == submission_id
        assert edge["metadata"]["submission_version"] == submission["submission_version"]
        assert edge["metadata"]["annotation_version"] == 1

    # 复核已通过：再次通过必须被状态机拒绝，且不得新增边。
    again = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/approve"),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert again.status_code in {409, 422}, again.text
    assert len(_edges(factory, ids.project_a, relation="ANNOTATED_BY")) == len(records)


def test_written_edges_are_project_scoped(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    flow = _annotation_flow(factory, ids, "lineage-scope")
    episode_id = flow["episode_id"]

    # owner_a 是项目 B 的成员：用项目 B 的上下文查项目 A 的 Episode → 404。
    owner = flow["owner"]
    cross = owner.get(f"/api/v1/episodes/{episode_id}/lineage?project_id={ids.project_b}")
    assert cross.status_code == 404

    # 完全不是项目 A 成员 → 403（连项目上下文都不成立）。
    outsider = make_client(factory, ids.outsider)
    forbidden = outsider.get(f"/api/v1/episodes/{episode_id}/lineage?project_id={ids.project_a}")
    assert forbidden.status_code == 403

    # 边必须落在项目 A，不能因为查询参数而漂移。
    with factory() as session:
        project_ids = set(
            session.scalars(
                select(DataLineageEdgeModel.project_id).where(
                    DataLineageEdgeModel.relation == "EVALUATED_BY"
                )
            )
        )
    assert project_ids == {ids.project_a}


def test_cross_project_lineage_write_is_rejected_by_database(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """跨项目写边必须被复合外键拒绝，而不是悄悄写到别的项目下。"""
    factory, ids = api_db
    flow = _annotation_flow(factory, ids, "lineage-cross-write")
    episode_id = flow["episode_id"]

    with factory() as session:
        repo = SqlLineageRepository(session)
        scope_b = ProjectScope(ids.project_b, actor_id=ids.owner_a)
        with pytest.raises(LineageConflictError):
            repo.ensure_edge(
                scope_b,
                from_node_type=LineageNodeType.EPISODE,
                from_node_id=episode_id,
                to_node_type=LineageNodeType.ANNOTATION,
                to_node_id=UUID(flow["submission"]["records"][0]["annotation_id"]),
                relation=LineageRelation.ANNOTATED_BY,
                metadata={},
                now=datetime.now(UTC),
            )
        session.rollback()

    with factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(DataLineageEdgeModel)
            .where(DataLineageEdgeModel.project_id == ids.project_b)
        )
    assert count == 0
