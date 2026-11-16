"""``/metrics`` 访问控制测试（HTTP 适配层）。

抓取端是机器、无法走用户 JWT，因此用静态 Bearer 令牌 + 可选来源网段白名单。
判定实现（恒定时间比较、判定顺序、失败关闭口径）的单测在
``tests/unit/test_metrics_access.py``；这里只验证 HTTP 层——状态码、错误体、
令牌零泄露，以及"配置了就必须校验"这条最危险的失败模式。

从 ``test_metrics_api.py`` 拆出：原文件叠加访问控制用例会超过 500 行评审线
（CLAUDE.md §五），而两者关注点不同（采集正确性 vs 访问控制）。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from app.core.config import Settings, get_settings
from app.core.metrics import (
    EPISODES_GAUGE,
    EXEMPT_PATHS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)
from app.core.metrics_access import (
    MetricsAccessOutcome,
    build_metrics_access_policy,
    decide_metrics_access,
)
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from tests.api.conftest import ApiIds
from tests.api.metrics_support import CollectingHandler

# ---------------------------------------------------------------------------
# /metrics 访问控制
#
# 抓取端是机器：不能走用户 JWT，改用静态 Bearer 令牌 + 可选来源网段白名单。
# 判定实现见 ``app/core/metrics_access.py``；这里验证 HTTP 适配层——
# 状态码、错误体、令牌零泄露，以及"配置了就必须校验"这条最危险的失败模式。
# ---------------------------------------------------------------------------

_METRICS_TOKEN = "metrics-api-test-token-0123456789abcdef"  # noqa: S105 - 测试固定令牌
_WRONG_TOKEN = "metrics-api-test-token-fedcba9876543210"  # noqa: S105 - 测试固定令牌


def _metrics_settings(**overrides: object) -> Settings:
    """构造 /metrics 鉴权相关配置；其余字段用默认值即可（不触发启动自检）。"""
    values: dict[str, object] = {
        "env": "local",
        "metrics_access_token": _METRICS_TOKEN,
    }
    values.update(overrides)
    return Settings(**values)


def _with_settings(client: TestClient, settings: Settings) -> TestClient:
    """覆盖 ``SettingsDep`` 依赖；不动 ``create_app`` 的启动自检（那是另一层）。"""
    client.app.dependency_overrides[get_settings] = lambda: settings
    return client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _async_get(
    app: object,
    url: str,
    *,
    client_host: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """用指定对端地址发一次请求（TestClient 固定为 ``testclient``，无法覆盖）。"""

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            return await async_client.get(url, headers=headers or {})

    return asyncio.run(run())


def test_metrics_requires_token_when_configured(
    metrics_client: TestClient,
) -> None:
    """配置了令牌后，不带 Authorization 必须 401。"""
    _with_settings(metrics_client, _metrics_settings())

    response = metrics_client.get("/metrics")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "not_authenticated"
    assert body["error"]["request_id"] == response.headers["X-Request-Id"]
    # 拒绝时不得吐任何指标内容。
    assert HTTP_REQUESTS_TOTAL not in response.text


def test_metrics_rejects_wrong_token_and_never_leaks_expected_value(
    metrics_client: TestClient,
) -> None:
    """错误令牌 401，且响应体不得泄露正确令牌的任何片段。"""
    _with_settings(metrics_client, _metrics_settings())

    wrong = metrics_client.get("/metrics", headers=_bearer(_WRONG_TOKEN))
    missing = metrics_client.get("/metrics")

    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "not_authenticated"
    # 不泄露期望值：整串、前 8 位、后 8 位都不得出现。
    for fragment in (_METRICS_TOKEN, _METRICS_TOKEN[:8], _METRICS_TOKEN[-8:]):
        assert fragment not in wrong.text
    # "没带" 与 "带错" 对外必须不可区分（否则可据此判断令牌是否接近正确）。
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]
    assert wrong.json()["error"]["code"] == missing.json()["error"]["code"]


def test_metrics_rejects_non_bearer_authorization_scheme(
    metrics_client: TestClient,
) -> None:
    """声明了 Authorization 但不是 Bearer：直接拒绝，不回退到匿名。"""
    _with_settings(metrics_client, _metrics_settings())

    response = metrics_client.get(
        "/metrics",
        headers={"Authorization": f"Basic {_METRICS_TOKEN}"},
    )

    assert response.status_code == 401
    assert _METRICS_TOKEN not in response.text


def test_metrics_accepts_correct_token_with_prometheus_text(
    metrics_client: TestClient,
) -> None:
    """正确令牌 → 200，且响应体是合法 Prometheus 文本格式。"""
    _with_settings(metrics_client, _metrics_settings())

    response = metrics_client.get("/metrics", headers=_bearer(_METRICS_TOKEN))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # 用官方解析器验证格式合法性，而不是只做字符串包含：解析失败即格式非法。
    families = {family.name for family in text_string_to_metric_families(response.text)}
    # 解析器按 Prometheus 约定把 counter 的 ``_total`` 后缀归一化掉，
    # 因此族名是 ``ddp_http_requests``，样本名仍是 ``ddp_http_requests_total``。
    assert "ddp_http_requests" in families
    assert HTTP_REQUEST_DURATION_SECONDS in families
    assert EPISODES_GAUGE in families
    assert f"# TYPE {HTTP_REQUESTS_TOTAL} counter" in response.text
    assert _METRICS_TOKEN not in response.text


def test_metrics_token_is_required_even_in_local_environment(
    metrics_client: TestClient,
) -> None:
    """令牌一旦配置，local 环境也没有免检旁路。"""
    _with_settings(metrics_client, _metrics_settings(env="local"))

    assert metrics_client.get("/metrics").status_code == 401
    assert metrics_client.get("/metrics", headers=_bearer(_METRICS_TOKEN)).status_code == 200


def test_metrics_unconfigured_non_local_is_rejected_with_503(
    metrics_client: TestClient,
) -> None:
    """非本地环境未配置令牌：请求级 503（配置缺失），而不是放行或启动失败。"""
    _with_settings(metrics_client, _metrics_settings(env="staging", metrics_access_token=""))

    response = metrics_client.get("/metrics")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "metrics_not_configured"
    assert HTTP_REQUESTS_TOTAL not in response.text


def test_metrics_unconfigured_local_allows_anonymous_scraping(
    metrics_client: TestClient,
) -> None:
    """local 环境未配置令牌：放行，便于本地开发与测试（与 local_header_auth 同口径）。"""
    _with_settings(metrics_client, _metrics_settings(env="local", metrics_access_token=""))

    response = metrics_client.get("/metrics")

    assert response.status_code == 200
    assert HTTP_REQUESTS_TOTAL in response.text


def test_health_stays_public_even_when_metrics_requires_token(
    metrics_client: TestClient,
) -> None:
    """``/health`` 不参与 /metrics 的认证：容器探活与负载均衡依赖它无条件可达。"""
    _with_settings(metrics_client, _metrics_settings(env="staging"))

    assert metrics_client.get("/health").status_code == 200
    assert metrics_client.get("/metrics").status_code == 401


def test_metrics_rejects_wrong_source_even_with_correct_token(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """来源网段白名单生效，且来源被拒与令牌错误对外不可区分。"""
    client, _ = metrics_app
    _with_settings(
        client,
        _metrics_settings(
            env="staging",
            metrics_allowed_source_networks=["10.0.0.0/8"],
        ),
    )

    blocked = _async_get(
        client.app,
        "/metrics",
        client_host="203.0.113.9",
        headers=_bearer(_METRICS_TOKEN),
    )
    wrong_token = _async_get(
        client.app,
        "/metrics",
        client_host="10.1.2.3",
        headers=_bearer(_WRONG_TOKEN),
    )

    assert blocked.status_code == 401
    assert wrong_token.status_code == 401
    # 两条路径的状态码、错误码与文案完全一致：远端无法区分自己"被网段拦了"
    # 还是"令牌错了"。
    assert blocked.json()["error"]["code"] == wrong_token.json()["error"]["code"]
    assert blocked.json()["error"]["message"] == wrong_token.json()["error"]["message"]


def test_metrics_allows_whitelisted_source_with_correct_token(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """白名单内的来源带对令牌可以抓取（白名单不是拒绝一切来源）。"""
    client, _ = metrics_app
    _with_settings(
        client,
        _metrics_settings(
            env="staging",
            metrics_allowed_source_networks=["10.0.0.0/8", "::1/128"],
        ),
    )

    response = _async_get(
        client.app,
        "/metrics",
        client_host="10.1.2.3",
        headers=_bearer(_METRICS_TOKEN),
    )

    assert response.status_code == 200
    assert HTTP_REQUESTS_TOTAL in response.text


def test_metrics_rejects_unparseable_source_when_whitelist_configured(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """ASGI 测试客户端地址 ``testclient`` 无法解析：配置了白名单即判为不允许。"""
    client, _ = metrics_app
    _with_settings(
        client,
        _metrics_settings(env="staging", metrics_allowed_source_networks=["10.0.0.0/8"]),
    )

    response = client.get("/metrics", headers=_bearer(_METRICS_TOKEN))

    assert response.status_code == 401


def test_rejected_metrics_requests_are_not_counted(
    metrics_client: TestClient,
) -> None:
    """被拒的抓取同样不埋点：``EXEMPT_PATHS`` 在认证之前生效，与是否 401 无关。"""
    _with_settings(metrics_client, _metrics_settings())
    client = metrics_client
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers=_bearer(_WRONG_TOKEN)).status_code == 401

    body = client.get("/metrics", headers=_bearer(_METRICS_TOKEN)).text

    assert 'path="/metrics"' not in body
    assert "/metrics" in EXEMPT_PATHS


def test_wrong_token_does_not_echo_token_into_logs(
    metrics_client: TestClient,
) -> None:
    """拒绝日志不得包含客户端提交的令牌（含错误令牌）。"""
    _with_settings(metrics_client, _metrics_settings())
    handler = CollectingHandler()
    target = logging.getLogger("ddp.metrics")
    target.addHandler(handler)
    try:
        assert metrics_client.get("/metrics", headers=_bearer(_WRONG_TOKEN)).status_code == 401
    finally:
        target.removeHandler(handler)

    messages = [payload["message"] for payload in handler.payloads]
    assert any("令牌" in message for message in messages), "拒绝必须留日志"
    for message in messages:
        assert _WRONG_TOKEN not in message
        assert _METRICS_TOKEN not in message


def test_metrics_rejects_token_with_surrounding_whitespace(
    metrics_client: TestClient,
) -> None:
    """带首尾空白的令牌不算匹配：不做规范化，避免扩大可命中的令牌空间。"""
    _with_settings(metrics_client, _metrics_settings())

    policy = build_metrics_access_policy(
        token=_METRICS_TOKEN,
        allowed_source_networks=(),
        is_local=False,
    )

    assert (
        decide_metrics_access(
            policy,
            presented_token=f"  {_METRICS_TOKEN}  ",
            client_host="10.1.2.3",
        )
        is MetricsAccessOutcome.UNAUTHENTICATED
    )
