"""``HuggingFaceRemoteVerifier`` 隔离单测：不联网，注入 fake Adapter。

覆盖任务要求的六类远端故障与安全约束：

- 超时 / 404 / 5xx / 非法 JSON / 超大响应 → 收敛为稳定的
  :class:`RemoteCheckFailureReason`，**不吞异常**；
- 凭据 / URL / 绝对路径 → 经脱敏后才进入异常文本，不外泄；
- 有界重试：仅瞬时错误重试，确定性错误立即失败；
- 字段白名单：只保留规范化字段，远端任意 JSON 不原样落库。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.application.datasources.verification import (
    DATASET_CARD_URL_MAX_LENGTH,
    LICENSE_MAX_LENGTH,
    RemoteCheckError,
    RemoteCheckFailureReason,
)
from app.infrastructure.huggingface.remote_verifier import HuggingFaceRemoteVerifier

_SHA = "0123456789abcdef0123456789abcdef01234567"


@dataclass(frozen=True, slots=True)
class _Ref:
    repo_id: str
    revision: str
    license: str | None
    is_gated: bool
    dataset_card_url: str


def _ref(**overrides: object) -> _Ref:
    base: dict[str, object] = {
        "repo_id": "lerobot/aloha_sim_insertion_human",
        "revision": _SHA,
        "license": "Apache-2.0",
        "is_gated": False,
        "dataset_card_url": "https://huggingface.co/datasets/lerobot/aloha_sim_insertion_human",
    }
    base.update(overrides)
    return _Ref(**base)  # type: ignore[arg-type]


class _FakeAdapter:
    def __init__(self, *, result: object = None, error: BaseException | None = None) -> None:
        self._result = result
        self._error = error

    def describe(self, repo_id: str, *, revision: str | None = None) -> object:
        if self._error is not None:
            raise self._error
        return self._result


class _Factory:
    """按调用次数返回不同 Adapter，用于验证重试次数。"""

    def __init__(self, *adapters: _FakeAdapter) -> None:
        self._adapters = list(adapters)
        self.calls = 0

    def __call__(self, token: str | None, timeout: float) -> _FakeAdapter:
        self.calls += 1
        index = min(self.calls - 1, len(self._adapters) - 1)
        return self._adapters[index]


def _verifier(
    *,
    result: object = None,
    error: BaseException | None = None,
    max_attempts: int = 1,
    sleep_calls: list[float] | None = None,
) -> HuggingFaceRemoteVerifier:
    factory = _Factory(_FakeAdapter(result=result, error=error))
    return HuggingFaceRemoteVerifier(
        adapter_factory=factory,
        max_attempts=max_attempts,
        sleep=(sleep_calls.append if sleep_calls is not None else (lambda _s: None)),
    )


def test_success_returns_whitelisted_result() -> None:
    verifier = _verifier(result=_ref())

    result = verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert result.repo_id == "lerobot/aloha_sim_insertion_human"
    assert result.resolved_revision == _SHA
    assert result.license == "Apache-2.0"
    assert result.is_gated is False


class _Timeout(Exception):
    pass


def test_timeout_is_classified_and_not_swallowed() -> None:
    verifier = _verifier(error=_Timeout("Request timed out after 10s"))

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.TIMEOUT


class _NotFound(Exception):
    pass


def test_404_is_classified_as_not_found() -> None:
    verifier = _verifier(error=_NotFound("404 Client Error: Repository Not Found"))

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/missing", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.NOT_FOUND


class _ServerError(Exception):
    pass


def test_5xx_is_classified_as_server_error() -> None:
    verifier = _verifier(error=_ServerError("503 Service Unavailable"))

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.SERVER_ERROR


def test_invalid_json_is_classified_as_invalid_response() -> None:
    verifier = _verifier(error=ValueError("Expecting value: line 1 column 1 (char 0)"))

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.INVALID_RESPONSE


def test_oversized_license_is_rejected_not_truncated() -> None:
    verifier = _verifier(result=_ref(license="x" * (LICENSE_MAX_LENGTH + 1)))

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.RESPONSE_TOO_LARGE


def test_oversized_card_url_is_rejected() -> None:
    long_url = "https://x/" + "a" * DATASET_CARD_URL_MAX_LENGTH
    verifier = _verifier(result=_ref(dataset_card_url=long_url))

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.RESPONSE_TOO_LARGE


def test_malformed_result_shape_is_invalid_response() -> None:
    verifier = _verifier(result=object())  # 缺字段

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.INVALID_RESPONSE


def test_credentials_are_redacted_from_error_text() -> None:
    """远端报错里若混入 token / 绝对路径，异常文本必须已脱敏。"""
    verifier = _verifier(
        error=_ServerError(
            "500 error for https://huggingface.co/api/datasets/x?token=hf_secret_value "
            "Authorization: Bearer hf_another_secret"
        )
    )

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    message = str(excinfo.value)
    assert "hf_secret_value" not in message
    assert "hf_another_secret" not in message
    assert "huggingface.co" not in message


def test_transient_error_retries_up_to_cap() -> None:
    """瞬时错误（5xx）在有重试预算时重试，用尽后失败。"""
    sleeps: list[float] = []
    factory = _Factory(
        _FakeAdapter(error=_ServerError("503 Service Unavailable")),
        _FakeAdapter(error=_ServerError("503 Service Unavailable")),
    )
    verifier = HuggingFaceRemoteVerifier(
        adapter_factory=factory,
        max_attempts=2,
        sleep=sleeps.append,
    )

    with pytest.raises(RemoteCheckError) as excinfo:
        verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert excinfo.value.reason is RemoteCheckFailureReason.SERVER_ERROR
    assert factory.calls == 2
    assert sleeps == [0.0]


def test_deterministic_error_does_not_retry() -> None:
    """404 是确定性错误，即使有重试预算也只调用一次。"""
    factory = _Factory(_FakeAdapter(error=_NotFound("404 Not Found")))
    verifier = HuggingFaceRemoteVerifier(
        adapter_factory=factory,
        max_attempts=3,
        sleep=lambda _s: None,
    )

    with pytest.raises(RemoteCheckError):
        verifier.describe("lerobot/missing", revision=_SHA)

    assert factory.calls == 1


def test_transient_error_recovers_on_retry() -> None:
    """首次瞬时失败、重试成功时返回结果。"""
    factory = _Factory(
        _FakeAdapter(error=_Timeout("timed out")),
        _FakeAdapter(result=_ref()),
    )
    verifier = HuggingFaceRemoteVerifier(
        adapter_factory=factory,
        max_attempts=2,
        sleep=lambda _s: None,
    )

    result = verifier.describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert result.resolved_revision == _SHA
    assert factory.calls == 2
