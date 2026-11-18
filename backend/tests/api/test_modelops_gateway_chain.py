"""模型 Adapter 出站守卫的 API 级回归（N8-008）。

网关单测（``tests/unit/test_adapter_url_guard.py``）已覆盖地址筛查本身；本文件
只验证它在 API 链路上的两件事：

1. 公网 endpoint 下，"提交评测（真实网关 + MockTransport）→ HMAC 回调完成"
   这条既有链路仍然完整，守卫不误杀；
2. 私网 endpoint 被拒时，错误映射为**既有**的
   ``model_adapter_unavailable``（503），不新增错误码，也不触达传输层。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from app.api.v1.modelops_dependencies import get_model_gateway
from app.core.config import Settings
from app.infrastructure.model_platform_http import HttpModelPlatformGateway

from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version
from tests.api.modelops_support import SECRET, FakeInspector
from tests.api.modelops_support import client as _client
from tests.api.modelops_support import create_model as _create_model
from tests.api.modelops_support import ready_version as _ready_version

# 公网 IP 字面量：守卫会解析主机名，MockTransport 不联网，不能用假域名。
_PUBLIC_MODEL_ENDPOINT = "http://93.184.216.34:9101"


def _signed_headers(body: bytes, *, nonce: str) -> dict[str, str]:
    ts = int(datetime.now(UTC).timestamp())
    digest = hmac.new(SECRET.encode(), f"{ts}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-DDP-Signature": f"sha256={digest}",
        "X-DDP-Timestamp": str(ts),
        "X-DDP-Nonce": nonce,
        "Content-Type": "application/json",
    }


def _settings(endpoint: str) -> Settings:
    # 显式钉住安全默认值并忽略开发机的 .env：本用例断言「私网 endpoint 必须
    # 被拒绝」，不能因为某个开发者本地为 local-adapter 打开了 allow_private
    # 就变成放行（CI 无 .env，两种环境必须同结论）。
    return Settings(
        _env_file=None,
        env="local",
        model_adapter_shared_secrets={"mock": SECRET},
        model_adapter_capabilities={"mock": ["evaluate"]},
        model_adapter_endpoints={"mock": endpoint},
        model_adapter_allow_private=False,
    )


def _gateway_with_ack(settings: Settings, seen: list[httpx.Request]) -> HttpModelPlatformGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    return HttpModelPlatformGateway(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _create_evaluation(editor, factory, ids, adapter_id: str = "mock") -> httpx.Response:
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    context = build_frozen_version(factory, ids)
    return editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": str(context["version_id"]),
            "thresholds": {"success_rate": {"min": 0.9}},
            "adapter_id": adapter_id,
        },
    )


def test_guarded_gateway_submit_then_callback_completes(api_db) -> None:
    """公网 endpoint：提交走真实网关，回调完成链路不受守卫影响。"""
    factory, ids = api_db
    settings = _settings(_PUBLIC_MODEL_ENDPOINT)
    editor = _client(factory, ids.editor_a, FakeInspector(128), settings_override=settings)
    seen: list[httpx.Request] = []
    gateway = _gateway_with_ack(settings, seen)
    editor.app.dependency_overrides[get_model_gateway] = lambda: gateway

    created = _create_evaluation(editor, factory, ids)
    assert created.status_code == 202, created.text
    evaluation = created.json()
    assert [str(request.url) for request in seen] == [f"{_PUBLIC_MODEL_ENDPOINT}/evaluations"]

    body = json.dumps(
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": evaluation["evaluation_id"],
            "metrics": {"success_rate": 0.42},
        },
        separators=(",", ":"),
    ).encode()
    callback = editor.post(
        "/api/v1/integrations/model-adapters/mock/callbacks",
        content=body,
        headers=_signed_headers(body, nonce=f"nonce-{uuid4().hex[:8]}"),
    )
    assert callback.status_code == 204, callback.text

    completed = editor.get(url(ids.project_a, f"/evaluations/{evaluation['evaluation_id']}")).json()
    assert completed["status"] == "SUCCEEDED"
    assert completed["verdict"] == "FAIL"


def test_private_model_endpoint_maps_to_existing_unavailable_code(api_db) -> None:
    """私网 endpoint：503 + 既有错误码，且不发出任何出站请求。"""
    factory, ids = api_db
    settings = _settings("http://127.0.0.1:9101")
    editor = _client(factory, ids.editor_a, FakeInspector(128), settings_override=settings)
    seen: list[httpx.Request] = []
    gateway = _gateway_with_ack(settings, seen)
    editor.app.dependency_overrides[get_model_gateway] = lambda: gateway

    rejected = _create_evaluation(editor, factory, ids)

    assert rejected.status_code == 503, rejected.text
    assert rejected.json()["error"]["code"] == "model_adapter_unavailable"
    assert seen == []
