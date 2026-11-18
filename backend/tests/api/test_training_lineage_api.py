"""DatasetVersion → TrainingRun 血缘（ADR-0036）的真实 HTTP + SQLite 验收。

覆盖：

- 外部训练引用创建成功后，同事务写入 ``TRAINED_ON`` 边与两端节点；
- 版本正向血缘查询能返回该边（只读链路不被新关系卡住）；
- 同一幂等键重放不重复建边；
- 写边失败时引用创建整体回滚，不出现"有引用、无血缘"；
- 跨项目访问在读取交付时即 404，不会在别的项目写边。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.api.v1.training_dependencies import (
    get_training_adapter_gateway,
    get_training_services,
)
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.training_commands import NewExternalTrainingReference
from app.core.config import Settings
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import DataLineageEdgeModel, LineageNodeRefModel
from app.infrastructure.db.models.training import ExternalTrainingReferenceModel
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.export_support import FakeExportStorage
from tests.api.training_support import (
    FakeTrainingGateway,
    add_member,
    approved_request,
    build_deliverable,
    delivery_payload,
)


def _delivered(
    factory: sessionmaker[Session],
    ids: ApiIds,
) -> tuple[TestClient, dict[str, object], dict[str, object]]:
    """造出 DELIVERED 状态的交付，并替换 Adapter 网关。"""
    approver_id = add_member(factory, ids.project_a, role="owner")
    editor = make_client(factory, ids.editor_a)
    approver = make_client(factory, approver_id)
    ctx = build_deliverable(factory, ids)
    request_body = approved_request(editor, approver, ids.project_a, ids.owner_a)
    editor = ctx["editor"]
    editor.app.dependency_overrides[get_training_adapter_gateway] = lambda: FakeTrainingGateway(
        "MOCK"
    )
    created = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
        headers={"Idempotency-Key": f"delivery-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    delivery = created.json()
    ready = editor.post(
        url(
            ids.project_a,
            f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready",
        ),
        headers={"If-Match": f'"{delivery["version"]}"'},
        json={},
    )
    assert ready.status_code == 200, ready.text
    delivered = editor.post(
        url(
            ids.project_a,
            f"/training-deliveries/{delivery['training_delivery_id']}/actions/deliver",
        ),
        headers={"If-Match": f'"{ready.json()["version"]}"'},
        json={},
    )
    assert delivered.status_code == 200, delivered.text
    return editor, ctx, delivered.json()


def _create_reference(
    client: TestClient,
    project_id: UUID,
    delivery_id: UUID,
    *,
    key: str,
    correlation_id: str | None = None,
) -> object:
    return client.post(
        url(project_id, f"/training-deliveries/{delivery_id}/external-references"),
        json={
            "adapter_id": "mock-training",
            "adapter_version": "1.0.0",
            "correlation_id": correlation_id or f"corr-{uuid4().hex[:8]}",
            "execution_mode": "MOCK",
        },
        headers={"Idempotency-Key": key},
    )


def _trained_on_edges(
    factory: sessionmaker[Session],
    project_id: UUID,
) -> list[DataLineageEdgeModel]:
    with factory() as session:
        return list(
            session.scalars(
                select(DataLineageEdgeModel).where(
                    DataLineageEdgeModel.project_id == project_id,
                    DataLineageEdgeModel.relation == "TRAINED_ON",
                )
            )
        )


def test_create_reference_writes_trained_on_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor, ctx, delivery = _delivered(factory, ids)
    response = _create_reference(
        editor,
        ids.project_a,
        UUID(str(delivery["training_delivery_id"])),
        key=f"trained-{uuid4().hex[:8]}",
    )
    assert response.status_code == 202, response.text
    reference = response.json()
    reference_id = UUID(reference["external_ref_id"])
    version_id = UUID(str(ctx["version_id"]))

    edges = _trained_on_edges(factory, ids.project_a)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.from_node_type == "DATASET_VERSION"
    assert edge.from_node_id == version_id
    assert edge.to_node_type == "TRAINING_RUN"
    assert edge.to_node_id == reference_id
    assert edge.created_by == ids.editor_a
    # 外部平台的 run 是跨系统对账键，必须落进 metadata。
    assert edge.edge_metadata["external_run_id"] == reference["external_run_id"]
    assert edge.edge_metadata["training_delivery_id"] == delivery["training_delivery_id"]
    assert edge.edge_metadata["adapter_id"] == "mock-training"

    with factory() as session:
        node_types = set(
            session.scalars(
                select(LineageNodeRefModel.node_type).where(
                    LineageNodeRefModel.project_id == ids.project_a,
                    LineageNodeRefModel.node_id.in_([version_id, reference_id]),
                )
            )
        )
    assert node_types == {"DATASET_VERSION", "TRAINING_RUN"}

    dataset_id = ctx["dataset"]["dataset_id"]
    forward = editor.get(
        f"/api/v1/projects/{ids.project_a}/datasets/{dataset_id}"
        f"/versions/{version_id}/lineage?direction=forward&depth=1"
    )
    assert forward.status_code == 200, forward.text
    items = forward.json()["items"]
    assert any(
        item["relation"] == "TRAINED_ON"
        and item["from_node_id"] == str(version_id)
        and item["to_node_id"] == str(reference_id)
        for item in items
    )


def test_idempotent_replay_does_not_duplicate_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor, _ctx, delivery = _delivered(factory, ids)
    key = f"trained-{uuid4().hex[:8]}"
    correlation_id = f"corr-{uuid4().hex[:8]}"
    delivery_id = UUID(str(delivery["training_delivery_id"]))

    first = _create_reference(
        editor, ids.project_a, delivery_id, key=key, correlation_id=correlation_id
    )
    replay = _create_reference(
        editor, ids.project_a, delivery_id, key=key, correlation_id=correlation_id
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert replay.json()["external_ref_id"] == first.json()["external_ref_id"]
    assert len(_trained_on_edges(factory, ids.project_a)) == 1


def test_edge_write_failure_rolls_back_reference(
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, ids = api_db
    _editor, _ctx, delivery = _delivered(factory, ids)

    def _fail(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("模拟血缘写入失败")

    monkeypatch.setattr(DatasetVersionLineageRecorder, "record_training_run", _fail)
    # 直接调用用例，避免让"预期内的 500"穿过 TestClient 的 portal；事务语义与
    # HTTP 路径相同：写边失败 → 不 commit → 会话回滚。
    with factory() as session:
        services = get_training_services(
            session,
            Settings(env="local"),
            FakeExportStorage(),
            FakeTrainingGateway("MOCK"),
        )
        with pytest.raises(RuntimeError):
            services.references.create(
                ProjectScope(ids.project_a, actor_id=ids.editor_a),
                UUID(str(delivery["training_delivery_id"])),
                NewExternalTrainingReference(
                    adapter_id="mock-training",
                    adapter_version="1.0.0",
                    correlation_id=f"corr-{uuid4().hex[:8]}",
                    execution_mode="MOCK",
                ),
                idempotency_key=f"trained-{uuid4().hex[:8]}",
                method="POST",
                path="/training-deliveries/x/external-references",
            )
        session.rollback()

    with factory() as session:
        reference_count = session.scalar(
            select(func.count()).select_from(ExternalTrainingReferenceModel)
        )
        trained_on_count = session.scalar(
            select(func.count())
            .select_from(DataLineageEdgeModel)
            .where(DataLineageEdgeModel.relation == "TRAINED_ON")
        )
    # 引用与血缘必须一起成败：写边失败后引用不得残留。
    assert reference_count == 0
    assert trained_on_count == 0


def test_cross_project_delivery_cannot_write_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _editor, _ctx, delivery = _delivered(factory, ids)
    # owner_a 同时是项目 A 与 B 的 owner，但项目 B 的路径不得操作 A 的交付。
    owner = make_client(factory, ids.owner_a)
    owner.app.dependency_overrides[get_training_adapter_gateway] = lambda: FakeTrainingGateway(
        "MOCK"
    )
    cross = _create_reference(
        owner,
        ids.project_b,
        UUID(str(delivery["training_delivery_id"])),
        key=f"trained-{uuid4().hex[:8]}",
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    assert _trained_on_edges(factory, ids.project_a) == []
    assert _trained_on_edges(factory, ids.project_b) == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExternalTrainingReferenceModel)) == 0
