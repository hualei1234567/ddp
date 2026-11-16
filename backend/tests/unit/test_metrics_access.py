"""``/metrics`` 访问控制策略单元测试（纯函数，无 HTTP）。

重点覆盖三件容易写错的事：

1. **判定顺序**：来源被拒先于令牌判定，因此来源不合法时无论令牌对错都得到
   同一个结果，不给远端"令牌是否正确"的探测信号；
2. **恒定时间比较的失败面**：未配置期望值、未带令牌、长度不同都要返回
   ``False``，且不能抛异常（抛异常会让 500 泄露"配置存在但比较崩了"）；
3. **网段解析按严格口径**：``10.1.2.3/8`` 这类带主机位的写法直接拒绝，
   而不是静默放宽到 ``10.0.0.0/8``。
"""

from __future__ import annotations

import pytest
from app.core.metrics_access import (
    MetricsAccessOutcome,
    build_metrics_access_policy,
    decide_metrics_access,
    parse_source_networks,
    source_allowed,
    token_matches,
)

_TOKEN = "unit-test-placeholder-token-0123456789"  # noqa: S105 - 测试占位值


def _policy(*, token: str = _TOKEN, networks: tuple[str, ...] = (), is_local: bool = False):
    return build_metrics_access_policy(
        token=token,
        allowed_source_networks=networks,
        is_local=is_local,
    )


def test_token_matches_accepts_identical_value() -> None:
    assert token_matches(_TOKEN, _TOKEN) is True


def test_token_matches_rejects_missing_or_empty_inputs() -> None:
    # 未带令牌、期望值未配置：都不能被判为匹配（否则等于放行匿名）。
    assert token_matches(None, _TOKEN) is False
    assert token_matches("", _TOKEN) is False
    assert token_matches(_TOKEN, "") is False
    assert token_matches("", "") is False


def test_token_matches_rejects_prefix_and_case_variants() -> None:
    """不做前缀匹配、不做大小写折叠——两者都会扩大可命中的令牌空间。"""
    assert token_matches(_TOKEN[:-1], _TOKEN) is False
    assert token_matches(_TOKEN.upper(), _TOKEN) is False
    assert token_matches(f"{_TOKEN}x", _TOKEN) is False


def test_token_matches_handles_non_ascii_without_raising() -> None:
    """非 ASCII 输入不得让比较抛异常（否则变成 500，反而暴露"配置存在"）。"""
    assert token_matches("令牌", _TOKEN) is False
    assert token_matches(_TOKEN, "令牌") is False


@pytest.mark.parametrize(
    "value",
    ["10.0.0.1/8", " 10.0.0.0/8", "10.0.0.0/8 ", "", "not-a-network", "10.0.0.0/33"],
)
def test_parse_source_networks_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_source_networks([value])


def test_parse_source_networks_accepts_single_address_as_host_network() -> None:
    networks = parse_source_networks(["10.1.2.3", "::1"])

    assert {network.prefixlen for network in networks} == {32, 128}
    assert source_allowed("10.1.2.3", networks) is True
    assert source_allowed("10.1.2.4", networks) is False
    assert source_allowed("::1", networks) is True


def test_source_allowed_is_unrestricted_when_no_networks() -> None:
    assert source_allowed(None, ()) is True
    assert source_allowed("anything", ()) is True


def test_source_allowed_fails_closed_on_unparseable_host_when_configured() -> None:
    """配置了白名单就要求来源可判定：``testclient``、空值、非法值一律拒绝。"""
    networks = parse_source_networks(["10.0.0.0/8"])

    assert source_allowed("10.1.2.3", networks) is True
    assert source_allowed(None, networks) is False
    assert source_allowed("testclient", networks) is False
    assert source_allowed("10.0.0.0.0.1", networks) is False


def test_source_networks_are_version_scoped() -> None:
    """IPv4 白名单不得匹配 IPv6 地址（防止 ``::ffff:10.x`` 之类绕过）。"""
    networks = parse_source_networks(["10.0.0.0/8"])

    assert source_allowed("::ffff:10.1.2.3", networks) is False


def test_decide_allows_correct_token_from_any_source_without_whitelist() -> None:
    outcome = decide_metrics_access(
        _policy(),
        presented_token=_TOKEN,
        client_host="203.0.113.9",
    )

    assert outcome is MetricsAccessOutcome.ALLOWED


def test_decide_rejects_missing_and_wrong_token_when_configured() -> None:
    policy = _policy()

    assert (
        decide_metrics_access(policy, presented_token=None, client_host="203.0.113.9")
        is MetricsAccessOutcome.UNAUTHENTICATED
    )
    assert (
        decide_metrics_access(policy, presented_token="wrong", client_host="203.0.113.9")
        is MetricsAccessOutcome.UNAUTHENTICATED
    )


def test_decide_checks_source_before_token() -> None:
    """来源不合法时，令牌对错都返回 ``SOURCE_NOT_ALLOWED``（不可区分）。"""
    policy = _policy(networks=("10.0.0.0/8",))

    with_correct = decide_metrics_access(
        policy,
        presented_token=_TOKEN,
        client_host="203.0.113.9",
    )
    with_wrong = decide_metrics_access(
        policy,
        presented_token="wrong",
        client_host="203.0.113.9",
    )

    assert with_correct is MetricsAccessOutcome.SOURCE_NOT_ALLOWED
    assert with_correct is with_wrong


def test_decide_does_not_exempt_local_from_token_check() -> None:
    """配置了令牌后，local 环境同样要校验——不存在"本地免检"的旁路。"""
    policy = _policy(is_local=True)

    assert (
        decide_metrics_access(policy, presented_token=None, client_host="127.0.0.1")
        is MetricsAccessOutcome.UNAUTHENTICATED
    )


def test_decide_allows_anonymous_only_when_unconfigured_and_local() -> None:
    outcome = decide_metrics_access(
        _policy(token="", is_local=True),
        presented_token=None,
        client_host="127.0.0.1",
    )

    assert outcome is MetricsAccessOutcome.ALLOWED


def test_decide_rejects_anonymous_in_non_local_environment() -> None:
    outcome = decide_metrics_access(
        _policy(token="", is_local=False),
        presented_token=None,
        client_host="10.1.2.3",
    )

    assert outcome is MetricsAccessOutcome.NOT_CONFIGURED


def test_decide_rejects_non_local_anonymous_even_with_whitelisted_source() -> None:
    """来源可信不等于免认证：未配置令牌时非本地环境仍然拒绝。"""
    outcome = decide_metrics_access(
        _policy(token="", networks=("10.0.0.0/8",), is_local=False),
        presented_token=None,
        client_host="10.1.2.3",
    )

    assert outcome is MetricsAccessOutcome.NOT_CONFIGURED


def test_policy_with_token_reports_required() -> None:
    assert _policy().requires_token is True
    assert _policy(token="").requires_token is False
