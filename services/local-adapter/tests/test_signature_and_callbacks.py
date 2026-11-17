from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import httpx
import pytest

from app.callbacks import (
    CallbackClient,
    encode_body,
    model_callback_payload,
    training_callback_payload,
)
from app.config import Settings
from app.signature import callback_headers, compute_signature


def test_signature_matches_ddp_algorithm() -> None:
    body = b'{"hello":"world"}'
    headers = callback_headers("secret", body, timestamp=1700000000, nonce="abcdefgh")
    expected = hmac.new(
        b"secret", b"1700000000.abcdefgh." + body, hashlib.sha256
    ).hexdigest()
    assert headers["X-DDP-Signature"] == f"sha256={expected}"
    assert headers["X-DDP-Timestamp"] == "1700000000"
    assert headers["X-DDP-Nonce"] == "abcdefgh"
    assert (
        compute_signature("secret", 1700000000, "abcdefgh", body)
        == headers["X-DDP-Signature"]
    )


def test_default_nonce_is_unique_and_in_range() -> None:
    body = b"{}"
    first = callback_headers("secret", body)
    second = callback_headers("secret", body)
    assert 8 <= len(first["X-DDP-Nonce"]) <= 128
    assert first["X-DDP-Nonce"] != second["X-DDP-Nonce"]


def test_training_callback_body_shape() -> None:
    payload = training_callback_payload(
        adapter_id="local-training",
        adapter_version="1.0.0",
        external_run_id="train-1",
        status="SUCCEEDED",
        model_ref={
            "model_id": "local-adapter/train-1",
            "model_version": "1.0.0",
            "registered_at": "2026-11-17T00:00:00+00:00",
        },
        payload={"metrics": {"train_accuracy": 0.9}},
    )
    assert set(payload) == {
        "event_id",
        "adapter_id",
        "adapter_version",
        "external_run_id",
        "external_status",
        "occurred_at",
        "model_ref",
        "payload",
    }
    assert set(payload["model_ref"]) == {"model_id", "model_version", "registered_at"}
    datetime.fromisoformat(str(payload["occurred_at"]))
    assert payload["external_status"] == "SUCCEEDED"


def test_training_callback_failed_requires_error_fields() -> None:
    payload = training_callback_payload(
        adapter_id="local-training",
        adapter_version="1.0.0",
        external_run_id="train-2",
        status="FAILED",
        error_code="blocked_media_manifest_missing",
        error_summary="缺少 media.jsonl",
    )
    assert payload["error_code"] == "blocked_media_manifest_missing"
    assert payload["error_summary"] == "缺少 media.jsonl"
    assert "model_ref" not in payload


def test_training_callback_rejects_unknown_model_ref_fields() -> None:
    with pytest.raises(ValueError):
        training_callback_payload(
            adapter_id="local-training",
            adapter_version="1.0.0",
            external_run_id="train-3",
            status="SUCCEEDED",
            model_ref={"model_id": "x", "object_key": "models/x"},
        )


def test_model_callback_body_shape_and_finite_metrics() -> None:
    payload = model_callback_payload(
        adapter_id="local-model",
        adapter_version="1.0.0",
        event_type="evaluation.completed",
        project_id=uuid4(),
        evaluation_id=uuid4(),
        metrics={"accuracy": 0.9, "bad": float("nan"), "label": "x"},  # type: ignore[dict-item]
        report_ref={"object_key": "evaluations/x/report.json"},
    )
    assert payload["event_type"] == "evaluation.completed"
    assert "evaluation_id" in payload
    assert payload["metrics"] == {"accuracy": 0.9}
    json.loads(encode_body(payload))


def _client(settings: Settings, handler) -> CallbackClient:
    transport = httpx.MockTransport(handler)
    return CallbackClient(
        settings,
        http_client=httpx.Client(transport=transport),
        sleep=lambda _: None,
    )


def test_http_callback_is_signed_over_raw_body_and_retries(settings: Settings) -> None:
    settings = replace(settings, callback_max_attempts=3)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(204)

    client = _client(settings, handler)
    body = training_callback_payload(
        adapter_id="local-training",
        adapter_version="1.0.0",
        external_run_id="train-1",
        status="RUNNING",
    )
    result = client.send_training(body, label="training:RUNNING")
    assert result.ok
    assert result.attempts == 3
    assert len(requests) == 3
    for request in requests:
        timestamp = request.headers["x-ddp-timestamp"]
        nonce = request.headers["x-ddp-nonce"]
        expected = hmac.new(
            settings.training_secret.encode(),
            f"{timestamp}.{nonce}.".encode() + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["x-ddp-signature"] == f"sha256={expected}"
        assert request.url.path == (
            "/api/v1/integrations/training-adapters/local-training/callbacks"
        )


def test_callback_retries_404_during_reference_registration_race(
    settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        return httpx.Response(204)

    client = _client(settings, handler)
    payload = model_callback_payload(
        adapter_id="local-model",
        adapter_version="1.0.0",
        event_type="evaluation.completed",
        project_id=uuid4(),
        evaluation_id=uuid4(),
    )
    result = client.send_model(payload, label="evaluation.completed")
    assert result.ok
    assert result.attempts == 2


def test_callback_stops_on_non_retryable_4xx(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, text="bad signature")

    client = _client(settings, handler)
    payload = training_callback_payload(
        adapter_id="local-training",
        adapter_version="1.0.0",
        external_run_id="train-1",
        status="RUNNING",
    )
    result = client.send_training(payload, label="training:RUNNING")
    assert not result.ok
    assert result.attempts == 1
    assert len(requests) == 1
