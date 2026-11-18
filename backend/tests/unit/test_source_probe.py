"""S3 来源探测适配器：三态语义（存在 / 明确不存在 / 无法判定）。"""

from __future__ import annotations

from typing import Any

import pytest
from app.application.datasets.ports import SourceReachability
from app.infrastructure.storage.source_probe import S3SourceProbe
from botocore.exceptions import ClientError, EndpointConnectionError


class _FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {}


def _probe(client: _FakeClient) -> S3SourceProbe:
    probe = object.__new__(S3SourceProbe)
    probe._bucket = "ddp-raw"  # type: ignore[attr-defined]
    probe._client = client  # type: ignore[attr-defined]
    return probe


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadObject",
    )


def test_existing_object_is_reachable() -> None:
    client = _FakeClient()
    assert _probe(client).probe("canonical/p/e/1/manifest.json") is (SourceReachability.REACHABLE)
    assert client.calls == [{"Bucket": "ddp-raw", "Key": "canonical/p/e/1/manifest.json"}]


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_missing_object_is_stable_fact(code: str) -> None:
    probe = _probe(_FakeClient(_client_error(code, 404)))
    assert probe.probe("key") is SourceReachability.MISSING


def test_server_error_is_unreachable_not_missing() -> None:
    probe = _probe(_FakeClient(_client_error("InternalError", 500)))
    assert probe.probe("key") is SourceReachability.UNREACHABLE


def test_transport_error_is_unreachable() -> None:
    probe = _probe(_FakeClient(EndpointConnectionError(endpoint_url="http://s3")))
    assert probe.probe("key") is SourceReachability.UNREACHABLE
