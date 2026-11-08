"""训练需求/交付 API 测试夹具。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.identity.models import ProjectRole
from app.infrastructure.db.models.identity import ProjectMember, User
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import make_client
from tests.api.dataset_support import url
from tests.api.export_support import (
    FakeExportStorage,
    build_frozen_version,
    export_payload,
    run_export_worker,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def add_member(
    factory: sessionmaker[Session],
    project_id: UUID,
    *,
    member_id: UUID | None = None,
    role: str = ProjectRole.OWNER.value,
) -> UUID:
    user_id = member_id or uuid4()
    with factory() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, display_name=f"用户-{user_id.hex[:6]}"))
            session.flush()
        session.add(
            ProjectMember(project_id=project_id, user_id=user_id, role=role)
        )
        session.commit()
    return user_id


def snapshot(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "business_goal": "训练抓取策略",
        "model_type": "diffusion-policy",
        "training_type": "IMITATION",
        "data_modalities": ["RGB", "PROPRIOCEPTION"],
        "scene_targets": ["kitchen"],
        "action_targets": ["pick"],
        "robot_targets": ["arm-a"],
        "device_targets": ["cam-a"],
        "required_labels": ["action"],
        "required_schema_versions": [],
        "target_episode_count": 10,
        "target_duration_ns": None,
        "quality_thresholds": {"min_quality_score": 0.8},
        "split_requirements": {"train_ratio": 0.8, "validation_ratio": 0.1, "test_ratio": 0.1},
        "leakage_policy_id": None,
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
        "delivery_due_at": (NOW + timedelta(days=7)).isoformat(),
        "acceptance_method": "离线回放验收",
        "priority": "P1",
    }
    payload.update(overrides)
    return payload


def request_payload(owner_id: UUID, *, priority: str = "P1", **overrides: object) -> dict[str, object]:
    snapshot_payload = snapshot(priority=priority)
    snapshot_payload.update(overrides)
    return {
        "code": f"TR-{uuid4().hex[:8]}",
        "title": "抓取策略训练数据需求",
        "owner_id": str(owner_id),
        "priority": priority,
        "snapshot": snapshot_payload,
    }


def create_request(client, project_id: UUID, owner_id: UUID, **overrides: object) -> dict[str, object]:
    response = client.post(
        url(project_id, "/training-requests"),
        json=request_payload(owner_id, **overrides),
        headers={"Idempotency-Key": f"tr-create-{uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_revision(client, project_id: UUID, request_body: dict[str, object], revision_id: UUID | str):
    response = client.get(
        url(
            project_id,
            f"/training-requests/{request_body['training_request_id']}/revisions/{revision_id}",
        )
    )
    assert response.status_code == 200, response.text
    return response.json()


def submit_revision(client, project_id: UUID, request_body: dict[str, object]):
    request_id = request_body["training_request_id"]
    revision = get_revision(client, project_id, request_body, request_body["current_revision_id"])
    response = client.post(
        url(project_id, f"/training-requests/{request_id}/revisions/{revision['revision_id']}/actions/submit"),
        headers={"If-Match": f'"{revision["version"]}"'},
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


def approve_revision(client, project_id: UUID, request_body: dict[str, object]):
    request_id = request_body["training_request_id"]
    revision = get_revision(client, project_id, request_body, request_body["current_revision_id"])
    response = client.post(
        url(project_id, f"/training-requests/{request_id}/revisions/{revision['revision_id']}/actions/approve"),
        headers={"If-Match": f'"{revision["version"]}"'},
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


def approved_request(editor, approver, project_id: UUID, owner_id: UUID) -> dict[str, object]:
    created = create_request(editor, project_id, owner_id)
    submit_revision(editor, project_id, created)
    approve_revision(approver, project_id, created)
    response = approver.get(
        url(project_id, f"/training-requests/{created['training_request_id']}")
    )
    assert response.status_code == 200, response.text
    return response.json()


def build_deliverable(factory, ids) -> dict[str, object]:
    """构建冻结版本并成功导出，返回可交付上下文。"""
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    created = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": f"export-delivery-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["export_job_id"]
    result = run_export_worker(factory, storage)
    assert result.status == "PUBLISHED", result
    job = editor.get(url(ids.project_a, f"/exports/{job_id}")).json()
    assert job["status"] == "SUCCEEDED"
    return {
        **ctx,
        "storage": storage,
        "editor": editor,
        "export_job_id": job_id,
        "export_job": job,
    }


def delivery_payload(
    request_body: dict[str, object],
    ctx: dict[str, object],
    *,
    note_no: str | None = None,
    recipient_id: UUID | None = None,
) -> dict[str, object]:
    return {
        "training_request_id": request_body["training_request_id"],
        "training_request_revision_id": request_body["current_revision_id"],
        "dataset_version_id": ctx["version_id"],
        "export_job_id": ctx["export_job_id"],
        "recipient_id": str(recipient_id or uuid4()),
        "recipient_team": "算法组",
        "delivery_note_no": note_no or f"DN-{uuid4().hex[:8]}",
    }
