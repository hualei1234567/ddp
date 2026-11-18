"""多基地资源只读概览 API 集成测试（N9-005）。

真实 SQLite + 真实路由 + 真实仓储，无 mock。覆盖：

- 按场地聚合设备/传感器/采集任务数，空基地如实为 0；
- 未归属场地的资源不折算到任何基地，也不产生"幽灵场地"；
- 项目隔离与成员权限（outsider 403、viewer 只读可用）；
- ``kind`` 过滤、游标分页与参数上限；
- 响应形状与只读性（没有写操作）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, device_payload, make_client, sensor_payload


def _overview_url(project_id: object) -> str:
    return f"/api/v1/projects/{project_id}/sites/overview"


def _locations_url(project_id: object) -> str:
    return f"/api/v1/projects/{project_id}/locations"


def _create_location(
    client: TestClient,
    project_id: object,
    code: str,
    *,
    kind: str = "SITE",
) -> dict[str, object]:
    response = client.post(
        _locations_url(project_id),
        json={"code": code, "name": f"基地 {code}", "kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_device(
    client: TestClient,
    project_id: object,
    code: str,
    *,
    location_id: object | None = None,
) -> dict[str, object]:
    payload = device_payload(code)
    if location_id is not None:
        payload["location_id"] = str(location_id)
    response = client.post(f"/api/v1/projects/{project_id}/devices", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_sensor(
    client: TestClient,
    project_id: object,
    *,
    code: str,
    device_id: object,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/projects/{project_id}/sensors",
        json=sensor_payload(code=code, device_id=str(device_id)),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_task(
    client: TestClient,
    project_id: object,
    code: str,
    *,
    location_id: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"采集任务 {code}",
        "priority": "P1",
        "target_episode_count": 1,
        "target_duration_sec": 60,
    }
    if location_id is not None:
        payload["location_id"] = str(location_id)
    response = client.post(f"/api/v1/projects/{project_id}/collection-tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _by_code(response_body: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["code"]: item for item in response_body["items"]}  # type: ignore[index]


def test_overview_aggregates_per_site_and_reports_zero_for_empty_site(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    bj1 = _create_location(client, ids.project_a, "BJ-1")
    bj2 = _create_location(client, ids.project_a, "BJ-2", kind="LAB")

    device = _create_device(client, ids.project_a, "dev-1", location_id=bj1["location_id"])
    _create_sensor(client, ids.project_a, code="cam-1", device_id=device["device_id"])
    _create_sensor(client, ids.project_a, code="cam-2", device_id=device["device_id"])
    _create_task(client, ids.project_a, "task-bj1", location_id=bj1["location_id"])
    _create_task(client, ids.project_a, "task-bj2", location_id=bj2["location_id"])
    # 未指定场地的历史设备不得折算到任何基地，也不得生成"幽灵场地"。
    _create_device(client, ids.project_a, "dev-no-location")
    _create_task(client, ids.project_a, "task-no-location")

    response = client.get(_overview_url(ids.project_a))
    assert response.status_code == 200, response.text
    body = response.json()
    items = _by_code(body)

    assert set(items) == {"BJ-1", "BJ-2"}
    assert body["next_cursor"] is None
    assert items["BJ-1"]["device_count"] == 1
    assert items["BJ-1"]["sensor_count"] == 2
    assert items["BJ-1"]["collection_task_count"] == 1
    # 空基地如实返回 0，而不是省略该行。
    assert items["BJ-2"]["device_count"] == 0
    assert items["BJ-2"]["sensor_count"] == 0
    assert items["BJ-2"]["collection_task_count"] == 1
    assert items["BJ-2"]["kind"] == "LAB"
    assert set(items["BJ-1"]) == {
        "location_id",
        "code",
        "name",
        "kind",
        "device_count",
        "sensor_count",
        "collection_task_count",
    }


def test_overview_is_project_scoped_and_enforces_membership(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor_a = make_client(factory, ids.editor_a)
    owner_a = make_client(factory, ids.owner_a)

    a_site = _create_location(editor_a, ids.project_a, "BJ-1")
    b_site = _create_location(owner_a, ids.project_b, "SH-1")
    _create_device(owner_a, ids.project_b, "b-dev", location_id=b_site["location_id"])
    _create_task(owner_a, ids.project_b, "b-task", location_id=b_site["location_id"])

    a_body = editor_a.get(_overview_url(ids.project_a)).json()
    assert set(_by_code(a_body)) == {"BJ-1"}
    a_item = _by_code(a_body)["BJ-1"]
    assert a_item["device_count"] == 0
    assert a_item["collection_task_count"] == 0
    assert a_item["location_id"] == a_site["location_id"]

    b_body = owner_a.get(_overview_url(ids.project_b)).json()
    assert set(_by_code(b_body)) == {"SH-1"}
    assert _by_code(b_body)["SH-1"]["device_count"] == 1
    assert _by_code(b_body)["SH-1"]["collection_task_count"] == 1

    # 项目外用户：列表与概览都必须 403，不能借"聚合"绕过成员资格。
    outsider = make_client(factory, ids.outsider)
    assert outsider.get(_overview_url(ids.project_a)).status_code == 403

    # viewer 是只读角色：概览可用，且响应不得包含任何写操作提示。
    viewer = make_client(factory, ids.viewer_a)
    viewer_response = viewer.get(_overview_url(ids.project_a))
    assert viewer_response.status_code == 200
    assert viewer_response.json()["items"][0]["code"] == "BJ-1"


def test_overview_kind_filter_cursor_and_query_bounds(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    for code in ("BJ-1", "BJ-2", "BJ-3"):
        _create_location(client, ids.project_a, code)
    _create_location(client, ids.project_a, "BJ-LAB", kind="LAB")

    sites = client.get(_overview_url(ids.project_a), params={"kind": "SITE"})
    assert sites.status_code == 200
    assert {item["kind"] for item in sites.json()["items"]} == {"SITE"}
    assert len(sites.json()["items"]) == 3

    labs = client.get(_overview_url(ids.project_a), params={"kind": "LAB"})
    assert [item["code"] for item in labs.json()["items"]] == ["BJ-LAB"]

    invalid_kind = client.get(_overview_url(ids.project_a), params={"kind": "WAREHOUSE"})
    assert invalid_kind.status_code == 422
    assert invalid_kind.json()["error"]["code"] == "validation_failed"

    assert client.get(_overview_url(ids.project_a), params={"limit": 0}).status_code == 422
    assert client.get(_overview_url(ids.project_a), params={"limit": 101}).status_code == 422
    invalid_cursor = client.get(_overview_url(ids.project_a), params={"cursor": "not-a-cursor"})
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "validation_failed"

    first = client.get(_overview_url(ids.project_a), params={"limit": 2})
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second = client.get(
        _overview_url(ids.project_a),
        params={"limit": 2, "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 2
    assert second_body["next_cursor"] is None
    assert {item["location_id"] for item in first_body["items"]}.isdisjoint(
        {item["location_id"] for item in second_body["items"]}
    )


def test_overview_has_no_write_paths(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """只读契约：同一路径的 POST/PATCH/DELETE 不得被概览路由接受。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _create_location(client, ids.project_a, "BJ-1")

    assert client.post(_overview_url(ids.project_a), json={}).status_code == 405
    assert client.patch(_overview_url(ids.project_a), json={}).status_code == 405
    assert client.delete(_overview_url(ids.project_a)).status_code == 405
