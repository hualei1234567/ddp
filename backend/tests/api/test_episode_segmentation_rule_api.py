"""N3：切片规则校验的 HTTP 契约表现。

校验发生在应用层；这里证明 HTTP 层把领域错误正确映射为契约内的
``validation_failed``（422），并且 ``details`` 能让客户端直接定位——
新增独立错误码属于契约变更，本任务不新增。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from app.domain.episodes.segmentation_rules import (
    EPISODE_BOUNDARY_RULE_VERSION,
    FIXED_WINDOW_RULE_VERSION,
)
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import episode_payload, project_url, seed_valid_asset
from tests.fakes_s3 import FakeMultipartStorage


def _rule_payload(
    asset_id: UUID,
    *,
    code: str,
    rule_version: str,
    parameters: dict[str, object],
) -> dict[str, object]:
    payload = episode_payload(asset_id, code=code)
    payload["segmentation"] = {
        "method": "RULE",
        "rule_version": rule_version,
        "rule_parameters": parameters,
        "start_time_ns": 1_000,
        "end_time_ns": 2_000,
    }
    return payload


@pytest.fixture()
def rule_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[Any, ApiIds, UUID]:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="seg-rule")
    return client, ids, asset_id


def test_api_rejects_unregistered_rule_with_actionable_details(
    rule_api: tuple[Any, ApiIds, UUID],
) -> None:
    client, ids, asset_id = rule_api
    response = client.post(
        project_url(ids.project_a, "/episodes"),
        json=_rule_payload(
            asset_id,
            code="EP-RULE-GHOST",
            rule_version="ghost-rule/2.0.0",
            parameters={"window_ns": 10},
        ),
        headers={"Idempotency-Key": "episode-rule-ghost-0001"},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    # 不新增契约外 code：沿用 validation_failed。
    assert error["code"] == "validation_failed"
    assert error["details"]["rule_version"] == "ghost-rule/2.0.0"
    assert FIXED_WINDOW_RULE_VERSION in error["details"]["registered_rule_versions"]
    assert EPISODE_BOUNDARY_RULE_VERSION in error["details"]["registered_rule_versions"]
    # R-12：固定文案不回显领域异常原文。
    assert "ghost-rule/2.0.0" not in error["message"]


def test_api_accepts_registered_rule(rule_api: tuple[Any, ApiIds, UUID]) -> None:
    client, ids, asset_id = rule_api
    response = client.post(
        project_url(ids.project_a, "/episodes"),
        json=_rule_payload(
            asset_id,
            code="EP-RULE-OK",
            rule_version=FIXED_WINDOW_RULE_VERSION,
            parameters={"window_ns": 500},
        ),
        headers={"Idempotency-Key": "episode-rule-ok-0001"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["segmentation_rule_version"] == FIXED_WINDOW_RULE_VERSION


def test_api_rejects_rule_parameters_violating_schema(
    rule_api: tuple[Any, ApiIds, UUID],
) -> None:
    client, ids, asset_id = rule_api
    response = client.post(
        project_url(ids.project_a, "/episodes"),
        json=_rule_payload(
            asset_id,
            code="EP-RULE-BAD-PARAM",
            rule_version=FIXED_WINDOW_RULE_VERSION,
            parameters={"window_ns": -1},
        ),
        headers={"Idempotency-Key": "episode-rule-bad-param-0001"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field_path"] == ["window_ns"]


def test_api_still_accepts_manual_time_range(rule_api: tuple[Any, ApiIds, UUID]) -> None:
    """回归保护：既有客户端发送 ``seg-manual-v1`` 的人工切片必须继续可用。"""
    client, ids, asset_id = rule_api
    response = client.post(
        project_url(ids.project_a, "/episodes"),
        json=episode_payload(asset_id, code="EP-MANUAL-STILL-OK"),
        headers={"Idempotency-Key": "episode-manual-still-ok-0001"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["segmentation_rule_version"] == "seg-manual-v1"
