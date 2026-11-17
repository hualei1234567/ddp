from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.container import ServiceContainer
from app.main import create_app
from app.runs import JobRunner, RunRegistry

from tests.fakes import (
    DATASET_VERSION_ID,
    RecordingCallbacks,
    seed_export,
)


def _container(settings, storage) -> ServiceContainer:
    return ServiceContainer(
        settings=settings,
        storage=storage,
        callbacks=RecordingCallbacks(),
        registry=RunRegistry(),
        runner=JobRunner(max_workers=1, max_pending=8),
        ffmpeg_info=None,
    )


def _wait(
    client: TestClient, run_id: str, expected: set[str], timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    snapshot: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/training-runs/{run_id}")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot.get("internal_status", snapshot["status"]) in expected:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"运行未在 {timeout}s 内进入 {expected}：{snapshot}")


def test_health_and_training_submit_runs_in_background(settings, storage) -> None:
    seed_export(storage, with_media=False, with_labels=False)
    container = _container(settings, storage)
    app = create_app(container)
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert (
            health["adapters"]["training"]["adapter_id"] == settings.training_adapter_id
        )
        assert health["ffmpeg"]["available"] is False

        response = client.post(
            "/training-runs",
            json={
                "adapter_id": settings.training_adapter_id,
                "adapter_version": "1.0.0",
                "correlation_id": "corr-1",
                "delivery_id": "00000000-0000-0000-0000-000000000001",
                "dataset_version_id": DATASET_VERSION_ID,
                "config": {},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["external_run_id"].startswith("train-")
        assert body["accepted"] is True

        snapshot = _wait(
            client, body["external_run_id"], {"BLOCKED", "FAILED", "SUCCEEDED"}
        )
        assert snapshot["internal_status"] == "BLOCKED"
        assert snapshot["status"] == "FAILED"  # DDP 只接受 ExternalTrainingStatus
        assert snapshot["error_code"] == "blocked_media_manifest_missing"


def test_unknown_adapter_and_missing_run(settings, storage) -> None:
    container = _container(settings, storage)
    app = create_app(container)
    with TestClient(app) as client:
        response = client.post(
            "/training-runs",
            json={
                "adapter_id": "someone-else",
                "adapter_version": "1.0.0",
                "correlation_id": "corr-2",
                "delivery_id": "00000000-0000-0000-0000-000000000002",
                "dataset_version_id": DATASET_VERSION_ID,
                "config": {},
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"]["code"] == "unknown_adapter"

        assert client.get("/training-runs/missing").status_code == 404
        assert client.post("/training-runs/missing/cancel").status_code == 404


def test_training_cancel_of_finished_run_is_reported_not_accepted(
    settings, storage
) -> None:
    seed_export(storage, with_media=False, with_labels=False)
    container = _container(settings, storage)
    app = create_app(container)
    with TestClient(app) as client:
        body = client.post(
            "/training-runs",
            json={
                "adapter_id": settings.training_adapter_id,
                "adapter_version": "1.0.0",
                "correlation_id": "corr-3",
                "delivery_id": "00000000-0000-0000-0000-000000000003",
                "dataset_version_id": DATASET_VERSION_ID,
                "config": {},
            },
        ).json()
        _wait(client, body["external_run_id"], {"BLOCKED", "FAILED", "SUCCEEDED"})
        cancel = client.post(f"/training-runs/{body['external_run_id']}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["accepted"] is False


def test_training_delivery_id_alias_and_inference_tasks_path(settings, storage) -> None:
    seed_export(storage, with_media=False, with_labels=False)
    container = _container(settings, storage)
    app = create_app(container)
    with TestClient(app) as client:
        response = client.post(
            "/training-runs",
            json={
                "adapter_id": settings.training_adapter_id,
                "adapter_version": "1.0.0",
                "correlation_id": "corr-4",
                "training_delivery_id": "00000000-0000-0000-0000-000000000004",
                "dataset_version_id": DATASET_VERSION_ID,
                "config": {},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["external_run_id"].startswith("train-")

        accepted = client.post(
            "/inference-tasks",
            json={
                "adapter_id": settings.model_adapter_id,
                "adapter_version": "1.0.0",
                "task_id": "00000000-0000-0000-0000-000000000005",
                "model_version_id": "00000000-0000-0000-0000-000000000006",
                "dataset_version_id": DATASET_VERSION_ID,
                "episode_ids": [],
                "config": {"project_id": "1bca073d-c9af-4601-9b0f-7888d0747db0"},
            },
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["run_id"].startswith("infer-")
