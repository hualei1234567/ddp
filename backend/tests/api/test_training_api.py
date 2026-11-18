"""TrainingRequest / Revision / Delivery HTTP API 测试。"""

from __future__ import annotations

import json
from uuid import uuid4

from app.api.v1.training_dependencies import get_training_adapter_gateway
from app.infrastructure.db.models.collection import OutboxEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.training_support import (
    FakeTrainingGateway,
    add_member,
    approved_request,
    build_deliverable,
    create_request,
    delivery_payload,
    get_revision,
    snapshot,
    submit_revision,
)


def test_training_request_revision_versioning_and_approval(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    approver_id = add_member(factory, ids.project_a, role="owner")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    approver = make_client(factory, approver_id)

    created = create_request(editor, ids.project_a, ids.owner_a)
    assert created["status"] == "DRAFT"
    assert created["current_revision_no"] == 1
    assert created["priority"] == "P1"
    revision = get_revision(editor, ids.project_a, created, created["current_revision_id"])
    assert revision["status"] == "DRAFT"
    assert revision["revision_sha256"].startswith("sha256:")

    submitted = submit_revision(owner, ids.project_a, created)
    assert submitted["status"] == "PENDING_REVIEW"
    assert submitted["submitted_by"] == str(ids.owner_a)

    self_approve = owner.post(
        url(
            ids.project_a,
            f"/training-requests/{created['training_request_id']}/revisions/{revision['revision_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{submitted["version"]}"'},
        json={},
    )
    assert self_approve.status_code == 403
    assert self_approve.json()["error"]["code"] == "training_request_approval_forbidden"

    approved = approver.post(
        url(
            ids.project_a,
            f"/training-requests/{created['training_request_id']}/revisions/{revision['revision_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{submitted["version"]}"'},
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["approved_by"] == str(approver_id)

    patched = editor.patch(
        url(
            ids.project_a,
            f"/training-requests/{created['training_request_id']}/revisions/{revision['revision_id']}",
        ),
        headers={"If-Match": f'"{approved.json()["version"]}"'},
        json={"snapshot": {**revision["snapshot"], "business_goal": "擅自修改"}},
    )
    assert patched.status_code == 409
    assert patched.json()["error"]["code"] == "training_request_immutable"

    new_revision = editor.post(
        url(ids.project_a, f"/training-requests/{created['training_request_id']}/revisions"),
        json={
            "snapshot": {**revision["snapshot"], "target_episode_count": 20},
            "change_reason": "扩大样本量",
        },
        headers={"Idempotency-Key": f"revision-{uuid4().hex[:8]}"},
    )
    assert new_revision.status_code == 201, new_revision.text
    body = new_revision.json()
    assert body["revision_no"] == 2
    assert body["supersedes_revision_id"] == revision["revision_id"]
    assert body["status"] == "DRAFT"

    old = get_revision(editor, ids.project_a, created, revision["revision_id"])
    assert old["status"] == "SUPERSEDED"
    assert old["revision_sha256"] == revision["revision_sha256"]

    request_after = editor.get(
        url(ids.project_a, f"/training-requests/{created['training_request_id']}")
    ).json()
    assert request_after["status"] == "DRAFT"
    assert request_after["current_revision_no"] == 2
    assert request_after["priority"] == "P1"

    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type.like("training.request.%"))
            )
            >= 3
        )


def test_training_request_validation_and_cross_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    owner = make_client(factory, ids.owner_a)
    outsider = make_client(factory, ids.outsider)

    invalid = editor.post(
        url(ids.project_a, "/training-requests"),
        json={
            "code": f"TR-{uuid4().hex[:8]}",
            "title": "缺字段需求",
            "owner_id": str(ids.owner_a),
            "priority": "P1",
            "snapshot": {"business_goal": "缺字段"},
        },
        headers={"Idempotency-Key": f"invalid-{uuid4().hex[:8]}"},
    )
    assert invalid.status_code == 422

    done = create_request(editor, ids.project_a, ids.owner_a)
    assert viewer.get(url(ids.project_a, "/training-requests")).status_code == 200
    assert outsider.get(url(ids.project_a, "/training-requests")).status_code == 403
    cross = owner.get(url(ids.project_b, f"/training-requests/{done['training_request_id']}"))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    viewer_write = viewer.post(
        url(ids.project_a, "/training-requests"),
        json={
            "code": f"TR-{uuid4().hex[:8]}",
            "title": "只读用户",
            "owner_id": str(ids.owner_a),
            "priority": "P1",
            "snapshot": snapshot(),
        },
    )
    assert viewer_write.status_code == 403


def test_training_delivery_accept_reject_rework_and_history_preserved(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    approver_id = add_member(factory, ids.project_a, role="owner")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    approver = make_client(factory, approver_id)
    ctx = build_deliverable(factory, ids)
    request_body = approved_request(editor, approver, ids.project_a, ids.owner_a)
    editor = ctx["editor"]

    created = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
        headers={"Idempotency-Key": f"delivery-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    delivery = created.json()
    assert delivery["status"] == "DRAFT"
    assert delivery["item_count"] == ctx["version"]["item_count"]
    assert delivery["artifact_manifest_sha256"] == ctx["export_job"]["artifact_manifest_sha256"]
    assert delivery["aggregate_sha256"] == ctx["export_job"]["aggregate_sha256"]

    missing_if_match = editor.post(
        url(
            ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"
        ),
        json={},
    )
    assert missing_if_match.status_code == 428

    ready = editor.post(
        url(
            ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"
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
    assert delivered.json()["status"] == "DELIVERED"

    self_accept = editor.post(
        url(
            ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/accept"
        ),
        headers={"If-Match": f'"{delivered.json()["version"]}"'},
        json={"acceptance_opinion": "自验"},
    )
    assert self_accept.status_code == 403
    assert self_accept.json()["error"]["code"] == "training_delivery_acceptance_forbidden"

    rejected = owner.post(
        url(
            ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/reject"
        ),
        headers={"If-Match": f'"{delivered.json()["version"]}"'},
        json={"reason": "划分比例与需求不一致"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    request_after_reject = owner.get(
        url(ids.project_a, f"/training-requests/{request_body['training_request_id']}")
    ).json()
    assert request_after_reject["status"] == "REVISION_REQUIRED"

    second = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
        headers={"Idempotency-Key": f"delivery-{uuid4().hex[:8]}"},
    )
    assert second.status_code == 201, second.text
    second_delivery = second.json()
    assert (
        second_delivery["training_request_revision_id"] == delivery["training_request_revision_id"]
    )

    reworked = editor.post(
        url(
            ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/rework"
        ),
        headers={"If-Match": f'"{rejected.json()["version"]}"'},
        json={
            "reason": "返工范围：重新导出并复核",
            "rework_ref": second_delivery["training_delivery_id"],
        },
    )
    assert reworked.status_code == 200, reworked.text
    assert reworked.json()["status"] == "REWORK"
    assert reworked.json()["rework_ref"] == second_delivery["training_delivery_id"]
    assert reworked.json()["dataset_version_id"] == delivery["dataset_version_id"]

    second_ready = editor.post(
        url(
            ids.project_a,
            f"/training-deliveries/{second_delivery['training_delivery_id']}/actions/ready",
        ),
        headers={"If-Match": f'"{second_delivery["version"]}"'},
        json={},
    )
    assert second_ready.status_code == 200, second_ready.text
    second_delivered = editor.post(
        url(
            ids.project_a,
            f"/training-deliveries/{second_delivery['training_delivery_id']}/actions/deliver",
        ),
        headers={"If-Match": f'"{second_ready.json()["version"]}"'},
        json={},
    )
    assert second_delivered.status_code == 200, second_delivered.text
    accepted = owner.post(
        url(
            ids.project_a,
            f"/training-deliveries/{second_delivery['training_delivery_id']}/actions/accept",
        ),
        headers={"If-Match": f'"{second_delivered.json()["version"]}"'},
        json={"acceptance_opinion": "Manifest、校验和与验收单一致"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"

    old = editor.get(
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}")
    ).json()
    assert old["status"] == "REWORK"
    assert old["dataset_version_id"] == delivery["dataset_version_id"]
    assert old["training_request_revision_id"] == delivery["training_request_revision_id"]

    request_final = owner.get(
        url(ids.project_a, f"/training-requests/{request_body['training_request_id']}")
    ).json()
    assert request_final["status"] == "ACCEPTED"


def test_delivery_requires_frozen_version_and_approved_revision(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    approver_id = add_member(factory, ids.project_a, role="owner")
    editor = make_client(factory, ids.editor_a)
    approver = make_client(factory, approver_id)
    ctx = build_deliverable(factory, ids)
    request_body = create_request(editor, ids.project_a, ids.owner_a)
    editor = ctx["editor"]

    not_approved = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
        headers={"Idempotency-Key": f"delivery-{uuid4().hex[:8]}"},
    )
    assert not_approved.status_code == 409
    assert not_approved.json()["error"]["code"] == "training_request_not_approved"

    submit_revision(editor, ids.project_a, request_body)
    revision = get_revision(
        editor,
        ids.project_a,
        request_body,
        request_body["current_revision_id"],
    )
    approver.post(
        url(
            ids.project_a,
            f"/training-requests/{request_body['training_request_id']}"
            f"/revisions/{request_body['current_revision_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{revision["version"]}"'},
        json={},
    )

    mismatched = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json={
            **delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
            "dataset_version_id": str(ctx["dataset"]["dataset_id"]),
        },
        headers={"Idempotency-Key": f"delivery-{uuid4().hex[:8]}"},
    )
    assert mismatched.status_code == 404


def _delivered_delivery(
    api_db: tuple[sessionmaker[Session], ApiIds], *, execution_mode: str | None
) -> tuple[TestClient, dict[str, object]]:
    factory, ids = api_db
    approver_id = add_member(factory, ids.project_a, role="owner")
    editor = make_client(factory, ids.editor_a)
    approver = make_client(factory, approver_id)
    ctx = build_deliverable(factory, ids)
    request_body = approved_request(editor, approver, ids.project_a, ids.owner_a)
    editor = ctx["editor"]
    editor.app.dependency_overrides[get_training_adapter_gateway] = lambda: FakeTrainingGateway(
        execution_mode
    )
    created = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
        headers={"Idempotency-Key": f"delivery-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    delivery = created.json()
    # 尚未提交外部训练时执行方式必须是 UNKNOWN，不能预填成真实训练。
    assert delivery["execution_mode"] == "UNKNOWN"
    ready = editor.post(
        url(
            ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"
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
    return editor, delivered.json()


def test_external_reference_persists_and_exposes_execution_mode(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    editor, delivery = _delivered_delivery(api_db, execution_mode="MOCK")
    base = f"/training-deliveries/{delivery['training_delivery_id']}/external-references"

    reference = editor.post(
        url(api_db[1].project_a, base),
        json={
            "adapter_id": "mock-training",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "execution_mode": "MOCK",
        },
        headers={"Idempotency-Key": f"ext-{uuid4().hex[:8]}"},
    )
    assert reference.status_code == 202, reference.text
    assert reference.json()["execution_mode"] == "MOCK"

    detail = editor.get(
        url(api_db[1].project_a, f"/training-deliveries/{delivery['training_delivery_id']}")
    ).json()
    assert detail["execution_mode"] == "MOCK"


def test_external_reference_persists_sanitized_training_run_fields(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """N7-002：配置摘要落库前脱敏、版本标识往返，且响应里不出现秘密原文。"""
    editor, delivery = _delivered_delivery(api_db, execution_mode="MOCK")
    base = f"/training-deliveries/{delivery['training_delivery_id']}/external-references"
    key = f"ext-{uuid4().hex[:8]}"
    gateway = FakeTrainingGateway("MOCK")
    editor.app.dependency_overrides[get_training_adapter_gateway] = lambda: gateway
    payload = {
        "adapter_id": "mock-training",
        "adapter_version": "1.0.0",
        "correlation_id": f"corr-{uuid4().hex[:8]}",
        "config": {
            "learning_rate": 0.001,
            "batch_size": 32,
            "api_key": "sk-live-should-not-leak",
            "hf_token": "hf_should-not-leak",
            "nested": {"password": "p@ss", "max_steps": 1000},
            "checkpoint_dir": "/home/trainer/checkpoints",
            "endpoint": "https://internal.example/api",
            "tag": "ddp.lerobot/1.0.0",
        },
        "code_version": "git:abc1234",
        "model_version": "policy-v3",
    }

    created = editor.post(
        url(api_db[1].project_a, base), json=payload, headers={"Idempotency-Key": key}
    )
    assert created.status_code == 202, created.text
    body = created.json()
    summary = body["config_summary"]
    assert summary["learning_rate"] == 0.001
    assert summary["batch_size"] == 32
    assert summary["api_key"] == "[已脱敏]"
    assert summary["hf_token"] == "[已脱敏]"
    assert summary["nested"] == {"password": "[已脱敏]", "max_steps": 1000}
    assert summary["tag"] == "ddp.lerobot/1.0.0"
    assert body["code_version"] == "git:abc1234"
    assert body["model_version"] == "policy-v3"

    serialized = json.dumps(body, ensure_ascii=False)
    for leaked in (
        "sk-live-should-not-leak",
        "hf_should-not-leak",
        "p@ss",
        "/home/trainer",
        "internal.example",
    ):
        assert leaked not in serialized

    # Adapter 收到的是完整配置（训练平台需要真实参数），DDP 落库/返回的只是摘要。
    assert gateway.submitted_configs == [payload["config"]]

    fetched = editor.get(
        url(api_db[1].project_a, f"/external-training-references/{body['external_ref_id']}")
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["config_summary"] == summary
    assert fetched.json()["code_version"] == "git:abc1234"
    assert fetched.json()["model_version"] == "policy-v3"

    # 同一幂等键 + 同一请求：重放返回同一条引用。
    replay = editor.post(
        url(api_db[1].project_a, base), json=payload, headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["external_ref_id"] == body["external_ref_id"]

    # 同一幂等键 + 不同配置：不是同一次提交，必须冲突而不是悄悄改写摘要。
    conflict = editor.post(
        url(api_db[1].project_a, base),
        json={**payload, "config": {**payload["config"], "learning_rate": 0.002}},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "training_external_run_conflict"


def test_external_reference_rejects_oversized_config_summary(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    editor, delivery = _delivered_delivery(api_db, execution_mode="MOCK")
    base = f"/training-deliveries/{delivery['training_delivery_id']}/external-references"
    response = editor.post(
        url(api_db[1].project_a, base),
        json={
            "adapter_id": "mock-training",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "config": {"padding": "x" * (8 * 1024 + 1)},
        },
        headers={"Idempotency-Key": f"ext-{uuid4().hex[:8]}"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_external_reference_replay_survives_execution_mode_config_change(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """部署调整/移除执行方式声明后，原样重放同一幂等键仍是合法请求。

    幂等语义要求"同一个键 + 同一个请求"返回原结果；执行方式是**部署配置**，
    它变化不能把已经成功的提交变成 422/409。只有请求显式声明了不同模式时，
    才属于"同一幂等键对应不同请求"。
    """
    editor, delivery = _delivered_delivery(api_db, execution_mode="MOCK")
    base = f"/training-deliveries/{delivery['training_delivery_id']}/external-references"
    key = f"replay-{uuid4().hex[:8]}"
    payload = {
        "adapter_id": "mock-training",
        "adapter_version": "1.0.0",
        "correlation_id": f"corr-{uuid4().hex[:8]}",
    }

    first = editor.post(
        url(api_db[1].project_a, base),
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202, first.text
    assert first.json()["execution_mode"] == "MOCK"

    # 部署侧移除了执行方式声明。
    editor.app.dependency_overrides[get_training_adapter_gateway] = lambda: FakeTrainingGateway(
        None
    )
    replay = editor.post(
        url(api_db[1].project_a, base),
        json=payload,
        headers={"Idempotency-Key": key},
    )

    assert replay.status_code == 202, replay.text
    assert replay.json()["external_ref_id"] == first.json()["external_ref_id"]
    assert replay.json()["execution_mode"] == "MOCK"


def test_external_reference_rejects_missing_or_conflicting_execution_mode(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    editor, delivery = _delivered_delivery(api_db, execution_mode=None)
    base = f"/training-deliveries/{delivery['training_delivery_id']}/external-references"

    missing = editor.post(
        url(api_db[1].project_a, base),
        json={
            "adapter_id": "unconfigured-training",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
        },
        headers={"Idempotency-Key": f"ext-{uuid4().hex[:8]}"},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_failed"

    # UNKNOWN 只描述存量记录，不是客户端可声明的执行方式。
    unknown = editor.post(
        url(api_db[1].project_a, base),
        json={
            "adapter_id": "unconfigured-training",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "execution_mode": "UNKNOWN",
        },
        headers={"Idempotency-Key": f"ext-{uuid4().hex[:8]}"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_failed"

    # Adapter 配置了 LOCAL，请求声称 REMOTE：不是同一次提交，必须拒绝。
    editor.app.dependency_overrides[get_training_adapter_gateway] = lambda: FakeTrainingGateway(
        "LOCAL"
    )
    conflict = editor.post(
        url(api_db[1].project_a, base),
        json={
            "adapter_id": "local-training",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "execution_mode": "REMOTE",
        },
        headers={"Idempotency-Key": f"ext-{uuid4().hex[:8]}"},
    )
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "validation_failed"
