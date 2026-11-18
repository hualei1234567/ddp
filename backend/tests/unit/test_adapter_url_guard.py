"""训练 / 模型 Adapter 出站 URL 的 SSRF 守卫单元测试。

不联网：``socket.getaddrinfo`` 由 monkeypatch 替换，HTTP 传输用
``httpx.MockTransport``。覆盖验收要求的全部拒绝/放行形态，以及两个网关层
"守卫失败 → *AdapterUnavailableError（503）"的映射、开关独立性与重定向不跟随。
N8-008 起模型平台网关接线复用同一守卫，规则不在网关里重复实现。
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.domain.datasets.training_errors import TrainingAdapterUnavailableError
from app.domain.modelops.errors import ModelAdapterUnavailableError
from app.infrastructure.adapter_http import AdapterHttpFailure
from app.infrastructure.adapter_url_guard import ensure_safe_adapter_url
from app.infrastructure.model_platform_http import HttpModelPlatformGateway
from app.infrastructure.training_adapter_http import HttpTrainingAdapterGateway

_PUBLIC_IP = "93.184.216.34"
_CONTEXT = "训练 Adapter mock 提交训练"


def _fake_dns(monkeypatch: pytest.MonkeyPatch, records: Mapping[str, list[str]]) -> None:
    """把主机名映射到固定地址；字面量 IP 仍走真实 getaddrinfo（不联网）。"""
    original = socket.getaddrinfo

    def fake_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        addresses = records.get(host)
        if addresses is None:
            return original(host, port, *args, **kwargs)
        infos = []
        for value in addresses:
            if ":" in value:
                infos.append(
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (value, port, 0, 0),
                    )
                )
            else:
                infos.append(
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (value, port),
                    )
                )
        return infos

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _settings(**overrides: object) -> Settings:
    """测试用的最小配置；``_env_file=None`` 避免读到开发机上的 .env。"""
    values: dict[str, Any] = {"env": "local"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _client(handler: Any, *, follow_redirects: bool = False) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=follow_redirects,
    )


# ------------------------------------------------------------------ 拒绝：地址


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1:9100/training-runs", "环回地址"),
        ("https://127.0.0.1/training-runs", "环回地址"),
        ("http://10.1.2.3/training-runs", "私有地址"),
        ("http://172.16.0.1/training-runs", "私有地址"),
        ("http://172.31.255.254/training-runs", "私有地址"),
        ("http://192.168.1.10/training-runs", "私有地址"),
        ("http://169.254.169.254/latest/meta-data/", "链路本地地址"),
        ("http://[::1]:9100/training-runs", "环回地址"),
        ("http://[fe80::1]/training-runs", "链路本地地址"),
        ("http://0.0.0.0:9100/training-runs", "未指定地址"),
        ("http://[::]/training-runs", "未指定地址"),
        ("http://224.0.0.1/training-runs", "组播地址"),
        ("http://[ff02::1]/training-runs", "组播地址"),
        ("http://[::ffff:127.0.0.1]/training-runs", "环回地址"),
        ("http://[::ffff:10.0.0.1]/training-runs", "私有地址"),
        ("http://[::7f00:1]/training-runs", "IPv4 兼容地址"),
        ("http://[2002:7f00:1::]/training-runs", "私有地址"),
        ("http://100.64.0.1/training-runs", "非公网地址"),
    ],
)
def test_non_public_addresses_are_rejected(url: str, reason: str) -> None:
    with pytest.raises(AdapterHttpFailure) as excinfo:
        ensure_safe_adapter_url(url, context=_CONTEXT)

    assert reason in str(excinfo.value)
    assert "已拒绝" in str(excinfo.value)


def test_hostname_resolving_to_any_private_address_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任一 A/AAAA 记录落在禁止范围即拒绝（即使同时存在公网地址）。"""
    _fake_dns(monkeypatch, {"adapter.example": [_PUBLIC_IP, "10.0.0.7"]})

    with pytest.raises(AdapterHttpFailure) as excinfo:
        ensure_safe_adapter_url("http://adapter.example:9100", context=_CONTEXT)

    assert "10.0.0.7" in str(excinfo.value)


def test_unresolvable_hostname_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """解析失败必须 fail-closed，不能"解析不了就放行"。"""

    def fake_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(AdapterHttpFailure) as excinfo:
        ensure_safe_adapter_url("http://unresolvable.example:9100", context=_CONTEXT)

    assert "无法解析" in str(excinfo.value)


# ------------------------------------------------------------------ 拒绝：URL 形态


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com/training-runs",
        "http://user@example.com/training-runs",
        "ftp://example.com/training-runs",
        "file:///etc/passwd",
        "gopher://example.com/training-runs",
        "http://",
        "http://example.com:not-a-port/training-runs",
    ],
)
def test_unsafe_url_shapes_are_rejected(url: str) -> None:
    with pytest.raises(AdapterHttpFailure):
        ensure_safe_adapter_url(url, context=_CONTEXT)


# ------------------------------------------------------------------ 放行


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/training-runs",
        "https://example.com/training-runs",
        f"http://{_PUBLIC_IP}:9100/training-runs",
    ],
)
def test_public_addresses_are_allowed(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _fake_dns(monkeypatch, {"example.com": [_PUBLIC_IP]})

    assert ensure_safe_adapter_url(url, context=_CONTEXT) is None


def test_allow_private_explicitly_permits_local_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式开关只用于本地开发：打开后 localhost 放行。"""
    _fake_dns(monkeypatch, {"localhost": ["127.0.0.1", "::1"]})

    assert (
        ensure_safe_adapter_url(
            "http://localhost:9100/training-runs",
            context=_CONTEXT,
            allow_private=True,
        )
        is None
    )
    assert (
        ensure_safe_adapter_url(
            "http://192.168.1.10:9100/training-runs",
            context=_CONTEXT,
            allow_private=True,
        )
        is None
    )


def test_allow_private_does_not_disable_url_shape_checks() -> None:
    with pytest.raises(AdapterHttpFailure, match="不得内嵌凭据"):
        ensure_safe_adapter_url(
            "http://user:pass@localhost:9100/training-runs",
            context=_CONTEXT,
            allow_private=True,
        )


# ------------------------------------------------------------------ 配置默认值


def test_allow_private_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DDP_TRAINING_ADAPTER_ALLOW_PRIVATE", raising=False)

    assert Settings(_env_file=None).training_adapter_allow_private is False


def test_allow_private_can_be_enabled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DDP_TRAINING_ADAPTER_ALLOW_PRIVATE", "true")

    assert Settings(_env_file=None).training_adapter_allow_private is True


# ------------------------------------------------------------------ 网关接线


def test_gateway_rejects_loopback_endpoint_by_default() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        calls.append(request)
        raise AssertionError("环回地址不得进入传输层")

    gateway = HttpTrainingAdapterGateway(
        _settings(training_adapter_endpoints={"mock": "http://127.0.0.1:9100"}),
        client=_client(handler),
    )
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="corr-1")

    assert "环回地址" in str(excinfo.value)
    assert calls == []


def test_gateway_rejects_metadata_endpoint_on_submit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("云元数据地址不得进入传输层")

    gateway = HttpTrainingAdapterGateway(
        _settings(training_adapter_endpoints={"mock": "http://169.254.169.254"}),
        client=_client(handler),
    )
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.submit(
            adapter_id="mock",
            adapter_version="1.0.0",
            correlation_id="corr-1",
            delivery_id=uuid4(),
            dataset_version_id=uuid4(),
            config={},
        )

    assert "链路本地地址" in str(excinfo.value)


def test_gateway_allows_local_endpoint_when_switch_is_on() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "RUNNING"})

    gateway = HttpTrainingAdapterGateway(
        _settings(
            training_adapter_endpoints={"mock": "http://127.0.0.1:9100"},
            training_adapter_allow_private=True,
        ),
        client=_client(handler),
    )
    status = gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="c")

    assert status.status.value == "RUNNING"
    assert str(seen[0].url) == "http://127.0.0.1:9100/training-runs/run-1"


def test_gateway_does_not_follow_redirects() -> None:
    """3xx 不得被跟随：重定向目标会绕过 endpoint 的 SSRF 校验。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        )

    gateway = HttpTrainingAdapterGateway(
        _settings(training_adapter_endpoints={"mock": f"http://{_PUBLIC_IP}:9100"}),
        # 即使调用方注入的 client 默认跟随重定向，网关也必须显式关闭。
        client=_client(handler, follow_redirects=True),
    )
    with pytest.raises(TrainingAdapterUnavailableError) as excinfo:
        gateway.get_status(adapter_id="mock", external_run_id="run-1", correlation_id="c")

    assert "HTTP 302" in str(excinfo.value)
    assert len(seen) == 1


# ------------------------------------------------------------------ 模型平台网关接线


def _model_gateway(
    endpoint: str,
    *,
    client: httpx.Client,
    **overrides: object,
) -> HttpModelPlatformGateway:
    return HttpModelPlatformGateway(
        _settings(model_adapter_endpoints={"mock": endpoint}, **overrides),
        client=client,
    )


def _reject_at_transport(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    """传输层兜底：不安全的 endpoint 根本不应进入 httpx。"""
    raise AssertionError(f"不安全的模型 Adapter 地址不得进入传输层：{request.url}")


def _submit_evaluation(gateway: HttpModelPlatformGateway) -> None:
    gateway.submit_evaluation(
        adapter_id="mock",
        adapter_version="1.0.0",
        evaluation_id=uuid4(),
        model_version_id=uuid4(),
        dataset_version_id=uuid4(),
        config={},
    )


@pytest.mark.parametrize(
    ("endpoint", "reason"),
    [
        ("http://127.0.0.1:9101", "环回地址"),
        ("http://[::1]:9101", "环回地址"),
        ("http://[::ffff:127.0.0.1]:9101", "环回地址"),
        ("http://10.1.2.3:9101", "私有地址"),
        ("http://172.16.0.1:9101", "私有地址"),
        ("http://192.168.1.10:9101", "私有地址"),
        ("http://169.254.169.254", "链路本地地址"),
        ("http://0.0.0.0:9101", "未指定地址"),
        ("http://100.64.0.1:9101", "非公网地址"),
        ("http://user:pass@93.184.216.34:9101", "不得内嵌凭据"),
        ("ftp://93.184.216.34:9101", "协议不被允许"),
    ],
)
def test_model_gateway_rejects_unsafe_endpoints(endpoint: str, reason: str) -> None:
    """私网/环回/元数据/凭据/非 http(s) 在模型网关出站前一律拒绝。"""
    gateway = _model_gateway(endpoint, client=_client(_reject_at_transport))

    with pytest.raises(ModelAdapterUnavailableError) as excinfo:
        _submit_evaluation(gateway)

    assert reason in str(excinfo.value)
    # 复用训练网关的中文前缀口径，排障时能直接看出是哪个 adapter、哪个动作。
    assert "模型 Adapter mock 提交评测" in str(excinfo.value)


def test_model_gateway_rejects_hostname_resolving_to_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_dns(monkeypatch, {"model-adapter.internal": [_PUBLIC_IP, "10.0.0.7"]})

    gateway = _model_gateway(
        "http://model-adapter.internal:9101",
        client=_client(_reject_at_transport),
    )
    with pytest.raises(ModelAdapterUnavailableError) as excinfo:
        _submit_evaluation(gateway)

    assert "10.0.0.7" in str(excinfo.value)


def test_model_gateway_allows_public_endpoint() -> None:
    """公网 endpoint 不被误杀：评测与推理都照常发出，路径不变。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    gateway = _model_gateway(f"http://{_PUBLIC_IP}:9101", client=_client(handler))
    _submit_evaluation(gateway)
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

    assert [str(request.url) for request in seen] == [
        f"http://{_PUBLIC_IP}:9101/evaluations",
        f"http://{_PUBLIC_IP}:9101/inference-tasks",
    ]


def test_model_gateway_allows_local_endpoint_when_model_switch_is_on() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    gateway = _model_gateway(
        "http://127.0.0.1:9101",
        client=_client(handler),
        model_adapter_allow_private=True,
    )
    _submit_evaluation(gateway)

    assert [str(request.url) for request in seen] == ["http://127.0.0.1:9101/evaluations"]


def test_training_switch_does_not_open_the_model_gateway() -> None:
    """两个出站面的开关相互独立：放开训练不得连带放开模型。"""
    gateway = _model_gateway(
        "http://127.0.0.1:9101",
        client=_client(_reject_at_transport),
        training_adapter_allow_private=True,
    )

    with pytest.raises(ModelAdapterUnavailableError) as excinfo:
        _submit_evaluation(gateway)

    assert "环回地址" in str(excinfo.value)


def test_model_allow_private_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DDP_MODEL_ADAPTER_ALLOW_PRIVATE", raising=False)

    assert Settings(_env_file=None).model_adapter_allow_private is False


def test_model_allow_private_can_be_enabled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DDP_MODEL_ADAPTER_ALLOW_PRIVATE", "true")

    assert Settings(_env_file=None).model_adapter_allow_private is True
