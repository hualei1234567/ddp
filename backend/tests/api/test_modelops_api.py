"""阶段 5 模型闭环 API 测试：模型/版本、评测、反馈转需求、缺口与隔离。"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings

from tests.api.conftest import ApiIds
from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version
from tests.api.modelops_support import SECRET, SHA, FakeInspector
from tests.api.modelops_support import client as _client
from tests.api.modelops_support import create_model as _create_model
from tests.api.modelops_support import feedback_with_dimension as _feedback_with_dimension
from tests.api.modelops_support import model_payload as _model_payload
from tests.api.modelops_support import ready_version as _ready_version
from tests.api.modelops_support import version_payload as _version_payload


def _signed_headers(body: bytes, *, nonce: str, timestamp: int | None = None) -> dict[str, str]:
    ts = timestamp or int(datetime.now(UTC).timestamp())
    digest = hmac.new(SECRET.encode(), f"{ts}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-DDP-Signature": f"sha256={digest}",
        "X-DDP-Timestamp": str(ts),
        "X-DDP-Nonce": nonce,
        "Content-Type": "application/json",
    }


def _callback(client, payload: dict[str, object], *, nonce: str | None = None):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/api/v1/integrations/model-adapters/mock/callbacks",
        content=body,
        headers=_signed_headers(body, nonce=nonce or f"nonce-{uuid4().hex[:8]}"),
    )


def test_model_version_publish_and_isolation(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    outsider = _client(factory, ids.outsider, FakeInspector(128))

    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])

    published = owner.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/publish",
        ),
        headers={"If-Match": f'"{version["version"]}"'},
        json={},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "RELEASED"

    # 发布后版本内容不可变；再次 validate/publish 均被拒绝。
    again = owner.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/publish",
        ),
        headers={"If-Match": f'"{published.json()["version"]}"'},
        json={},
    )
    assert again.status_code == 409

    forbidden = outsider.get(url(ids.project_a, f"/models/{model['model_id']}"))
    assert forbidden.status_code in {403, 404}


def test_artifact_size_mismatch_blocks_ready(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(99))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    created = editor.post(
        url(ids.project_a, f"/models/{model['model_id']}/versions"), json=_version_payload(128)
    )
    assert created.status_code == 201, created.text
    validated = editor.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{created.json()['model_version_id']}/actions/validate",
        ),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 422


def test_evaluation_feedback_requirement_closed_loop(api_db) -> None:
    factory, ids = api_db
    inspector = FakeInspector(128)
    editor = _client(factory, ids.editor_a, inspector)
    owner = _client(factory, ids.owner_a, inspector)

    context = build_frozen_version(factory, ids)
    version_id = context["version_id"]

    model = _create_model(editor, ids.project_a, ids.owner_a)
    model_version = _ready_version(editor, ids.project_a, model["model_id"])

    created = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": model_version["model_version_id"],
            "dataset_version_id": str(version_id),
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert created.status_code == 202, created.text
    evaluation = created.json()
    assert evaluation["status"] == "QUEUED"

    callback = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": evaluation["evaluation_id"],
            "metrics": {"success_rate": 0.42},
        },
    )
    assert callback.status_code == 204, callback.text
    completed = editor.get(url(ids.project_a, f"/evaluations/{evaluation['evaluation_id']}")).json()
    assert completed["status"] == "SUCCEEDED"
    assert completed["verdict"] == "FAIL"

    feedback = editor.post(
        url(ids.project_a, "/model-feedback"),
        json={
            "model_version_id": model_version["model_version_id"],
            "evaluation_id": evaluation["evaluation_id"],
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "厨房场景抓取成功率仅 0.42",
            "dimensions": [
                {
                    "dimension_type": "SCENE",
                    "dimension_value": "kitchen",
                    "current_count": 2,
                    "target_count": 10,
                }
            ],
            "evidence_refs": ["evaluations/report.json"],
        },
    )
    assert feedback.status_code == 201, feedback.text
    feedback_body = feedback.json()

    gaps = editor.get(url(ids.project_a, "/data-gaps")).json()
    kitchen = next(item for item in gaps["items"] if item["dimension_value"] == "kitchen")
    assert kitchen["open_feedback_count"] == 1
    # 反馈里人工填的 current_count=2 已被真实覆盖取代：本项目有 10 个
    # ACCEPTED/PASS 的 kitchen Episode，目标 10 已满足，建议补采为 0。
    assert kitchen["coverage_source"] == "MEASURED"
    assert kitchen["current_count"] == 10
    assert kitchen["suggested_episode_count"] == 0

    converted = editor.post(
        url(
            ids.project_a,
            f"/model-feedback/{feedback_body['feedback_id']}/actions/create-requirement",
        ),
        headers={"If-Match": f'"{feedback_body["version"]}"'},
        json={"requirement_code": f"DR-{uuid4().hex[:6]}", "target_episode_count": 8},
    )
    assert converted.status_code == 200, converted.text
    converted_feedback = converted.json()
    assert converted_feedback["status"] == "REQUIREMENT_CREATED"
    requirement_id = converted_feedback["converted_requirement_id"]

    requirement = editor.get(url(ids.project_a, f"/data-requirements/{requirement_id}")).json()
    assert requirement["source_feedback_ids"] == [feedback_body["feedback_id"]]

    submitted = editor.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/submit"),
        headers={"If-Match": f'"{requirement["version"]}"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text

    # 提交人不得自批
    self_approve = editor.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert self_approve.status_code == 403

    approved = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert approved.status_code == 200, approved.text

    satisfied = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/satisfy"),
        headers={"If-Match": f'"{approved.json()["version"]}"'},
        json={"dataset_version_ids": [str(version_id)]},
    )
    assert satisfied.status_code == 200, satisfied.text
    assert satisfied.json()["status"] == "SATISFIED"
    assert satisfied.json()["linked_dataset_version_ids"] == [str(version_id)]

    retest = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": model_version["model_version_id"],
            "dataset_version_id": str(version_id),
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert retest.status_code == 202, retest.text
    retest_id = retest.json()["evaluation_id"]
    passed = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": retest_id,
            "metrics": {"success_rate": 0.95},
        },
    )
    assert passed.status_code == 204, passed.text

    linked = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/link-retest"),
        headers={"If-Match": f'"{satisfied.json()["version"]}"'},
        json={"evaluation_id": retest_id},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["retest_evaluation_ids"] == [retest_id]

    closed = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/close"),
        headers={"If-Match": f'"{linked.json()["version"]}"'},
        json={},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"


def test_requirement_satisfy_requires_frozen_version(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    owner = _client(factory, ids.owner_a, FakeInspector(128))

    requirement = editor.post(
        url(ids.project_a, "/data-requirements"),
        json={
            "code": f"DR-{uuid4().hex[:6]}",
            "title": "补采厨房场景",
            "source_type": "MANUAL",
            "dimensions": [{"dimension_type": "SCENE", "dimension_value": "kitchen"}],
            "owner_id": str(ids.owner_a),
        },
    )
    assert requirement.status_code == 201, requirement.text
    body = requirement.json()
    requirement_id = body["requirement_id"]

    submitted = editor.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/submit"),
        headers={"If-Match": f'"{body["version"]}"'},
        json={},
    )
    assert submitted.status_code == 200
    approved = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert approved.status_code == 200

    unsatisfied = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/satisfy"),
        headers={"If-Match": f'"{approved.json()["version"]}"'},
        json={"dataset_version_ids": []},
    )
    assert unsatisfied.status_code == 409

    close_early = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/close"),
        headers={"If-Match": f'"{approved.json()["version"]}"'},
        json={},
    )
    assert close_early.status_code == 409


def test_model_callback_replay_rejected(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])

    task = editor.post(
        url(ids.project_a, "/inference-tasks"),
        json={
            "model_version_id": version["model_version_id"],
            "episode_ids": [str(uuid4())],
        },
    )
    assert task.status_code == 202, task.text
    task_id = task.json()["task_id"]

    body = {
        "event_id": "evt-fixed",
        "adapter_version": "1.0.0",
        "event_type": "inference.completed",
        "project_id": str(ids.project_a),
        "task_id": task_id,
        "result_ref": {"object_key": "inference/result.json", "sha256": SHA},
        "metrics": {"latency_ms": 12.0},
    }
    first = _callback(editor, body, nonce="nonce-12345678")
    assert first.status_code == 204, first.text
    assert (
        editor.get(url(ids.project_a, f"/inference-tasks/{task_id}")).json()["status"]
        == "SUCCEEDED"
    )

    replay = _callback(editor, {**body, "metrics": {"latency_ms": 99.0}}, nonce="nonce-87654321")
    assert replay.status_code == 409

    valid_shape = json.dumps(
        {
            "event_id": "evt-bad-signature",
            "adapter_version": "1.0.0",
            "event_type": "inference.completed",
            "project_id": str(ids.project_a),
            "task_id": task_id,
        },
        separators=(",", ":"),
    ).encode()
    bad = editor.post(
        "/api/v1/integrations/model-adapters/mock/callbacks",
        content=valid_shape,
        headers={
            "X-DDP-Signature": "sha256=" + "0" * 64,
            "X-DDP-Timestamp": str(int(datetime.now(UTC).timestamp())),
            "X-DDP-Nonce": "nonce-00000000",
        },
    )
    assert bad.status_code == 401


def test_publish_requires_different_actor(api_db) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    # 负责人自己是版本创建人时，角色门禁通过，但领域规则禁止自审发布。
    model = _create_model(owner, ids.project_a, ids.owner_a)
    version = _ready_version(owner, ids.project_a, model["model_id"])

    response = owner.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/publish",
        ),
        headers={"If-Match": f'"{version["version"]}"'},
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_release_approval_forbidden"


def test_inference_task_without_adapter_returns_503(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])

    task = editor.post(
        url(ids.project_a, "/inference-tasks"),
        json={
            "model_version_id": version["model_version_id"],
            "episode_ids": [str(uuid4())],
            "adapter_id": "mock",
        },
    )
    assert task.status_code == 503
    assert task.json()["error"]["code"] == "model_adapter_unavailable"


def test_precondition_and_evaluation_dataset_gate(api_db) -> None:
    from tests.api.dataset_support import create_dataset, create_policies, version_payload

    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])

    missing = editor.post(
        url(ids.project_a, f"/models/{model['model_id']}/actions/activate"),
        json={},
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"gate-{uuid4().hex[:6]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created_version = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created_version.status_code == 201, created_version.text

    evaluation = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": created_version.json()["dataset_version_id"],
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert evaluation.status_code == 409
    assert evaluation.json()["error"]["code"] == "evaluation_dataset_not_ready"


def test_requirement_creator_cannot_self_approve(api_db) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    created = owner.post(
        url(ids.project_a, "/data-requirements"),
        json={
            "code": f"DR-{uuid4().hex[:6]}",
            "title": "自审拦截验证",
            "source_type": "MANUAL",
            "dimensions": [{"dimension_type": "SCENE", "dimension_value": "kitchen"}],
            "owner_id": str(ids.owner_a),
        },
    )
    assert created.status_code == 201, created.text
    requirement_id = created.json()["requirement_id"]
    submitted = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/submit"),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    approved = owner.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert approved.status_code == 403
    assert approved.json()["error"]["code"] == "data_requirement_approval_forbidden"


def test_artifact_checksum_mismatch_blocks_ready(api_db) -> None:
    factory, ids = api_db
    wrong_sha = "sha256:" + "b" * 64
    editor = _client(factory, ids.editor_a, FakeInspector(128, sha=wrong_sha))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    created = editor.post(
        url(ids.project_a, f"/models/{model['model_id']}/versions"), json=_version_payload(128)
    )
    assert created.status_code == 201, created.text
    validated = editor.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{created.json()['model_version_id']}/actions/validate",
        ),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 422
    assert validated.json()["error"]["code"] == "model_artifact_checksum_mismatch"


def test_completed_evaluation_is_immutable(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    created = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": str(context["version_id"]),
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert created.status_code == 202, created.text
    evaluation_id = created.json()["evaluation_id"]
    first = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": evaluation_id,
            "metrics": {"success_rate": 0.95},
        },
    )
    assert first.status_code == 204, first.text
    again = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": evaluation_id,
            "metrics": {"success_rate": 0.1},
        },
    )
    # 已完成评测的重复回调按幂等处理，但结果不得被覆盖。
    assert again.status_code == 204
    stored = editor.get(url(ids.project_a, f"/evaluations/{evaluation_id}")).json()
    assert stored["metrics"] == {"success_rate": 0.95}
    assert stored["verdict"] == "PASS"


def test_evaluation_rejects_cross_project_dataset(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    from app.domain.identity.models import ProjectRole
    from app.infrastructure.db.models.identity import ProjectMember

    with factory() as session:
        session.add(
            ProjectMember(
                project_id=ids.project_b,
                user_id=ids.editor_a,
                role=ProjectRole.EDITOR.value,
            )
        )
        session.commit()
    foreign_ids = ApiIds(
        project_a=ids.project_b,
        project_b=ids.project_a,
        editor_a=ids.editor_a,
        owner_a=ids.owner_a,
        viewer_a=ids.owner_a,
        outsider=ids.outsider,
    )
    foreign = build_frozen_version(factory, foreign_ids)
    response = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": str(foreign["version_id"]),
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_archive_blocked_by_active_service(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    published = owner.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/publish",
        ),
        headers={"If-Match": f'"{version["version"]}"'},
        json={},
    )
    assert published.status_code == 200, published.text
    service = editor.post(
        url(ids.project_a, "/inference-services"),
        json={
            "model_version_id": version["model_version_id"],
            "environment": "DEV",
            "endpoint_url": "http://inference.internal:8080",
        },
    )
    assert service.status_code == 201, service.text
    service_id = service.json()["service_id"]
    deployed = editor.post(
        url(ids.project_a, f"/inference-services/{service_id}/actions/deploy"),
        headers={"If-Match": f'"{service.json()["version"]}"'},
        json={},
    )
    assert deployed.status_code == 200, deployed.text
    active = editor.post(
        url(ids.project_a, f"/inference-services/{service_id}/actions/activate"),
        headers={"If-Match": f'"{deployed.json()["version"]}"'},
        json={},
    )
    assert active.status_code == 200, active.text
    assert active.json()["status"] == "ACTIVE"

    archived = owner.post(
        url(
            ids.project_a,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/archive",
        ),
        headers={"If-Match": f'"{published.json()["version"]}"'},
        json={"reason": "尝试在服务运行中归档"},
    )
    assert archived.status_code == 409
    assert archived.json()["error"]["code"] == "model_version_in_use"


def test_requirement_terminal_cannot_reopen(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    created = editor.post(
        url(ids.project_a, "/data-requirements"),
        json={
            "code": f"DR-{uuid4().hex[:6]}",
            "title": "终态不可回退验证",
            "source_type": "MANUAL",
            "dimensions": [{"dimension_type": "SCENE", "dimension_value": "kitchen"}],
            "owner_id": str(ids.owner_a),
        },
    )
    assert created.status_code == 201, created.text
    requirement_id = created.json()["requirement_id"]
    cancelled = editor.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/cancel"),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={"reason": "需求取消"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    again = editor.post(
        url(ids.project_a, f"/data-requirements/{requirement_id}/actions/submit"),
        headers={"If-Match": f'"{cancelled.json()["version"]}"'},
        json={},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "data_requirement_terminal"


def test_idempotency_key_reuse_with_different_body(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    key = f"idem-{uuid4().hex[:12]}"
    payload = _model_payload(ids.owner_a)
    first = editor.post(
        url(ids.project_a, "/models"),
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    replay = editor.post(
        url(ids.project_a, "/models"),
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["model_id"] == first.json()["model_id"]
    different = editor.post(
        url(ids.project_a, "/models"),
        json={**payload, "name": "不同请求体"},
        headers={"Idempotency-Key": key},
    )
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "idempotency_key_reused"


def test_inference_success_without_result_ref_is_rejected(api_db) -> None:
    """AC-IN-02：结果未知不得记为成功。成功回调缺结果引用 → 422 且任务不变。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    task = editor.post(
        url(ids.project_a, "/inference-tasks"),
        json={"model_version_id": version["model_version_id"], "episode_ids": [str(uuid4())]},
    )
    assert task.status_code == 202, task.text
    task_id = task.json()["task_id"]

    rejected = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "inference.completed",
            "project_id": str(ids.project_a),
            "task_id": task_id,
            "metrics": {"latency_ms": 12.0},
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "inference_task_result_invalid"
    stored = editor.get(url(ids.project_a, f"/inference-tasks/{task_id}")).json()
    assert stored["status"] == "QUEUED"
    assert stored["result_ref"] is None


def test_inference_result_ref_with_bad_hash_is_rejected(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    task = editor.post(
        url(ids.project_a, "/inference-tasks"),
        json={"model_version_id": version["model_version_id"], "episode_ids": [str(uuid4())]},
    )
    assert task.status_code == 202, task.text

    rejected = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "inference.completed",
            "project_id": str(ids.project_a),
            "task_id": task.json()["task_id"],
            "result_ref": {"object_key": "inference/result.json", "sha256": "sha256:zz"},
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "inference_task_result_invalid"


def test_adapter_without_declared_capability_is_rejected(api_db) -> None:
    """Adapter 已登记但未声明 evaluate 能力 → 422 model_adapter_unsupported。"""
    factory, ids = api_db
    editor = _client(
        factory,
        ids.editor_a,
        FakeInspector(128),
        settings_override=Settings(
            env="local",
            model_adapter_shared_secrets={"mock": SECRET},
            model_adapter_capabilities={"mock": ["inference"]},
        ),
    )
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    response = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": str(context["version_id"]),
            "thresholds": {"success_rate": {"min": 0.9}},
            "adapter_id": "mock",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "model_adapter_unsupported"


def test_adapter_with_declared_capability_still_hits_unavailable(api_db) -> None:
    """能力已声明但真实客户端未配置 → 仍需 503，两个码语义不可混用。"""
    factory, ids = api_db
    editor = _client(
        factory,
        ids.editor_a,
        FakeInspector(128),
        settings_override=Settings(
            env="local",
            model_adapter_shared_secrets={"mock": SECRET},
            model_adapter_capabilities={"mock": ["evaluate"]},
        ),
    )
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    response = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": str(context["version_id"]),
            "thresholds": {"success_rate": {"min": 0.9}},
            "adapter_id": "mock",
        },
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "model_adapter_unavailable"


def test_training_source_must_exist_in_project(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    response = editor.post(
        url(ids.project_a, f"/models/{model['model_id']}/versions"),
        json={**_version_payload(), "training_request_id": str(uuid4())},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "model_training_source_invalid"


def test_archived_model_rejects_new_version(api_db) -> None:
    """Model 的 ARCHIVED 是终态（state-machines.v1.json），归档后不得新增版本。"""
    factory, ids = api_db
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    model = _create_model(owner, ids.project_a, ids.owner_a)
    activated = owner.post(
        url(ids.project_a, f"/models/{model['model_id']}/actions/activate"),
        headers={"If-Match": f'"{model["version"]}"'},
        json={},
    )
    assert activated.status_code == 200, activated.text
    archived = owner.post(
        url(ids.project_a, f"/models/{model['model_id']}/actions/archive"),
        headers={"If-Match": f'"{activated.json()["version"]}"'},
        json={"reason": "模型下线"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"

    rejected = owner.post(
        url(ids.project_a, f"/models/{model['model_id']}/versions"),
        json=_version_payload(),
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "model_not_active"


# --------------------------------------------------------------------------- #
# 数据缺口：真实覆盖联动（FBK-002）
# --------------------------------------------------------------------------- #
def test_data_gaps_uses_measured_episode_coverage(api_db) -> None:
    """缺口摘要的 current_count / coverage_source 来自真实 Episode 统计。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    assert context["version_id"]

    # 反馈里故意把人工值填错（2），真实覆盖是 10 —— 必须以实测为准。
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version["model_version_id"],
        dimension_type="SCENE",
        dimension_value="kitchen",
        current_count=2,
        target_count=25,
    )
    gaps = editor.get(url(ids.project_a, "/data-gaps")).json()
    kitchen = next(item for item in gaps["items"] if item["dimension_value"] == "kitchen")
    assert kitchen["coverage_source"] == "MEASURED"
    assert kitchen["current_count"] == 10
    assert kitchen["suggested_episode_count"] == 15
    # 有实测时间戳且在阈值内 → FRESH，而不是此前的硬编码常量。
    assert gaps["freshness"] == "FRESH"


def test_data_gaps_unknown_when_no_manual_value_and_no_coverage(api_db) -> None:
    """既不填人工值、又测不到时必须是 UNKNOWN + 建议补采 0（不臆测）。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    assert context["version_id"]

    _feedback_with_dimension(
        editor,
        ids.project_a,
        version["model_version_id"],
        dimension_type="SCENE",
        dimension_value="moon-base",
        target_count=40,
    )
    gaps = editor.get(url(ids.project_a, "/data-gaps")).json()
    unknown = next(item for item in gaps["items"] if item["dimension_value"] == "moon-base")
    assert unknown["coverage_source"] == "UNKNOWN"
    assert unknown["current_count"] is None
    assert unknown["suggested_episode_count"] == 0
