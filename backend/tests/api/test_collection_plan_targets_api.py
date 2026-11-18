"""采集方案目标与质量阈值 API 测试（N9-003）。

单独成文件而不是继续追加到 ``test_collection_assets_api.py``：后者已超过
800 行评审阈值，本任务只新增一组独立关注点（目标/阈值的往返、校验与清空），
按主题切分比继续膨胀同一文件更易审阅。

复用既有测试脚手架（``_create`` / ``plan_payload``）与 ``api_db`` 夹具，
不重复实现请求与权限装配。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.test_collection_assets_api import _create, _url, plan_payload


def test_plan_targets_roundtrip_and_defaults(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = _create(
        client,
        ids.project_a,
        "collection-plans",
        plan_payload(
            code="plan-targets",
            target_episode_count=20,
            quality_thresholds={
                "min_selected_count": 20,
                "max_duplicated_count": 0,
                "max_warning_count": 2,
            },
        ),
    )
    assert created["target_episode_count"] == 20
    assert created["quality_thresholds"] == {
        "min_selected_count": 20.0,
        "max_duplicated_count": 0.0,
        "max_warning_count": 2.0,
    }

    detail = client.get(
        _url(ids.project_a, "collection-plans", f"/{created['plan_id']}")
    )
    assert detail.status_code == 200
    assert detail.json()["target_episode_count"] == 20
    assert detail.json()["quality_thresholds"] == created["quality_thresholds"]

    # 未提交时如实返回"未设定"，不伪装成 0 或空目标。
    plain = _create(client, ids.project_a, "collection-plans", plan_payload(code="plan-plain"))
    assert plain["target_episode_count"] is None
    assert plain["quality_thresholds"] == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_episode_count": -1},
        {"target_episode_count": 1.5},
        {"quality_thresholds": {"unknown_threshold": 1}},
        {"quality_thresholds": {"min_selected_count": -1}},
    ],
)
def test_plan_targets_validation_rejects_unknown_and_invalid_values(
    api_db: tuple[sessionmaker[Session], ApiIds],
    overrides: dict[str, object],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    denied = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload(code="plan-invalid", **overrides),
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["error"]["code"] == "validation_failed"


def test_plan_targets_patch_updates_and_clears(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create(
        client,
        ids.project_a,
        "collection-plans",
        plan_payload(code="plan-patch", target_episode_count=10),
    )

    updated = client.patch(
        _url(ids.project_a, "collection-plans", f"/{created['plan_id']}"),
        headers={"If-Match": '"1"'},
        json={
            "target_episode_count": 30,
            "quality_thresholds": {"max_blocking_issue_count": 0},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["target_episode_count"] == 30
    assert updated.json()["quality_thresholds"] == {"max_blocking_issue_count": 0.0}
    assert updated.json()["version"] == 2

    # 显式 null 清空：目标回到"未设定"，阈值回到"未指定"。
    cleared = client.patch(
        _url(ids.project_a, "collection-plans", f"/{created['plan_id']}"),
        headers={"If-Match": '"2"'},
        json={"target_episode_count": None, "quality_thresholds": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["target_episode_count"] is None
    assert cleared.json()["quality_thresholds"] == {}
    assert cleared.json()["version"] == 3
