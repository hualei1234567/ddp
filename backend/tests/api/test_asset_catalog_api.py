"""统一资产目录 API：多类型、过滤、分页、隔离、权限与非法输入。"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.asset_catalog_support import seed_asset_catalog


@pytest.fixture()
def catalog_client(api_db) -> tuple[TestClient, sessionmaker[Session], ApiIds, dict[str, UUID]]:
    factory, ids = api_db
    seed_asset_catalog(factory, ids.project_a, ids.editor_a, handle="a")
    ids_b = seed_asset_catalog(factory, ids.project_b, ids.owner_a, handle="b")
    client = make_client(factory, ids.viewer_a)
    return client, factory, ids, ids_b


def _url(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/assets/search"


def test_search_returns_multiple_kinds_and_unified_fields(catalog_client) -> None:
    client, _, ids, _ = catalog_client
    response = client.get(_url(ids.project_a), params={"q": "ep-"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_cursor"] is None
    assert [item["kind"] for item in body["items"]] == [
        "EPISODE",
        "MEDIA_ASSET",
        "EPISODE",
        "MEDIA_ASSET",
    ]
    assert all(set(item) == {"kind", "id", "display_name", "updated_at"} for item in body["items"])

    raw = client.get(_url(ids.project_a), params={"q": "raw-alpha"})
    assert raw.status_code == 200
    assert raw.json()["items"][0]["display_name"] == "alpha.mcap"


def test_kind_filter_narrows_results(catalog_client) -> None:
    client, _, ids, _ = catalog_client
    response = client.get(
        _url(ids.project_a),
        params={"q": "ep-", "kind": "MEDIA_ASSET"},
    )
    assert response.status_code == 200
    assert {item["kind"] for item in response.json()["items"]} == {"MEDIA_ASSET"}

    blank = client.get(
        _url(ids.project_a),
        params={"q": "ep-", "kind": "DATASET"},
    )
    assert blank.status_code == 200
    assert blank.json()["items"] == []


def test_pagination_follows_cursor_without_duplicates(catalog_client) -> None:
    client, _, ids, _ = catalog_client
    seen: list[dict[str, object]] = []
    cursor = None
    for _ in range(10):
        params: dict[str, object] = {"q": "ep-", "limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(_url(ids.project_a), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert [item["display_name"] for item in seen] == [
        "ep-beta",
        "media/a/ep-beta/cam0.mp4",
        "ep-alpha",
        "media/a/ep-alpha/cam0.mp4",
    ]
    assert len({str(item["id"]) for item in seen}) == 4


def test_cross_project_isolation(catalog_client) -> None:
    client, _, ids, ids_b = catalog_client
    response = client.get(_url(ids.project_a), params={"q": "ep-"})
    assert response.status_code == 200
    returned = {item["id"] for item in response.json()["items"]}
    assert str(ids_b["episode_alpha"]) not in returned
    assert str(ids_b["media_alpha"]) not in returned

    # viewer_a 不是项目 B 成员：即使路径里写 B，也必须 403，不能用状态码探测。
    denied = client.get(_url(ids.project_b), params={"q": "ep-"})
    assert denied.status_code == 403


def test_permissions_and_invalid_query(catalog_client) -> None:
    client, factory, ids, _ = catalog_client
    outsider = make_client(factory, ids.outsider)
    assert outsider.get(_url(ids.project_a), params={"q": "ep-"}).status_code == 403

    # 缺少 q / q 为空 / 全空白 / 超长。
    assert client.get(_url(ids.project_a)).status_code == 422
    assert client.get(_url(ids.project_a), params={"q": ""}).status_code == 422
    blank = client.get(_url(ids.project_a), params={"q": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_failed"
    assert client.get(_url(ids.project_a), params={"q": "a" * 65}).status_code == 422

    # 非法 kind / 非法 cursor 都是 422，不静默忽略。
    assert client.get(_url(ids.project_a), params={"q": "ep-", "kind": "NOPE"}).status_code == 422
    assert (
        client.get(_url(ids.project_a), params={"q": "ep-", "cursor": "not-base64!!"}).status_code
        == 422
    )
