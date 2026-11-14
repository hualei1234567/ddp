"""供应商与到货验收 HTTP API 测试。

覆盖：项目隔离、状态机、批次终审统计、职责分离、账本只追加、
绩效缺失不显示 0 与 RawAsset 门禁。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.suppliers import AcceptanceLedgerModel
from sqlalchemy import select, text

from tests.api.conftest import make_client
from tests.api.dataset_support import url

pytestmark = pytest.mark.usefixtures("api_db")


def _seed_raw_asset(factory, project_id, actor_id) -> str:
    """插入一条最小 RawAsset，用于验证片段与既有数据接入链路的关联。"""
    from app.infrastructure.db.models.ingestion import RawAssetModel

    asset_id = uuid4()
    now = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=project_id,
                code=f"raw-{asset_id.hex[:8]}",
                source_type="IMPORT",
                collection_task_id=None,
                batch_id=None,
                upload_session_id=None,
                original_filename="supplier-segment.mcap",
                object_key=f"raw/{project_id}/{asset_id}/content.mcap",
                size_bytes=4096,
                content_type="application/x-mcap",
                format="MCAP",
                sha256="sha256:" + "a" * 64,
                # 守卫要求 RawAsset 初始状态为 VALIDATING，再由校验动作转 VALID。
                status="VALIDATING",
                captured_at=now,
                ingested_at=now,
                storage_class="STANDARD",
                retention_policy_id=None,
                quarantine_reason=None,
                quarantine_codes=[],
                scan_report_key=None,
                verified_at=now,
                archived_at=None,
                tombstoned_at=None,
                duplicate_of_asset_id=None,
                uploaded_by=actor_id,
                version=1,
                created_at=now,
                updated_at=now,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.commit()
    return str(asset_id)


def _create_supplier(client, project_id, *, code: str | None = None, activate: bool = True):
    created = client.post(
        url(project_id, "/suppliers"),
        json={
            "code": code or f"S-{uuid4().hex[:6]}",
            "name": "示例供应商",
            "category": "DATA_COLLECTION",
        },
    )
    assert created.status_code == 201, created.text
    supplier = created.json()
    if activate:
        activated = client.post(
            url(project_id, f"/suppliers/{supplier['supplier_id']}/actions/activate"),
            headers={"If-Match": f'"{supplier["version"]}"'},
            json={},
        )
        assert activated.status_code == 200, activated.text
        supplier = activated.json()
    return supplier


def _create_batch(client, project_id, supplier_id, *, code: str | None = None):
    created = client.post(
        url(project_id, "/inbound-batches"),
        json={
            "supplier_id": supplier_id,
            "batch_code": code or f"B-{uuid4().hex[:6]}",
            "planned_segment_count": 2,
            "planned_arrival_date": "2026-11-10T00:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _advance(client, project_id, batch, action: str):
    response = client.post(
        url(project_id, f"/inbound-batches/{batch['batch_id']}/actions/{action}"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _add_segment(client, project_id, batch_id, code="seg-1", raw_asset_id=None):
    response = client.post(
        url(project_id, f"/inbound-batches/{batch_id}/segments"),
        json={
            "segment_code": code,
            "raw_asset_id": str(raw_asset_id) if raw_asset_id else None,
            "duration_ns": 19 * 60 * 1_000_000_000,
            "task_name": "采集青椒",
            "diagnostics": {"vlm_summary": "双人有效操作", "hand_ratio": 0.873},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _queue(client, project_id, batch_id, segment):
    response = client.post(
        url(
            project_id,
            f"/inbound-batches/{batch_id}/segments/{segment['segment_id']}/actions/queue-review",
        ),
        headers={"If-Match": f'"{segment["version"]}"'},
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_supplier_creation_and_project_isolation(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    outsider = make_client(factory, ids.outsider)

    supplier = _create_supplier(editor, ids.project_a)
    assert supplier["status"] == "ACTIVE"
    assert supplier["project_id"] == str(ids.project_a)
    assert "suspend" in supplier["allowed_actions"]

    forbidden = outsider.get(url(ids.project_a, f"/suppliers/{supplier['supplier_id']}"))
    assert forbidden.status_code in {403, 404}

    duplicate = editor.post(
        url(ids.project_a, "/suppliers"),
        json={"code": supplier["code"], "name": "重复编码"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"


def test_supplier_state_machine_terminal(api_db) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    supplier = _create_supplier(owner, ids.project_a)

    terminated = owner.post(
        url(ids.project_a, f"/suppliers/{supplier['supplier_id']}/actions/terminate"),
        headers={"If-Match": f'"{supplier["version"]}"'},
        json={"reason": "合作到期"},
    )
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["status"] == "TERMINATED"

    again = owner.post(
        url(ids.project_a, f"/suppliers/{supplier['supplier_id']}/actions/activate"),
        headers={"If-Match": f'"{terminated.json()["version"]}"'},
        json={},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "state_transition_invalid"


def test_batch_requires_active_supplier(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    draft = _create_supplier(editor, ids.project_a, activate=False)
    response = editor.post(
        url(ids.project_a, "/inbound-batches"),
        json={"supplier_id": draft["supplier_id"], "batch_code": "B-1"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "supplier_not_active"


def test_full_acceptance_chain_with_segregation_of_duties(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    supplier = _create_supplier(editor, ids.project_a)
    batch = _create_batch(editor, ids.project_a, supplier["supplier_id"])
    assert batch["status"] == "ANNOUNCED"
    batch = _advance(editor, ids.project_a, batch, "dispatch")
    batch = _advance(editor, ids.project_a, batch, "arrive")
    assert batch["arrived_at"] is not None
    batch = _advance(editor, ids.project_a, batch, "start-screening")

    first = _add_segment(editor, ids.project_a, batch["batch_id"], "seg-1")
    second = _add_segment(editor, ids.project_a, batch["batch_id"], "seg-2")
    first = _queue(editor, ids.project_a, batch["batch_id"], first)
    second = _queue(editor, ids.project_a, batch["batch_id"], second)

    # 终审前有未裁决片段 → 拒绝
    early = owner.post(
        url(ids.project_a, f"/inbound-batches/{batch['batch_id']}/finalize"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={},
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "batch_review_incomplete"

    # 登记人不得裁决自己登记的片段
    self_decide = editor.post(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{first['segment_id']}/decision",
        ),
        headers={"If-Match": f'"{first["version"]}"'},
        json={"decision": "ACCEPT"},
    )
    assert self_decide.status_code == 403
    assert self_decide.json()["error"]["code"] == "segment_decision_forbidden"

    owner_decision = owner.post(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{first['segment_id']}/decision",
        ),
        headers={"If-Match": f'"{first["version"]}"'},
        json={"decision": "ACCEPT"},
    )
    assert owner_decision.status_code == 200, owner_decision.text
    assert owner_decision.json()["status"] == "ACCEPTED"

    missing_reason = owner.post(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{second['segment_id']}/decision",
        ),
        headers={"If-Match": f'"{second["version"]}"'},
        json={"decision": "REJECT"},
    )
    assert missing_reason.status_code == 422
    assert missing_reason.json()["error"]["code"] == "decision_reason_required"

    decided = owner.post(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{second['segment_id']}/decision",
        ),
        headers={"If-Match": f'"{second["version"]}"'},
        json={"decision": "REJECT", "reason": "画面遮挡，主体不可辨识"},
    )
    assert decided.status_code == 200, decided.text

    # 已通过片段缺 RawAsset → 终审拒绝
    blocked = owner.post(
        url(ids.project_a, f"/inbound-batches/{batch['batch_id']}/finalize"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "segment_raw_asset_missing"

    ledger = editor.get(
        url(ids.project_a, "/acceptance-ledger"),
        params={"batch_id": batch["batch_id"]},
    )
    assert ledger.status_code == 200, ledger.text
    actions = [item["action"] for item in ledger.json()["items"]]
    assert "register" in actions
    assert "decide:ACCEPT" in actions


def test_finalize_requires_owner_and_computes_status(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    supplier = _create_supplier(editor, ids.project_a)
    batch = _create_batch(editor, ids.project_a, supplier["supplier_id"], code="B-all")
    batch = _advance(editor, ids.project_a, batch, "dispatch")
    batch = _advance(editor, ids.project_a, batch, "arrive")
    batch = _advance(editor, ids.project_a, batch, "start-screening")
    raw_asset_id = _seed_raw_asset(factory, ids.project_a, ids.editor_a)
    segment = _add_segment(
        editor, ids.project_a, batch["batch_id"], "seg-only", raw_asset_id=raw_asset_id
    )
    segment = _queue(editor, ids.project_a, batch["batch_id"], segment)
    decided = owner.post(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{segment['segment_id']}/decision",
        ),
        headers={"If-Match": f'"{segment["version"]}"'},
        json={"decision": "ACCEPT"},
    )
    assert decided.status_code == 200, decided.text

    # 编辑者不能执行终审（高风险放行要求 Owner）
    forbidden = editor.post(
        url(ids.project_a, f"/inbound-batches/{batch['batch_id']}/finalize"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={},
    )
    assert forbidden.status_code == 403

    finalized = owner.post(
        url(ids.project_a, f"/inbound-batches/{batch['batch_id']}/finalize"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "ACCEPTED"
    assert finalized.json()["settled_at"] is not None


def test_metrics_return_null_when_no_settled_batch(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    supplier = _create_supplier(editor, ids.project_a)
    _create_batch(editor, ids.project_a, supplier["supplier_id"])

    metrics = editor.get(url(ids.project_a, f"/suppliers/{supplier['supplier_id']}/metrics"))
    assert metrics.status_code == 200, metrics.text
    body = metrics.json()
    assert body["batch_count"] == 1
    assert body["settled_batch_count"] == 0
    # 缺失必须为 null，不能是 0
    assert body["on_time_rate"] is None
    assert body["first_pass_rate"] is None
    assert body["segment_pass_rate"] is None


def test_ledger_is_append_only(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    supplier = _create_supplier(editor, ids.project_a)

    with factory() as session:
        row = session.scalars(
            select(AcceptanceLedgerModel).where(
                AcceptanceLedgerModel.supplier_id == UUID(supplier["supplier_id"])
            )
        ).first()
        assert row is not None
        with pytest.raises(Exception) as excinfo:
            session.execute(
                text("UPDATE acceptance_ledger_entries SET action = 'tampered' WHERE id = :id"),
                {"id": row.id.hex},
            )
            session.commit()
        session.rollback()
        assert (
            "append-only" in str(excinfo.value).lower()
            or "constraint" in str(excinfo.value).lower()
        )


def test_segment_diagnostics_are_preserved(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    supplier = _create_supplier(editor, ids.project_a)
    batch = _create_batch(editor, ids.project_a, supplier["supplier_id"], code="B-diag")
    segment = _add_segment(editor, ids.project_a, batch["batch_id"], "seg-diag")

    fetched = editor.get(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{segment['segment_id']}",
        )
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["diagnostics"]["hand_ratio"] == 0.873


def test_batch_rework_increments_counter(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    supplier = _create_supplier(editor, ids.project_a)
    batch = _create_batch(editor, ids.project_a, supplier["supplier_id"], code="B-rw")
    batch = _advance(editor, ids.project_a, batch, "dispatch")
    batch = _advance(editor, ids.project_a, batch, "arrive")
    batch = _advance(editor, ids.project_a, batch, "start-screening")
    segment = _add_segment(editor, ids.project_a, batch["batch_id"], "seg-rw")
    segment = _queue(editor, ids.project_a, batch["batch_id"], segment)
    rejected = owner.post(
        url(
            ids.project_a,
            f"/inbound-batches/{batch['batch_id']}/segments/{segment['segment_id']}/decision",
        ),
        headers={"If-Match": f'"{segment["version"]}"'},
        json={"decision": "REJECT", "reason": "全部不合格"},
    )
    assert rejected.status_code == 200, rejected.text

    # 全部拒收 → REJECTED，可返工
    finalized = owner.post(
        url(ids.project_a, f"/inbound-batches/{batch['batch_id']}/finalize"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "REJECTED"

    rework = owner.post(
        url(ids.project_a, f"/inbound-batches/{batch['batch_id']}/actions/rework"),
        headers={"If-Match": f'"{finalized.json()["version"]}"'},
        json={"reason": "供应商补件"},
    )
    assert rework.status_code == 200, rework.text
    assert rework.json()["status"] == "REWORK"
    assert rework.json()["rework_count"] == 1


def test_metrics_computation_with_settled_batches(api_db) -> None:
    """两个准时且一次通过的批次 → 及时率 1.0、一次通过率 1.0。"""
    from app.domain.suppliers.metrics import BatchSample, summarize

    settled = [
        BatchSample(
            settled=True,
            status="ACCEPTED",
            arrived_at=datetime(2026, 11, 9, 8, tzinfo=UTC),
            settled_at=datetime(2026, 11, 9, 18, tzinfo=UTC),
            planned_arrival_date=datetime(2026, 11, 10, tzinfo=UTC),
            rework_count=0,
        ),
        BatchSample(
            settled=True,
            status="ACCEPTED",
            arrived_at=datetime(2026, 11, 10, 8, tzinfo=UTC),
            settled_at=datetime(2026, 11, 10, 14, tzinfo=UTC),
            planned_arrival_date=datetime(2026, 11, 11, tzinfo=UTC),
            rework_count=0,
        ),
    ]
    metrics = summarize(settled, accepted_segments=10, decided_segments=10)
    assert metrics.on_time_rate == 1.0
    assert metrics.first_pass_rate == 1.0
    assert metrics.rework_rate == 0.0
    assert metrics.average_cycle_hours == 8.0
    assert metrics.segment_pass_rate == 1.0


def test_late_batch_lowers_on_time_rate() -> None:
    from app.domain.suppliers.metrics import BatchSample, summarize

    samples = [
        BatchSample(
            settled=True,
            status="ACCEPTED",
            arrived_at=datetime(2026, 11, 9, tzinfo=UTC),
            settled_at=datetime(2026, 11, 9, 4, tzinfo=UTC),
            planned_arrival_date=datetime(2026, 11, 10, tzinfo=UTC),
            rework_count=0,
        ),
        BatchSample(
            settled=True,
            status="PARTIALLY_ACCEPTED",
            arrived_at=datetime(2026, 11, 12, tzinfo=UTC),
            settled_at=datetime(2026, 11, 12, 2, tzinfo=UTC),
            planned_arrival_date=datetime(2026, 11, 10, tzinfo=UTC),
            rework_count=1,
        ),
    ]
    metrics = summarize(samples, accepted_segments=3, decided_segments=4)
    assert metrics.on_time_rate == 0.5
    assert metrics.first_pass_rate == 0.5
    assert metrics.rework_rate == 0.5
    assert metrics.segment_pass_rate == 0.75


def test_cross_project_supplier_reference_rejected(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner_b = make_client(factory, ids.owner_a)

    # 在项目 B 创建供应商（owner 同时是 B 的 owner）
    supplier_b = _create_supplier(owner_b, ids.project_b, code="S-B")
    # 项目 A 引用项目 B 的供应商
    response = editor.post(
        url(ids.project_a, "/inbound-batches"),
        json={"supplier_id": supplier_b["supplier_id"], "batch_code": "B-cross"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"
