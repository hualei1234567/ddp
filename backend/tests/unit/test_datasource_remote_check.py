"""远端校验摘要的读取与防伪测试。

详情 API 只把结构合法、状态为 VERIFIED 的摘要当"已校验"；形状损坏或人工
污染必须降级为"未校验"，不能渲染成通过。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.datasources.verification import (
    REMOTE_CHECK_METADATA_KEY,
    RemoteCheckResult,
    build_remote_check_metadata,
    read_remote_check,
)

_SHA = "0123456789abcdef0123456789abcdef01234567"
_CHECKED_AT = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)


def _result() -> RemoteCheckResult:
    return RemoteCheckResult(
        repo_id="lerobot/aloha_sim_insertion_human",
        resolved_revision=_SHA,
        license="Apache-2.0",
        is_gated=False,
        dataset_card_url="https://huggingface.co/datasets/lerobot/aloha_sim_insertion_human",
    )


def _metadata() -> dict[str, object]:
    return {
        REMOTE_CHECK_METADATA_KEY: build_remote_check_metadata(
            _result(),
            checked_at=_CHECKED_AT,
        )
    }


def test_round_trip_returns_summary() -> None:
    summary = read_remote_check(_metadata())

    assert summary is not None
    assert summary.checked_at == _CHECKED_AT
    assert summary.resolved_revision == _SHA
    assert summary.license == "Apache-2.0"
    assert summary.is_gated is False


def test_missing_key_is_unverified() -> None:
    assert read_remote_check({}) is None


def test_non_mapping_is_unverified() -> None:
    assert read_remote_check({REMOTE_CHECK_METADATA_KEY: "VERIFIED"}) is None


def test_unknown_status_is_unverified() -> None:
    metadata = _metadata()
    metadata[REMOTE_CHECK_METADATA_KEY] = {
        **metadata[REMOTE_CHECK_METADATA_KEY],  # type: ignore[dict-item]
        "status": "PENDING",
    }

    assert read_remote_check(metadata) is None


def test_invalid_revision_is_unverified() -> None:
    metadata = _metadata()
    metadata[REMOTE_CHECK_METADATA_KEY] = {
        **metadata[REMOTE_CHECK_METADATA_KEY],  # type: ignore[dict-item]
        "resolved_revision": "main",
    }

    assert read_remote_check(metadata) is None


def test_naive_timestamp_is_unverified() -> None:
    metadata = _metadata()
    metadata[REMOTE_CHECK_METADATA_KEY] = {
        **metadata[REMOTE_CHECK_METADATA_KEY],  # type: ignore[dict-item]
        "checked_at": "2026-11-18T08:00:00",
    }

    assert read_remote_check(metadata) is None


def test_non_boolean_gated_is_unverified() -> None:
    metadata = _metadata()
    metadata[REMOTE_CHECK_METADATA_KEY] = {
        **metadata[REMOTE_CHECK_METADATA_KEY],  # type: ignore[dict-item]
        "is_gated": "false",
    }

    assert read_remote_check(metadata) is None
