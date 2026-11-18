"""存量 ``TRAINED_ON`` 血缘回填的集成验收（真实 SQLite + 真实仓储）。

覆盖：

- 一次扫描只命中"已登记但无边"的引用，已有边的引用不在候选里；
- dry-run 不写任何东西；``apply=True`` 补一条边、一条审计、一条留痕事件；
- 新边沿用引用的原始创建时间，metadata 标注 ``backfilled``；
- 已有边不被触碰（时间语义不变）；
- 再跑一次是幂等的，不重复补边。

跨项目脏引用在数据库层已由复合外键（``project_id + dataset_version_id``）
阻止，用例层的"本项目读不到源版本即跳过"由单元测试覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.api.v1.training_dependencies import get_training_adapter_gateway
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.collection import OutboxEvent as OutboxModel
from app.infrastructure.db.models.episodes import DataLineageEdgeModel
from app.infrastructure.db.models.training import ExternalTrainingReferenceModel
from app.infrastructure.repositories._collection_sql import as_utc
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.workers.lineage_backfill import run_once
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.training_support import (
    FakeTrainingGateway,
    add_member,
    approved_request,
    build_deliverable,
    delivery_payload,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _delivered(factory: sessionmaker[Session], ids: ApiIds):
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


def _create_reference(client, project_id: UUID, delivery_id: UUID) -> UUID:
    response = client.post(
        url(project_id, f"/training-deliveries/{delivery_id}/external-references"),
        json={
            "adapter_id": "mock-training",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "execution_mode": "MOCK",
        },
        headers={"Idempotency-Key": f"ref-{uuid4().hex[:8]}"},
    )
    assert response.status_code == 202, response.text
    return UUID(response.json()["external_ref_id"])


def _insert_legacy_reference(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    delivery: dict[str, object],
    version_id: UUID,
    created_by: UUID,
) -> UUID:
    """模拟 N5-005 之前的存量行：引用已登记，但 TRAINED_ON 边不存在。

    血缘边有 append-only 触发器，不能"先建边再删"来造历史；这里直接落引用行，
    复合外键需要的交付与需求修订都来自真实 API 创建的那条交付。
    """
    reference_id = uuid4()
    with factory() as session:
        session.add(
            ExternalTrainingReferenceModel(
                id=reference_id,
                project_id=project_id,
                adapter_id="legacy-adapter",
                adapter_version="0.9.0",
                training_delivery_id=UUID(str(delivery["training_delivery_id"])),
                training_request_revision_id=UUID(str(delivery["training_request_revision_id"])),
                dataset_version_id=version_id,
                external_run_id=f"legacy-run-{reference_id.hex[:8]}",
                external_run_url=None,
                # 外部引用初始状态只允许 SUBMITTED（训练守卫触发器，迁移 0019/0020）。
                external_status="SUBMITTED",
                execution_mode="REMOTE",
                status_stale=False,
                submitted_at=NOW,
                last_synced_at=NOW,
                model_ref=None,
                evaluation_ref=None,
                artifacts=None,
                correlation_id=f"corr-{reference_id.hex[:8]}",
                idempotency_key=f"legacy-{reference_id.hex[:8]}",
                error_code=None,
                error_summary=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=created_by,
                updated_by=created_by,
            )
        )
        session.commit()
    return reference_id


def _reference_time(factory: sessionmaker[Session], reference_id: UUID) -> datetime:
    with factory() as session:
        row = session.get(ExternalTrainingReferenceModel, reference_id)
    assert row is not None
    created_at = as_utc(row.created_at)
    assert created_at is not None
    return created_at


def _trained_on_edges_for(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    version_id: UUID,
    actor_id: UUID,
):
    with factory() as session:
        edges = SqlLineageRepository(session).list_edges_from(
            ProjectScope(project_id=project_id, actor_id=actor_id),
            node_type=LineageNodeType.DATASET_VERSION,
            node_id=version_id,
            limit=50,
        )
    return [edge for edge in edges if edge.relation is LineageRelation.TRAINED_ON]


def _count_trained_on(factory: sessionmaker[Session], project_id: UUID) -> int:
    with factory() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(DataLineageEdgeModel)
                .where(
                    DataLineageEdgeModel.project_id == project_id,
                    DataLineageEdgeModel.relation == LineageRelation.TRAINED_ON.value,
                )
            )
            or 0
        )


def test_backfill_run_counts_and_only_fills_missing_edge(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor, ctx, delivery = _delivered(factory, ids)
    delivery_id = UUID(str(delivery["training_delivery_id"]))
    version_id = UUID(str(ctx["version_id"]))

    # 1) 已有边的引用：N5-005 之后的正常行。
    existing_ref = _create_reference(editor, ids.project_a, delivery_id)
    # 2) 缺边的引用：模拟 N5-005 之前的存量行。
    legacy_ref = _insert_legacy_reference(
        factory,
        project_id=ids.project_a,
        delivery=delivery,
        version_id=version_id,
        created_by=ids.owner_a,
    )

    before = _trained_on_edges_for(
        factory,
        project_id=ids.project_a,
        version_id=version_id,
        actor_id=ids.owner_a,
    )
    assert [edge.to_node_id for edge in before] == [existing_ref]
    existing_created_at = before[0].created_at
    legacy_created_at = _reference_time(factory, legacy_ref)

    dry_run = run_once(
        factory,
        apply=False,
        limit=10,
        max_batches=10,
        timeout_seconds=None,
        now=NOW,
    )
    assert dry_run.planned == 1
    assert dry_run.created == 0
    assert dry_run.failed == 0
    assert dry_run.entries[0].external_ref_id == legacy_ref
    assert _count_trained_on(factory, ids.project_a) == 1

    applied = run_once(
        factory,
        apply=True,
        limit=10,
        max_batches=10,
        timeout_seconds=None,
        now=NOW,
    )
    assert applied.created == 1
    assert applied.failed == 0
    assert applied.timed_out is False

    edges = _trained_on_edges_for(
        factory,
        project_id=ids.project_a,
        version_id=version_id,
        actor_id=ids.owner_a,
    )
    by_target = {edge.to_node_id: edge for edge in edges}
    assert set(by_target) == {existing_ref, legacy_ref}
    backfilled = by_target[legacy_ref]
    assert backfilled.from_node_type is LineageNodeType.DATASET_VERSION
    assert backfilled.to_node_type is LineageNodeType.TRAINING_RUN
    assert backfilled.created_by == ids.owner_a
    assert backfilled.created_at == legacy_created_at, "新边必须沿用引用的原始创建时间"
    assert backfilled.metadata["backfilled"] is True
    assert backfilled.metadata["adapter_id"] == "legacy-adapter"
    assert str(backfilled.metadata["external_run_id"]).startswith("legacy-run-")
    # 已有边不被触碰：时间语义与写入者保持原值。
    assert by_target[existing_ref].created_at == existing_created_at

    with factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_id == legacy_ref,
                AuditLog.action == "backfill-lineage",
            )
        )
        event = session.scalar(
            select(OutboxModel).where(
                OutboxModel.project_id == ids.project_a,
                OutboxModel.aggregate_id == legacy_ref,
                OutboxModel.event_type == "training.external_reference.lineage_backfilled",
            )
        )
    assert audit is not None
    assert audit.details["backfilled"] is True
    assert audit.details["edge_created_at"] == legacy_created_at.isoformat()
    assert event is not None
    assert event.status == "RECORDED"
    assert event.dispatch is False

    replay = run_once(
        factory,
        apply=True,
        limit=10,
        max_batches=10,
        timeout_seconds=None,
        now=NOW,
    )
    assert replay.created == 0
    assert replay.failed == 0
    assert _count_trained_on(factory, ids.project_a) == 2
