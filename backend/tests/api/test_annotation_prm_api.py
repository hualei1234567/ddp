"""PRM 步级标注 API 集成测试（V52-020）。

覆盖验收点：
- 创建（含部分标注）→ 查询 → 列表全链路；
- fork 纯计算预览：分叉轨迹与 Step-DPO 对语义（前缀共享、chosen/rejected）；
- 同轨迹重复创建 409；越界 fork 步骤 422；
- 跨项目隔离：B 项目看不到 A 项目的轨迹标注；
- 越权：viewer 不可创建。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import url
from tests.api.conftest import ApiIds, make_client

_PRM_PATH = "/prm-trajectory-annotations"


def _annotation_payload(trajectory_id: str = "traj-api-1") -> dict[str, object]:
    return {
        "trajectory_id": trajectory_id,
        "task_goal": "把杯子从 A 移到 B",
        "step_actions": ["查表", "抓取", "放置"],
        "marks": [
            {"step_index": 0, "label": "CORRECT"},
            {"step_index": 1, "label": "HALLUCINATED_TOOL", "comment": "抓空了"},
        ],
    }


def test_create_and_get_roundtrip(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)

    created = editor.post(base, json=_annotation_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["trajectory_id"] == "traj-api-1"
    assert body["step_count"] == 3
    assert body["marked_count"] == 2
    assert [m["label"] for m in body["marks"]] == ["CORRECT", "HALLUCINATED_TOOL"]
    assert body["marks"][1]["reward"] == -1.0
    assert body["total_reward"] == 0.0  # +1 + (-1)
    assert body["label_counts"] == {"CORRECT": 1, "HALLUCINATED_TOOL": 1}
    assert body["annotation_id"]
    assert body["version"] == 1

    got = editor.get(f"{base}/{body['annotation_id']}")
    assert got.status_code == 200, got.text
    assert got.json()["trajectory_id"] == "traj-api-1"

    listed = editor.get(base)
    assert listed.status_code == 200, listed.text
    assert [item["trajectory_id"] for item in listed.json()["items"]] == ["traj-api-1"]


def test_duplicate_trajectory_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)
    first = editor.post(base, json=_annotation_payload("traj-dup"))
    assert first.status_code == 201, first.text
    second = editor.post(base, json=_annotation_payload("traj-dup"))
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "duplicate_resource"


def test_invalid_label_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    payload = _annotation_payload("traj-bad-label")
    payload["marks"] = [{"step_index": 0, "label": "PERFECT"}]
    response = editor.post(url(ids.project_a, _PRM_PATH), json=payload)
    assert response.status_code == 422, response.text


def test_out_of_range_mark_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    payload = _annotation_payload("traj-oob")
    payload["marks"] = [{"step_index": 9, "label": "CORRECT"}]
    response = editor.post(url(ids.project_a, _PRM_PATH), json=payload)
    assert response.status_code == 422, response.text


def test_fork_preview_returns_step_dpo_pair(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    """fork 是纯计算端点：返回分叉轨迹与 Step-DPO 对，且不产生新行。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)
    created = editor.post(base, json=_annotation_payload("traj-fork"))
    assert created.status_code == 201, created.text

    preview = editor.post(
        f"{base}/traj-fork/fork",
        json={"at_index": 1, "new_actions": ["重抓杯子", "稳稳放置"]},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["source_trajectory_id"] == "traj-fork"
    assert body["fork_point_index"] == 1
    assert body["shared_prefix_steps"] == 1
    # 分叉点前共享前缀（含标注）保留；重写步骤未标注。
    assert body["forked_steps"] == ["查表", "重抓杯子", "稳稳放置"]
    assert [m["step_index"] for m in body["forked_marks"]] == [0]
    pair = body["dpo_pair"]
    assert pair["chosen"] == "[1] 重抓杯子\n[2] 稳稳放置"
    assert pair["rejected"] == "[1] 抓取\n[2] 放置"
    assert "[0] 查表" in pair["prompt"]
    assert pair["preference_json_value"]["winner"] == "A"
    assert pair["preference_json_value"]["fork_point_index"] == 1

    # 预览不落库：列表仍只有一条。
    listed = editor.get(base)
    assert [item["trajectory_id"] for item in listed.json()["items"]] == ["traj-fork"]


def test_fork_out_of_range_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)
    created = editor.post(base, json=_annotation_payload("traj-fork-oob"))
    assert created.status_code == 201, created.text
    response = editor.post(
        f"{base}/traj-fork-oob/fork",
        json={"at_index": 3, "new_actions": ["新动作"]},
    )
    assert response.status_code == 422, response.text


def test_fork_missing_trajectory_404(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    response = editor.post(
        url(ids.project_a, f"{_PRM_PATH}/no-such-traj/fork"),
        json={"at_index": 0, "new_actions": ["新动作"]},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "resource_not_found"


def test_cross_project_isolation(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    """跨项目隔离：B 项目（owner_b）看不到 A 项目的轨迹标注。"""
    factory, ids = api_db
    editor_a = make_client(factory, ids.editor_a)
    created = editor_a.post(url(ids.project_a, _PRM_PATH), json=_annotation_payload("traj-iso"))
    assert created.status_code == 201, created.text
    annotation_id = created.json()["annotation_id"]

    # 项目 B 的成员只有 owner_a（见 seed_identity）。
    owner_b_view = make_client(factory, ids.owner_a)
    listed_b = owner_b_view.get(url(ids.project_b, _PRM_PATH))
    assert listed_b.status_code == 200, listed_b.text
    assert listed_b.json()["items"] == []
    got_b = owner_b_view.get(url(ids.project_b, f"{_PRM_PATH}/{annotation_id}"))
    assert got_b.status_code == 404, got_b.text


def test_viewer_cannot_create(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)
    response = viewer.post(url(ids.project_a, _PRM_PATH), json=_annotation_payload("traj-viewer"))
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("limit", [0, 201])
def test_list_limit_bounds_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
    limit: int,
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    response = editor.get(url(ids.project_a, _PRM_PATH), params={"limit": limit})
    assert response.status_code == 422, response.text


# ── V52-032：fork 一步物化（materialize=true） ──────────────────────────


def _fork_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"at_index": 1, "new_actions": ["重抓杯子", "稳稳放置"]}
    payload.update(overrides)
    return payload


def _created_trajectory_ids(client, project_id: object) -> list[str]:
    listed = client.get(url(project_id, _PRM_PATH))
    assert listed.status_code == 200, listed.text
    return [item["trajectory_id"] for item in listed.json()["items"]]


def test_fork_materialize_creates_visible_row(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """物化：一次请求完成分叉 + 落库，新行列表可见且带分叉血缘。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)
    assert editor.post(base, json=_annotation_payload("traj-mat")).status_code == 201

    response = editor.post(f"{base}/traj-mat/fork", json=_fork_payload(materialize=True))
    assert response.status_code == 200, response.text
    body = response.json()
    annotation_id = body["materialized_annotation_id"]
    assert annotation_id
    # 物化时 preview id 字段是**实际落库**的 trajectory_id。
    assert body["forked_trajectory_id_preview"] == "traj-mat#fork-1"
    assert body["dpo_pair"]["preference_json_value"]["winner"] == "A"

    assert _created_trajectory_ids(editor, ids.project_a) == ["traj-mat", "traj-mat#fork-1"]
    got = editor.get(f"{base}/{annotation_id}")
    assert got.status_code == 200, got.text
    materialized = got.json()
    assert materialized["trajectory_id"] == "traj-mat#fork-1"
    assert materialized["step_actions"] == ["查表", "重抓杯子", "稳稳放置"]
    assert [mark["step_index"] for mark in materialized["marks"]] == [0]
    assert materialized["fork_lineage"] == {
        "source_trajectory_id": "traj-mat",
        "fork_point_index": 1,
        "shared_prefix_steps": 1,
    }

    # 预览（默认 materialize=false / 不传）仍不落库，且 materialized id 为 null。
    preview = editor.post(f"{base}/traj-mat/fork", json=_fork_payload())
    assert preview.status_code == 200, preview.text
    assert preview.json()["materialized_annotation_id"] is None
    assert preview.json()["forked_trajectory_id_preview"] == "traj-mat#fork-preview"
    assert len(_created_trajectory_ids(editor, ids.project_a)) == 2


def test_fork_materialize_increments_on_conflict(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """trajectory_id 唯一冲突时序号递增：已占用 #fork-1/#fork-2 → 落 #fork-3。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)
    for trajectory_id in ("traj-c", "traj-c#fork-1", "traj-c#fork-2"):
        created = editor.post(base, json=_annotation_payload(trajectory_id))
        assert created.status_code == 201, created.text

    response = editor.post(f"{base}/traj-c/fork", json=_fork_payload(materialize=True))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["forked_trajectory_id_preview"] == "traj-c#fork-3"
    got = editor.get(f"{base}/{body['materialized_annotation_id']}")
    assert got.json()["trajectory_id"] == "traj-c#fork-3"
    assert len(_created_trajectory_ids(editor, ids.project_a)) == 4


def test_fork_materialize_retry_exhausted_returns_409(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """连续 5 个候选 id 全冲突：显式 409，不静默覆盖、不留下第 6 行。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    base = url(ids.project_a, _PRM_PATH)
    for sequence in range(0, 6):
        trajectory_id = "traj-x" if sequence == 0 else f"traj-x#fork-{sequence}"
        assert editor.post(base, json=_annotation_payload(trajectory_id)).status_code == 201

    response = editor.post(f"{base}/traj-x/fork", json=_fork_payload(materialize=True))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_resource"
    assert len(_created_trajectory_ids(editor, ids.project_a)) == 6


def test_viewer_can_preview_but_cannot_materialize(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """预览保持只读可用；物化是写入，只读用户 403 且不落库。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    base = url(ids.project_a, _PRM_PATH)
    assert editor.post(base, json=_annotation_payload("traj-perm")).status_code == 201

    preview = viewer.post(f"{base}/traj-perm/fork", json=_fork_payload())
    assert preview.status_code == 200, preview.text

    blocked = viewer.post(f"{base}/traj-perm/fork", json=_fork_payload(materialize=True))
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "forbidden"
    assert _created_trajectory_ids(viewer, ids.project_a) == ["traj-perm"]
