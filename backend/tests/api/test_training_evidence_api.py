"""训练过程证据（``training_run_evidence``）HTTP API 测试（内存 SQLite）。"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.api.v1.training_dependencies import get_training_adapter_gateway
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

_CONFIG_SHA = "sha256:" + "a" * 64
_CKPT_SHA = "sha256:" + "b" * 64
_NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def _seed_delivered_reference(
    factory: sessionmaker[Session],
    ids: ApiIds,
) -> dict[str, object]:
    """创建已交付的 TrainingDelivery 与一条 ExternalTrainingReference。"""
    approver_id = add_member(factory, ids.project_a, role="owner")
    ctx = build_deliverable(factory, ids)
    editor = ctx["editor"]
    approver = make_client(factory, approver_id, object_storage=ctx["storage"])
    gateway = FakeTrainingGateway("LOCAL")
    editor.app.dependency_overrides[get_training_adapter_gateway] = lambda: gateway

    request_body = approved_request(editor, approver, ids.project_a, ids.owner_a)
    created_delivery = editor.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(request_body, ctx, recipient_id=ids.owner_a),
        headers={"Idempotency-Key": f"deliv-ev-{uuid4().hex[:8]}"},
    )
    assert created_delivery.status_code == 201, created_delivery.text
    delivery = created_delivery.json()
    delivery_id = delivery["training_delivery_id"]

    ready = editor.post(
        url(ids.project_a, f"/training-deliveries/{delivery_id}/actions/ready"),
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

    ref_resp = editor.post(
        url(
            ids.project_a,
            f"/training-deliveries/{delivery['training_delivery_id']}/external-references",
        ),
        json={
            "adapter_id": "lerobot-local",
            "adapter_version": "0.3.3",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "execution_mode": "LOCAL",
            "config": {"policy": "ACT", "steps": 2},
        },
        headers={"Idempotency-Key": f"ref-ev-{uuid4().hex[:8]}"},
    )
    assert ref_resp.status_code == 202, ref_resp.text
    return {
        "editor": editor,
        "delivery": delivered.json(),
        "reference": ref_resp.json(),
        "version_id": ctx["version_id"],
    }


def _evidence_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_kind": "SMOKE",
        "device": "cpu",
        "runtime_versions": {"python": "3.11.10", "torch": "2.5.0", "lerobot": "0.3.3"},
        "config_sha256": _CONFIG_SHA,
        "seed": 42,
        "batch_size": 2,
        "steps": 2,
        "started_at": _NOW.isoformat(),
        "finished_at": (_NOW + timedelta(seconds=12)).isoformat(),
        "metrics_summary": {
            "gate": "GATE-B-VIDEO",
            "reload_ok": True,
            "model_safetensors_sha256": "b" * 64,
        },
        "checkpoint_ref": "checkpoints/000002/pretrained_model",
        "checkpoint_sha256": _CKPT_SHA,
        "exit_code": 0,
        "stdout_tail": "[3/3] Checkpoint reload ok",
    }
    payload.update(overrides)
    return payload


def test_training_evidence_post_and_get_roundtrip(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seeded = _seed_delivered_reference(factory, ids)
    editor = seeded["editor"]
    delivery_id = seeded["delivery"]["training_delivery_id"]
    ref_id = seeded["reference"]["external_ref_id"]

    created_smoke = editor.post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(),
    )
    assert created_smoke.status_code == 201, created_smoke.text
    smoke_body = created_smoke.json()
    assert smoke_body["project_id"] == str(ids.project_a)
    assert smoke_body["external_training_reference_id"] == ref_id
    assert smoke_body["dataset_version_id"] == seeded["version_id"]
    assert smoke_body["run_kind"] == "SMOKE"
    assert smoke_body["steps"] == 2
    assert smoke_body["checkpoint_sha256"] == _CKPT_SHA
    assert smoke_body["exit_code"] == 0
    assert smoke_body["status"] == "SUCCEEDED"

    # 同一引用允许登记多条证据（如失败的 REAL run，metrics 与 checkpoint 为 null）
    created_failed = editor.post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(
            run_kind="REAL",
            steps=100,
            exit_code=1,
            finished_at=None,
            metrics_summary=None,
            checkpoint_ref=None,
            checkpoint_sha256=None,
            stdout_tail="CUDA OOM",
        ),
    )
    assert created_failed.status_code == 201, created_failed.text
    failed_body = created_failed.json()
    assert failed_body["run_kind"] == "REAL"
    assert failed_body["exit_code"] == 1
    assert failed_body["status"] == "FAILED"
    assert failed_body["metrics_summary"] is None
    assert failed_body["checkpoint_sha256"] is None

    by_ref = editor.get(url(ids.project_a, f"/external-training-references/{ref_id}/evidence"))
    assert by_ref.status_code == 200, by_ref.text
    ref_items = by_ref.json()["items"]
    assert len(ref_items) == 2
    assert {item["evidence_id"] for item in ref_items} == {
        smoke_body["evidence_id"],
        failed_body["evidence_id"],
    }

    by_delivery = editor.get(url(ids.project_a, f"/training-deliveries/{delivery_id}/evidence"))
    assert by_delivery.status_code == 200, by_delivery.text
    delivery_items = by_delivery.json()["items"]
    assert len(delivery_items) == 2
    assert {item["run_kind"] for item in delivery_items} == {"SMOKE", "REAL"}


def test_training_evidence_project_isolation_and_rbac(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seeded = _seed_delivered_reference(factory, ids)
    delivery_id = seeded["delivery"]["training_delivery_id"]
    ref_id = seeded["reference"]["external_ref_id"]

    viewer = make_client(factory, ids.viewer_a)
    outsider = make_client(factory, ids.outsider)
    owner_b = make_client(factory, ids.owner_a)

    # Viewer 只读放行，写入 403
    assert (
        viewer.get(url(ids.project_a, f"/training-deliveries/{delivery_id}/evidence")).status_code
        == 200
    )
    assert (
        viewer.get(
            url(ids.project_a, f"/external-training-references/{ref_id}/evidence")
        ).status_code
        == 200
    )
    assert (
        viewer.post(
            url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
            json=_evidence_payload(),
        ).status_code
        == 403
    )

    # 非项目成员 403
    assert (
        outsider.get(url(ids.project_a, f"/training-deliveries/{delivery_id}/evidence")).status_code
        == 403
    )
    assert (
        outsider.post(
            url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
            json=_evidence_payload(),
        ).status_code
        == 403
    )

    # 跨项目隔离：在 project_b 下访问 project_a 的引用与交付，一律 404 不泄露存在性
    cross_get_ref = owner_b.get(
        url(ids.project_b, f"/external-training-references/{ref_id}/evidence")
    )
    assert cross_get_ref.status_code == 404
    assert cross_get_ref.json()["error"]["code"] == "resource_not_found"

    cross_post_ref = owner_b.post(
        url(ids.project_b, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(),
    )
    assert cross_post_ref.status_code == 404

    cross_get_delivery = owner_b.get(
        url(ids.project_b, f"/training-deliveries/{delivery_id}/evidence")
    )
    assert cross_get_delivery.status_code == 404

    # 传入不存在的 dataset_version_id -> 404
    missing_ver = seeded["editor"].post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(dataset_version_id=str(uuid4())),
    )
    assert missing_ver.status_code == 404


def test_training_evidence_validation_errors(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seeded = _seed_delivered_reference(factory, ids)
    editor = seeded["editor"]
    ref_id = seeded["reference"]["external_ref_id"]

    # 非法 sha256 格式 -> 422
    bad_sha = editor.post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(config_sha256="not-a-sha256"),
    )
    assert bad_sha.status_code == 422

    # steps=0 -> 422
    bad_steps = editor.post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(steps=0),
    )
    assert bad_steps.status_code == 422

    # finished_at 早于 started_at -> 422
    bad_time = editor.post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=_evidence_payload(finished_at=(_NOW - timedelta(seconds=1)).isoformat()),
    )
    assert bad_time.status_code == 422


def test_gate_b_video_train_payload_accepted_by_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``scripts/train/gate_b_video_train.py`` 构造的上报载荷与后端 API 100% 契约闭环。"""
    factory, ids = api_db
    seeded = _seed_delivered_reference(factory, ids)
    editor = seeded["editor"]
    ref_id = seeded["reference"]["external_ref_id"]

    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "train" / "gate_b_video_train.py"
    spec = importlib.util.spec_from_file_location("gate_b_video_train", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    raw_sha = "c" * 64
    sample_evidence = {
        "gate": "GATE-B-VIDEO",
        "sprint": "V6.1-Sprint-3",
        "git_commit": "deadbeef",
        "platform": "macOS-15-arm64",
        "python": "3.11.10",
        "dataset": {"episodes": 2, "frames_per_episode": 60, "total_frames": 120},
        "training": {
            "policy": "ACT",
            "device": "cpu",
            "steps": 2,
            "batch_size": 2,
            "seed": 42,
            "elapsed_seconds": 5.2,
            "torch": "2.5.0",
        },
        "checkpoint": {
            "dir": "checkpoints/000002/pretrained_model",
            "model_safetensors_sha256": raw_sha,
            "reload_forward_shape": [1, 2],
            "reload_ok": True,
        },
    }
    payload = module.build_evidence_payload(
        sample_evidence,
        run_kind="SMOKE",
        started_at=_NOW,
        finished_at=_NOW + timedelta(seconds=5),
    )
    resp = editor.post(
        url(ids.project_a, f"/external-training-references/{ref_id}/evidence"),
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["run_kind"] == "SMOKE"
    assert body["steps"] == 2
    assert body["checkpoint_sha256"] == f"sha256:{raw_sha}"
    assert body["metrics_summary"]["model_safetensors_sha256"] == raw_sha
    assert body["status"] == "SUCCEEDED"
