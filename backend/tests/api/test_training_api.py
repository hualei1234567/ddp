"""TrainingRequest / Revision / Delivery HTTP API 测试。"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.collection import OutboxEvent
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.training_support import (
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
    cross = owner.get(
        url(ids.project_b, f"/training-requests/{done['training_request_id']}")
    )
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
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"),
        json={},
    )
    assert missing_if_match.status_code == 428

    ready = editor.post(
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"),
        headers={"If-Match": f'"{delivery["version"]}"'},
        json={},
    )
    assert ready.status_code == 200, ready.text
    delivered = editor.post(
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/deliver"),
        headers={"If-Match": f'"{ready.json()["version"]}"'},
        json={},
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == "DELIVERED"

    self_accept = editor.post(
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/accept"),
        headers={"If-Match": f'"{delivered.json()["version"]}"'},
        json={"acceptance_opinion": "自验"},
    )
    assert self_accept.status_code == 403
    assert self_accept.json()["error"]["code"] == "training_delivery_acceptance_forbidden"

    rejected = owner.post(
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/reject"),
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
        second_delivery["training_request_revision_id"]
        == delivery["training_request_revision_id"]
    )

    reworked = editor.post(
        url(ids.project_a, f"/training-deliveries/{delivery['training_delivery_id']}/actions/rework"),
        headers={"If-Match": f'"{rejected.json()["version"]}"'},
        json={
            "reason": "返工范围：重新导出并复核",
            "rework_ref": second_delivery["training_delivery_id"],
        },
    )
    assert reworked.status_code == 200, reworked.text
    assert reworked.json()["status"] == "REWORK"
    assert reworked.json()["rework_ref"] == second_delivery["training_delivery_id"]
    assert (
        reworked.json()["dataset_version_id"] == delivery["dataset_version_id"]
    )

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
    approver.post(
        url(
            ids.project_a,
            f"/training-requests/{request_body['training_request_id']}/revisions/{request_body['current_revision_id']}/actions/approve",
        ),
        headers={"If-Match": f'"{get_revision(editor, ids.project_a, request_body, request_body["current_revision_id"])["version"]}"'},
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
