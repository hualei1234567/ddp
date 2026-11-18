"""训练 / 模型平台 Adapter 的 HTTP 网关单元测试。

不联网：用 ``httpx.MockTransport`` 替换传输层，网关本身（URL 构造、请求体、
状态码与异常映射、响应解析）全部走生产实现。

覆盖验收要求的四类情形：成功、超时、5xx、缺配置；另加传输层连接失败、
4xx、响应协议违规（未知状态、缺字段），确保"不吞异常、不返回假成功"。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
from app.api.v1.modelops_dependencies import get_model_gateway
from app.api.v1.training_dependencies import get_training_adapter_gateway
from app.core.config import Settings
from app.domain.datasets.training_enums import ExecutionMode, ExternalTrainingStatus
from app.domain.datasets.training_errors import TrainingAdapterUnavailableError
from app.domain.modelops.errors import ModelAdapterUnavailableError
from app.infrastructure.model_platform_http import HttpModelPlatformGateway
from app.infrastructure.training_adapter_http import HttpTrainingAdapterGateway
from pydantic import ValidationError

# 训练 / 模型 endpoint 都用公网 IP 字面量：两个网关的出站 SSRF 守卫都会解析
# 主机名并拒绝环回/私网地址（见 test_adapter_url_guard.py），而 MockTransport
# 不联网，因此这里不能再用无法解析的假域名。IP 字面量由 getaddrinfo 本地
# 返回，无 DNS；模型网关自 N8-008 起同样受守卫约束。
_TRAINING_ENDPOINT = "http://93.184.216.34:9100"
_MODEL_ENDPOINT = "http://93.184.216.34:9101"


def _settings(**overrides: object) -> Settings:
    """测试用的最小配置；``_env_file=None`` 避免读到开发机上的 .env。"""
    values: dict[str, Any] = {
        "env": "local",
        "training_adapter_capabilities": {"mock": ["submit", "status", "cancel"]},
        "model_adapter_capabilities": {"mock": ["evaluate", "inference"]},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _training_gateway(
    handler: Callable[[httpx.Request], httpx.Response],
) -> HttpTrainingAdapterGateway:
    return HttpTrainingAdapterGateway(
        _settings(training_adapter_endpoints={"mock": _TRAINING_ENDPOINT}),
        client=_client(handler),
    )


def _model_gateway(handler: Callable[[httpx.Request], httpx.Response]) -> HttpModelPlatformGateway:
    return HttpModelPlatformGateway(
        _settings(model_adapter_endpoints={"mock": _MODEL_ENDPOINT}),
        client=_client(handler),
    )


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


# ---------------------------------------------------------------- 训练：成功路径


def test_submit_posts_training_run_and_maps_reference() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "external_run_id": "run-42",
                "external_run_url": "https://ml.example.com/runs/42",
                "model_ref": {"model_id": "m-1"},
                "evaluation_ref": {"evaluation_id": "e-1"},
            },
        )

    delivery_id, dataset_version_id = uuid4(), uuid4()
    gateway = _training_gateway(handler)
    ref = gateway.submit(
        adapter_id="mock",
        adapter_version="1.2.0",
        correlation_id="corr-1",
        delivery_id=delivery_id,
        dataset_version_id=dataset_version_id,
        config={"epochs": 3},
    )

    assert ref.external_run_id == "run-42"
    assert ref.external_run_url == "https://ml.example.com/runs/42"
    assert ref.model_ref == {"model_id": "m-1"}
    assert ref.evaluation_ref == {"evaluation_id": "e-1"}
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{_TRAINING_ENDPOINT}/training-runs"
    assert _body(request) == {
        "adapter_id": "mock",
        "adapter_version": "1.2.0",
        "correlation_id": "corr-1",
        "training_delivery_id": str(delivery_id),
        "dataset_version_id": str(dataset_version_id),
        "config": {"epochs": 3},
    }


def test_submit_without_optional_refs_leaves_them_none() -> None:
    gateway = _training_gateway(
        lambda request: httpx.Response(201, json={"external_run_id": "run-1"})
    )

    ref = gateway.submit(
        adapter_id="mock",
        adapter_version="1.0.0",
        correlation_id="corr-1",
        delivery_id=uuid4(),
        dataset_version_id=uuid4(),
        config={},
    )

    assert ref.external_run_id == "run-1"
    assert ref.external_run_url is None
    assert ref.model_ref is None
    assert ref.evaluation_ref is None


def test_get_status_calls_run_endpoint_and_parses_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "RUNNING",
                "status_stale": True,
                "external_run_url": "https://ml.example.com/runs/42",
                "model_ref": {"model_id": "m-1"},
                "error_code": None,
                "error_summary": None,
            },
        )

    gateway = _training_gateway(handler)
    status = gateway.get_status(
        adapter_id="mock",
        external_run_id="run/42",  # 含斜杠：必须编码，不能拼接成多级路径
        correlation_id="corr-1",
    )

    assert status.status is ExternalTrainingStatus.RUNNING
    assert status.status_stale is True
    assert status.external_run_url == "https://ml.example.com/runs/42"
    assert status.model_ref == {"model_id": "m-1"}
    assert status.error_code is None
    assert seen[0].method == "GET"
    assert str(seen[0].url) == f"{_TRAINING_ENDPOINT}/training-runs/run%2F42"


def test_cancel_posts_cancel_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "CANCELLED"})

    gateway = _training_gateway(handler)
    status = gateway.cancel(adapter_id="mock", external_run_id="run-7", correlation_id="corr-9")

    assert status.status is ExternalTrainingStatus.CANCELLED
    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{_TRAINING_ENDPOINT}/training-runs/run-7/cancel"
    assert _body(seen[0]) == {"correlation_id": "corr-9"}


def test_capabilities_are_preserved_from_config() -> None:
    gateway = _training_gateway(lambda request: httpx.Response(200, json={}))

    assert gateway.capabilities("mock") == frozenset({"submit", "status", "cancel"})
    assert gateway.capabilities("unknown") == frozenset()


# ---------------------------------------------------------------- 训练：失败路径


def test_timeout_maps_to_training_adapter_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("慢", request=request)

    gateway = _training_gateway(handler)
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert "超时" in str(excinfo.value)


def test_connect_error_maps_to_training_adapter_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("拒绝连接", request=request)

    gateway = _training_gateway(handler)
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.submit(
            adapter_id="mock",
            adapter_version="1.0.0",
            correlation_id="corr-1",
            delivery_id=uuid4(),
            dataset_version_id=uuid4(),
            config={},
        )

    assert "连接失败" in str(excinfo.value)


def test_invalid_endpoint_url_maps_to_unavailable_not_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("非法地址不得进入传输层")

    gateway = HttpTrainingAdapterGateway(
        # 绕过启动自检直接构造配置：网关自己也要兜住，不能变成 500。
        _settings(training_adapter_endpoints={"mock": "http://"}),
        client=_client(handler),
    )
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert "地址非法" in str(excinfo.value)


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_server_error_maps_to_training_adapter_unavailable(status_code: int) -> None:
    gateway = _training_gateway(
        lambda request: httpx.Response(status_code, text="upstream exploded")
    )
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert f"HTTP {status_code}" in str(excinfo.value)


def test_client_error_also_maps_to_unavailable_not_fake_success() -> None:
    gateway = _training_gateway(lambda request: httpx.Response(422, json={"detail": "bad"}))
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.cancel(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert "HTTP 422" in str(excinfo.value)


def test_missing_endpoint_keeps_503_behaviour() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("未配置 endpoint 的 adapter 不得发出 HTTP 请求")

    gateway = HttpTrainingAdapterGateway(_settings(), client=_client(handler))

    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")
    assert "未配置真实平台客户端" in str(excinfo.value)

    with pytest.raises(TrainingAdapterUnavailableError):
        gateway.cancel(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    with pytest.raises(TrainingAdapterUnavailableError):
        gateway.submit(
            adapter_id="mock",
            adapter_version="1.0.0",
            correlation_id="corr-1",
            delivery_id=uuid4(),
            dataset_version_id=uuid4(),
            config={},
        )


def test_unknown_status_is_rejected_instead_of_treated_as_success() -> None:
    gateway = _training_gateway(lambda request: httpx.Response(200, json={"status": "MAGIC"}))
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert "响应状态非法" in str(excinfo.value)


def test_submit_without_external_run_id_is_rejected() -> None:
    gateway = _training_gateway(lambda request: httpx.Response(201, json={}))
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.submit(
            adapter_id="mock",
            adapter_version="1.0.0",
            correlation_id="corr-1",
            delivery_id=uuid4(),
            dataset_version_id=uuid4(),
            config={},
        )

    assert "external_run_id" in str(excinfo.value)


def test_non_json_response_is_rejected() -> None:
    gateway = _training_gateway(lambda request: httpx.Response(200, text="<html>ok</html>"))
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert "不是合法 JSON" in str(excinfo.value)


# ---------------------------------------------------------------- 模型平台：成功路径


def test_submit_evaluation_posts_to_evaluations() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)  # 空响应体是合法的受理确认

    evaluation_id, model_version_id, dataset_version_id = uuid4(), uuid4(), uuid4()
    gateway = _model_gateway(handler)
    result = gateway.submit_evaluation(
        adapter_id="mock",
        adapter_version="1.0.0",
        evaluation_id=evaluation_id,
        model_version_id=model_version_id,
        dataset_version_id=dataset_version_id,
        config={"metrics": ["success_rate"]},
    )

    assert result is None
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{_MODEL_ENDPOINT}/evaluations"
    assert _body(request) == {
        "adapter_id": "mock",
        "adapter_version": "1.0.0",
        "evaluation_id": str(evaluation_id),
        "model_version_id": str(model_version_id),
        "dataset_version_id": str(dataset_version_id),
        "config": {"metrics": ["success_rate"]},
    }


def test_submit_inference_posts_to_inference_tasks() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={})

    task_id, model_version_id, service_id = uuid4(), uuid4(), uuid4()
    episodes = [uuid4(), uuid4()]
    gateway = _model_gateway(handler)
    gateway.submit_inference(
        adapter_id="mock",
        adapter_version="1.0.0",
        task_id=task_id,
        model_version_id=model_version_id,
        service_id=service_id,
        dataset_version_id=None,
        episode_ids=episodes,
        config={},
    )

    assert str(seen[0].url) == f"{_MODEL_ENDPOINT}/inference-tasks"
    assert _body(seen[0]) == {
        "adapter_id": "mock",
        "adapter_version": "1.0.0",
        "task_id": str(task_id),
        "model_version_id": str(model_version_id),
        "service_id": str(service_id),
        "dataset_version_id": None,
        "episode_ids": [str(item) for item in episodes],
        "config": {},
    }


# ---------------------------------------------------------------- 模型平台：失败路径


def test_model_timeout_maps_to_model_adapter_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("超时", request=request)

    gateway = _model_gateway(handler)
    with pytest.raises(ModelAdapterUnavailableError) as excinfo:
        gateway.submit_evaluation(
            adapter_id="mock",
            adapter_version="1.0.0",
            evaluation_id=uuid4(),
            model_version_id=uuid4(),
            dataset_version_id=uuid4(),
            config={},
        )

    assert "超时" in str(excinfo.value)


def test_model_5xx_maps_to_model_adapter_unavailable() -> None:
    gateway = _model_gateway(lambda request: httpx.Response(503, text="busy"))
    with pytest.raises(ModelAdapterUnavailableError) as excinfo:
        gateway.submit_inference(
            adapter_id="mock",
            adapter_version="1.0.0",
            task_id=uuid4(),
            model_version_id=uuid4(),
            service_id=None,
            dataset_version_id=None,
            episode_ids=[],
            config={},
        )

    assert "HTTP 503" in str(excinfo.value)


def test_model_missing_endpoint_keeps_503_behaviour() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("未配置 endpoint 的 adapter 不得发出 HTTP 请求")

    gateway = HttpModelPlatformGateway(_settings(), client=_client(handler))

    with pytest.raises(ModelAdapterUnavailableError) as excinfo:
        gateway.submit_evaluation(
            adapter_id="mock",
            adapter_version="1.0.0",
            evaluation_id=uuid4(),
            model_version_id=uuid4(),
            dataset_version_id=uuid4(),
            config={},
        )
    assert "未配置真实评测客户端" in str(excinfo.value)

    with pytest.raises(ModelAdapterUnavailableError):
        gateway.submit_inference(
            adapter_id="mock",
            adapter_version="1.0.0",
            task_id=uuid4(),
            model_version_id=uuid4(),
            service_id=None,
            dataset_version_id=None,
            episode_ids=[],
            config={},
        )


# ---------------------------------------------------------------- 配置与装配


def test_endpoints_accept_json_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DDP_TRAINING_ADAPTER_ENDPOINTS", json.dumps({"mock": "http://127.0.0.1:9100"})
    )
    monkeypatch.setenv("DDP_MODEL_ADAPTER_ENDPOINTS", "mock=http://127.0.0.1:9101")

    settings = Settings(_env_file=None)

    assert settings.training_adapter_endpoints == {"mock": "http://127.0.0.1:9100"}
    assert settings.model_adapter_endpoints == {"mock": "http://127.0.0.1:9101"}
    assert settings.training_adapter_timeout_seconds == 10.0
    assert settings.model_adapter_timeout_seconds == 10.0


def test_empty_endpoints_env_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """compose 的 ``${VAR:-}`` 会把变量置成空串，不能因此让启动自检失败。"""
    monkeypatch.setenv("DDP_TRAINING_ADAPTER_ENDPOINTS", "")
    monkeypatch.setenv("DDP_MODEL_ADAPTER_ENDPOINTS", "   ")

    settings = Settings(_env_file=None)

    assert settings.training_adapter_endpoints == {}
    assert settings.model_adapter_endpoints == {}


def test_invalid_endpoint_url_is_rejected_at_startup() -> None:
    settings = _settings(training_adapter_endpoints={"mock": "adapter:9100"})

    with pytest.raises(RuntimeError) as excinfo:
        settings.validate_runtime_security()

    assert "DDP_TRAINING_ADAPTER_ENDPOINTS" in str(excinfo.value)


def test_malformed_comma_separated_endpoints_are_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(model_adapter_endpoints="mock")  # 缺 `=url`

    assert "adapter endpoint 片段非法" in str(excinfo.value)


def test_dependencies_wire_the_http_gateways() -> None:
    settings = _settings()

    assert isinstance(get_training_adapter_gateway(settings), HttpTrainingAdapterGateway)
    assert isinstance(get_model_gateway(settings), HttpModelPlatformGateway)


def test_training_execution_modes_accept_json_and_normalize_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DDP_TRAINING_ADAPTER_EXECUTION_MODES",
        json.dumps({"local-training": "local", "mock-training": "MOCK"}),
    )
    settings = Settings(_env_file=None)
    assert settings.training_adapter_execution_modes == {
        "local-training": "LOCAL",
        "mock-training": "MOCK",
    }
    gateway = get_training_adapter_gateway(settings)
    assert gateway.execution_mode("local-training") is ExecutionMode.LOCAL
    # 未声明的 Adapter 返回 None，由用例要求请求显式声明，而不是默认 REMOTE。
    assert gateway.execution_mode("unknown-adapter") is None


def test_unknown_training_execution_mode_is_rejected_at_startup() -> None:
    settings = _settings(training_adapter_execution_modes={"mock-training": "UNKNOWN"})

    with pytest.raises(RuntimeError) as excinfo:
        settings.validate_runtime_security()

    assert "执行方式非法" in str(excinfo.value)
