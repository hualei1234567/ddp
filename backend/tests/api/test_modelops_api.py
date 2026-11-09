"""阶段 5 模型闭环 API 测试：模型/版本、评测、反馈转需求、缺口与隔离。"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.api.v1.modelops_dependencies import get_artifact_inspector
from app.application.modelops.ports import ArtifactInspector
from app.core.config import Settings

from tests.api.conftest import make_client
from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version

SHA = "sha256:" + "a" * 64
SECRET = "model-adapter-secret-0123456789abcdef"


class FakeInspector(ArtifactInspector):
    def __init__(self, size: int) -> None:
        self.size = size

    def object_size(self, object_key: str) -> int | None:
        return self.size


def _model_payload(owner_id, code: str | None = None) -> dict[str, object]:
    return {
        "code": code or f"model-{uuid4().hex[:8]}",
        "name": "抓取策略模型",
        "task_type": "IMITATION",
        "framework": "pytorch",
        "owner_id": str(owner_id),
    }


def _version_payload(size: int = 128) -> dict[str, object]:
    return {
        "artifact": {
            "object_key": "models/policy.onnx",
            "sha256": SHA,
            "size_bytes": size,
            "format": "ONNX",
        },
        "runtime": {
            "framework": "pytorch",
            "framework_version": "2.4.0",
            "python_version": "3.11",
        },
    }


def _create_model(client, project_id, owner_id) -> dict[str, object]:
    response = client.post(url(project_id, "/models"), json=_model_payload(owner_id))
    assert response.status_code == 201, response.text
    return response.json()


def _ready_version(client, project_id, model_id, size: int = 128) -> dict[str, object]:
    created = client.post(
        url(project_id, f"/models/{model_id}/versions"), json=_version_payload(size)
    )
    assert created.status_code == 201, created.text
    validated = client.post(
        url(
            project_id,
            f"/models/{model_id}/versions/{created.json()['model_version_id']}/actions/validate",
        ),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "READY"
    return validated.json()


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


def _settings() -> Settings:
    return Settings(env="local", model_adapter_shared_secrets={"mock": SECRET})


def _client(factory, user_id, inspector: FakeInspector):
    client = make_client(factory, user_id, settings_override=_settings())
    client.app.dependency_overrides[get_artifact_inspector] = lambda: inspector
    return client


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
    assert kitchen["suggested_episode_count"] == 8

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
