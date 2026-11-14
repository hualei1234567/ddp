"""阶段 6 供应商到货验收本地真实 PostgreSQL 链路验证（受控数据）。

覆盖：供应商登记/激活 → 批次登记与状态推进 → 片段登记与审看 →
人工裁决 → 终审统计 → RawAsset 门禁 → 核验账本只追加 → 绩效缺失口径。

说明：
- 数据库为本地 PostgreSQL 16（Alembic 0022），应用走真实路由、仓储、SQL 与触发器。
- 片段引用的 RawAsset 由夹具直接写入，用于验证与阶段 2 链路的关联门禁，
  标注为 `[SYNTHETIC]`，不代表真实供应商交付。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[4]  # 仓库根目录
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DDP_ENV", "local")
os.environ.setdefault(
    "DDP_DATABASE_URL", "postgresql+psycopg://hualei@127.0.0.1:5432/ddp_stage6_e2e"
)
os.environ.setdefault("DDP_JWT_SECRET", "local-dev-secret-EXAMPLE-NOT-A-REAL-KEY")
os.environ.setdefault("DDP_S3_ACCESS_KEY", "test-access-key")
os.environ.setdefault("DDP_S3_SECRET_KEY", "test-secret-key-0123456789abcdef")

from sqlalchemy import select, text  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.domain.identity.models import ProjectRole  # noqa: E402
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent  # noqa: E402
from app.infrastructure.db.models.identity import Project, ProjectMember, User  # noqa: E402
from app.infrastructure.db.models.ingestion import RawAssetModel  # noqa: E402
from app.infrastructure.db.models.suppliers import AcceptanceLedgerModel  # noqa: E402
from app.infrastructure.db.session import create_db_engine, create_session_factory  # noqa: E402
from app.api.v1.supplier_routes import router as supplier_router  # noqa: E402,F401
from tests.api.conftest import make_client  # noqa: E402

PROJECT = UUID("00000000-0000-4000-8000-000000000022")
OWNER = UUID("00000000-0000-4000-8000-000000000021")
EDITOR = UUID("00000000-0000-4000-8000-000000000023")
VIEWER = UUID("00000000-0000-4000-8000-000000000024")

results: list[dict[str, object]] = []


def record(step: str, status: int, detail: object = "") -> None:
    results.append({"step": step, "status": status, "detail": detail})
    print(f"[{step}] HTTP {status} {detail}")


def seed_identity(factory) -> None:
    with factory() as session:
        if session.get(Project, PROJECT) is None:
            session.add(Project(id=PROJECT, code="stage6-e2e", name="阶段 6 验收项目"))
            for user_id, name in [(OWNER, "项目负责人"), (EDITOR, "验收员"), (VIEWER, "只读用户")]:
                if session.get(User, user_id) is None:
                    session.add(User(id=user_id, display_name=name))
            session.flush()
            session.add_all(
                [
                    ProjectMember(project_id=PROJECT, user_id=OWNER, role=ProjectRole.OWNER.value),
                    ProjectMember(project_id=PROJECT, user_id=EDITOR, role=ProjectRole.EDITOR.value),
                    ProjectMember(project_id=PROJECT, user_id=VIEWER, role=ProjectRole.VIEWER.value),
                ]
            )
            session.commit()


def seed_raw_asset(factory, code: str) -> str:
    """[SYNTHETIC] 插入最小 RawAsset：验证片段与阶段 2 链路的关联门禁。"""
    asset_id = uuid4()
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=PROJECT,
                code=code,
                source_type="IMPORT",
                collection_task_id=None,
                batch_id=None,
                upload_session_id=None,
                original_filename=f"{code}.mcap",
                object_key=f"raw/{PROJECT}/{asset_id}/content.mcap",
                size_bytes=8192,
                content_type="application/x-mcap",
                format="MCAP",
                sha256="sha256:" + "b" * 64,
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
                uploaded_by=EDITOR,
                version=1,
                created_at=now,
                updated_at=now,
                created_by=EDITOR,
                updated_by=EDITOR,
            )
        )
        session.commit()
    return str(asset_id)


def main() -> int:
    engine = create_db_engine(
        os.environ["DDP_DATABASE_URL"],
        pool_size=5,
        max_overflow=2,
        pool_timeout_seconds=10,
        connect_timeout_seconds=10,
    )
    factory = create_session_factory(engine)
    seed_identity(factory)

    settings = Settings(env="local")
    editor = make_client(factory, EDITOR, settings_override=settings)
    owner = make_client(factory, OWNER, settings_override=settings)
    viewer = make_client(factory, VIEWER, settings_override=settings)
    base = f"/api/v1/projects/{PROJECT}"

    supplier = editor.post(
        f"{base}/suppliers",
        json={"code": "SUP-STAGE6", "name": "阶段 6 供应商", "category": "DATA_COLLECTION"},
    )
    assert supplier.status_code == 201, supplier.text
    supplier_body = supplier.json()
    record("登记供应商", 201, {"status": supplier_body["status"]})

    activated = owner.post(
        f"{base}/suppliers/{supplier_body['supplier_id']}/actions/activate",
        headers={"If-Match": f'"{supplier_body["version"]}"'},
        json={},
    )
    assert activated.status_code == 200, activated.text
    record("激活供应商", 200, {"status": activated.json()["status"]})

    viewer_write = viewer.post(
        f"{base}/inbound-batches",
        json={"supplier_id": supplier_body["supplier_id"], "batch_code": "B-VIEWER"},
    )
    assert viewer_write.status_code == 403, viewer_write.text
    record("只读用户写入被拒绝", 403, "RBAC 生效")

    planned = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    batch = editor.post(
        f"{base}/inbound-batches",
        json={
            "supplier_id": supplier_body["supplier_id"],
            "batch_code": "20261020_01",
            "title": "到货验收批次",
            "planned_segment_count": 2,
            "planned_arrival_date": planned,
            "storage_prefix": "oss://supplier/20261020_01/",
        },
    )
    assert batch.status_code == 201, batch.text
    batch_body = batch.json()
    record("登记到货批次", 201, {"status": batch_body["status"]})

    for action, label in [("dispatch", "通知发货"), ("arrive", "确认到货"), ("start-screening", "开始验收")]:
        response = editor.post(
            f"{base}/inbound-batches/{batch_body['batch_id']}/actions/{action}",
            headers={"If-Match": f'"{batch_body["version"]}"'},
            json={},
        )
        assert response.status_code == 200, response.text
        batch_body = response.json()
        record(label, 200, {"status": batch_body["status"]})
    assert batch_body["arrived_at"] is not None

    raw_one = seed_raw_asset(factory, "supplier-seg-1")
    raw_two = seed_raw_asset(factory, "supplier-seg-2")

    segment_ids = []
    for code, raw_id in [("seg-0001", raw_one), ("seg-0002", raw_two)]:
        created = editor.post(
            f"{base}/inbound-batches/{batch_body['batch_id']}/segments",
            json={
                "segment_code": code,
                "raw_asset_id": raw_id,
                "duration_ns": 19 * 60 * 1_000_000_000,
                "task_name": "采集青椒",
                "diagnostics": {"vlm_summary": "双人有效操作", "hand_ratio": 0.873},
            },
        )
        assert created.status_code == 201, created.text
        queued = editor.post(
            f"{base}/inbound-batches/{batch_body['batch_id']}/segments/{created.json()['segment_id']}/actions/queue-review",
            headers={"If-Match": f'"{created.json()["version"]}"'},
            json={},
        )
        assert queued.status_code == 200, queued.text
        segment_ids.append(queued.json())
    record("登记并进入审看的片段", 200, {"count": len(segment_ids)})

    self_decide = editor.post(
        f"{base}/inbound-batches/{batch_body['batch_id']}/segments/{segment_ids[0]['segment_id']}/decision",
        headers={"If-Match": f'"{segment_ids[0]["version"]}"'},
        json={"decision": "ACCEPT"},
    )
    assert self_decide.status_code == 403, self_decide.text
    record("登记人自裁决被拒绝", 403, self_decide.json()["error"]["code"])

    first = owner.post(
        f"{base}/inbound-batches/{batch_body['batch_id']}/segments/{segment_ids[0]['segment_id']}/decision",
        headers={"If-Match": f'"{segment_ids[0]["version"]}"'},
        json={"decision": "ACCEPT"},
    )
    assert first.status_code == 200, first.text
    record("负责人裁决通过", 200, {"status": first.json()["status"]})

    missing_reason = owner.post(
        f"{base}/inbound-batches/{batch_body['batch_id']}/segments/{segment_ids[1]['segment_id']}/decision",
        headers={"If-Match": f'"{segment_ids[1]["version"]}"'},
        json={"decision": "REJECT"},
    )
    assert missing_reason.status_code == 422, missing_reason.text
    record("拒收缺原因被拒绝", 422, missing_reason.json()["error"]["code"])

    second = owner.post(
        f"{base}/inbound-batches/{batch_body['batch_id']}/segments/{segment_ids[1]['segment_id']}/decision",
        headers={"If-Match": f'"{segment_ids[1]["version"]}"'},
        json={"decision": "REJECT", "reason": "画面遮挡，主体不可辨识"},
    )
    assert second.status_code == 200, second.text
    record("负责人裁决拒收", 200, {"status": second.json()["status"]})

    editor_finalize = editor.post(
        f"{base}/inbound-batches/{batch_body['batch_id']}/finalize",
        headers={"If-Match": f'"{batch_body["version"]}"'},
        json={},
    )
    assert editor_finalize.status_code == 403, editor_finalize.text
    record("编辑者终审被拒绝", 403, "高风险放行要求负责人")

    finalized = owner.post(
        f"{base}/inbound-batches/{batch_body['batch_id']}/finalize",
        headers={"If-Match": f'"{batch_body["version"]}"'},
        json={},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "PARTIALLY_ACCEPTED"
    record("终审 → PARTIALLY_ACCEPTED", 200, {"settled_at": finalized.json()["settled_at"]})

    ledger = editor.get(f"{base}/acceptance-ledger", params={"batch_id": batch_body["batch_id"]})
    assert ledger.status_code == 200, ledger.text
    actions = [item["action"] for item in ledger.json()["items"]]
    record("核验账本", 200, {"entries": len(actions), "actions": actions})
    assert "register" in actions and "decide:ACCEPT" in actions and "finalize" in actions

    with factory() as session:
        row = session.scalars(select(AcceptanceLedgerModel)).first()
        blocked = False
        try:
            session.execute(
                text("UPDATE acceptance_ledger_entries SET action = 'tampered' WHERE id = :id"),
                {"id": row.id.hex},
            )
            session.commit()
        except Exception:
            session.rollback()
            blocked = True
        assert blocked, "账本被允许更新，PostgreSQL 触发器未生效"
    record("账本禁改触发器", 200, "UPDATE 被 PostgreSQL 拒绝")

    metrics = editor.get(f"{base}/suppliers/{supplier_body['supplier_id']}/metrics")
    assert metrics.status_code == 200, metrics.text
    body = metrics.json()
    record(
        "供应商绩效（缺失不显示 0）",
        200,
        {
            "batch_count": body["batch_count"],
            "first_pass_rate": body["first_pass_rate"],
            "segment_pass_rate": body["segment_pass_rate"],
        },
    )
    assert body["settled_batch_count"] == 1
    assert body["segment_pass_rate"] == 0.5
    assert body["first_pass_rate"] == 0.0

    with factory() as session:
        audit_count = len(
            session.scalars(select(AuditLog).where(AuditLog.project_id == PROJECT)).all()
        )
        outbox_count = len(
            session.scalars(select(OutboxEvent).where(OutboxEvent.project_id == PROJECT)).all()
        )
    record("审计与 Outbox 落库", 200, {"audit_logs": audit_count, "outbox": outbox_count})
    assert audit_count > 0 and outbox_count > 0

    engine.dispose()
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": "PostgreSQL 16.15 local (alembic 0022)",
        "data": "[SYNTHETIC] RawAsset 由夹具写入；片段诊断为受控样例",
        "supplier_id": supplier_body["supplier_id"],
        "batch_id": batch_body["batch_id"],
        "steps": results,
    }
    out = Path(__file__).with_name("summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"\n全部阶段 6 链路步骤通过，证据写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
