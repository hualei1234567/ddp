"""N7-005：评测失败 → 候选缺口 → 人工转需求闭环的接口验收。

覆盖：

- 候选视图按归一化 ``suggested_gap`` 聚合，并只挂到既有维度键；
- 无法归因的候选（未知提示值 / 无提示值的类别）显式列出；
- ``GET`` 只读，不产生需求；转需求是显式人工动作且只创建 DRAFT；
- 拒绝自动路径：未归因候选与不存在候选都不得创建需求；
- 项目隔离与写权限。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.infrastructure.db.models.modelops import EvaluationRow

from tests.api.conftest import make_client
from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version
from tests.api.modelops_support import FakeInspector
from tests.api.modelops_support import callback as _callback
from tests.api.modelops_support import client as _client
from tests.api.modelops_support import create_model as _create_model
from tests.api.modelops_support import feedback_with_dimension as _feedback_with_dimension
from tests.api.modelops_support import ready_version as _ready_version

_THRESHOLDS = {"success_rate": {"min": 0.9}}
_NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


def _evaluation_with_failure_cases(factory, ids, failure_cases: list[dict]) -> dict:
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    context = build_frozen_version(
        factory,
        ids,
        seed_prefix=f"ep{uuid4().hex[:6]}",
        seed_salt=uuid4().hex,
    )
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    created = editor.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": str(context["version_id"]),
            "thresholds": _THRESHOLDS,
        },
    )
    assert created.status_code == 202, created.text
    evaluation = created.json()
    response = _callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": evaluation["evaluation_id"],
            "metrics": {"success_rate": 0.42},
            "failure_cases": failure_cases,
        },
    )
    assert response.status_code == 204, response.text
    return evaluation


def _candidates(client, project_id: UUID, **params) -> dict:
    response = client.get(url(project_id, "/data-gap-candidates"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _convert(
    client,
    project_id: UUID,
    candidate_key: str,
    *,
    owner_id: UUID,
):
    return client.post(
        url(project_id, f"/data-gap-candidates/{candidate_key}/data-requirements"),
        json={
            "code": f"DR-{uuid4().hex[:8].upper()}",
            "title": "候选缺口转需求",
            "owner_id": str(owner_id),
        },
        headers={"Idempotency-Key": f"idem-{uuid4().hex}"},
    )


def _failure_cases() -> list[dict]:
    return [
        {
            "id": "fc-1",
            "category": "PERCEPTION",
            "severity": "BLOCKER",
            "suggested_gap": "Kitchen",
        },
        {
            "id": "fc-2",
            "category": "PLANNING",
            "severity": "MAJOR",
            "suggested_gap": "  kitchen ",
        },
        {
            "id": "fc-3",
            "category": "OTHER",
            "severity": "MINOR",
            "suggested_gap": "spaceship",
        },
        {
            "id": "fc-4",
            "category": "CONTROL",
            "severity": "MINOR",
        },
    ]


def _unmapped_reasons(payload: dict) -> dict[str, str]:
    return {entry["label"]: entry["unmapped_reason"] for entry in payload["unmapped"]}


# --------------------------------------------------------------------------- #
# 只读候选视图
# --------------------------------------------------------------------------- #
def test_candidates_aggregate_hints_and_list_unmapped(api_db) -> None:
    factory, ids = api_db
    evaluation = _evaluation_with_failure_cases(factory, ids, _failure_cases())
    editor = _client(factory, ids.editor_a, FakeInspector(128))

    payload = _candidates(editor, ids.project_a)

    assert len(payload["items"]) == 1
    candidate = payload["items"][0]
    # 默认数据集种子场景就是 kitchen，实测覆盖已建立该维度键；候选只挂既有键。
    assert candidate["dimension_type"] == "SCENE"
    assert candidate["dimension_value"] == "kitchen"
    assert candidate["failure_count"] == 2
    assert candidate["evaluation_count"] == 1
    assert candidate["categories"] == ["PERCEPTION", "PLANNING"]
    assert candidate["severities"] == ["BLOCKER", "MAJOR"]
    assert candidate["source_evaluation_ids"] == [evaluation["evaluation_id"]]

    assert _unmapped_reasons(payload) == {
        "spaceship": "VALUE_NOT_FOUND",
        "CONTROL": "NO_DIMENSION",
    }
    assert payload["items_truncated"] is False
    assert payload["unmapped_truncated"] is False
    assert payload["scan_truncated"] is False
    assert payload["scanned_evaluation_count"] == 1
    assert payload["invalid_evaluation_count"] == 0


def test_candidate_view_is_read_only(api_db) -> None:
    factory, ids = api_db
    _evaluation_with_failure_cases(factory, ids, _failure_cases())
    editor = _client(factory, ids.editor_a, FakeInspector(128))

    before = editor.get(url(ids.project_a, "/data-requirements"))
    assert before.status_code == 200, before.text
    assert before.json()["items"] == []

    _candidates(editor, ids.project_a)

    after = editor.get(url(ids.project_a, "/data-requirements"))
    assert after.status_code == 200, after.text
    assert after.json()["items"] == [], "只读候选视图不得创建需求"


def test_limit_is_bounded_and_marked(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version["model_version_id"],
        dimension_type="ACTION",
        dimension_value="pick",
    )
    _evaluation_with_failure_cases(
        factory,
        ids,
        [
            {
                "id": "fc-1",
                "category": "PERCEPTION",
                "severity": "MAJOR",
                "suggested_gap": "kitchen",
            },
            {"id": "fc-2", "category": "PLANNING", "severity": "MAJOR", "suggested_gap": "pick"},
        ],
    )

    payload = _candidates(editor, ids.project_a, limit=1)

    assert len(payload["items"]) == 1
    assert payload["items_truncated"] is True


# --------------------------------------------------------------------------- #
# 人工转需求
# --------------------------------------------------------------------------- #
def test_human_conversion_creates_draft_requirement(api_db) -> None:
    factory, ids = api_db
    evaluation = _evaluation_with_failure_cases(factory, ids, _failure_cases())
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    candidate = _candidates(editor, ids.project_a)["items"][0]

    response = _convert(
        editor,
        ids.project_a,
        candidate["candidate_key"],
        owner_id=ids.owner_a,
    )

    assert response.status_code == 201, response.text
    requirement = response.json()
    assert requirement["status"] == "DRAFT"
    assert requirement["source_type"] == "EVALUATION"
    assert requirement["dimensions"] == [
        {
            "dimension_type": "SCENE",
            "dimension_value": "kitchen",
            "current_count": None,
            "target_count": None,
        }
    ]
    assert requirement["source_evaluation_ids"] == [evaluation["evaluation_id"]]
    # 显式人工动作只创建 DRAFT：不得自动提交或审批。
    assert "approve" not in requirement["allowed_actions"]
    assert "submit" in requirement["allowed_actions"]


def test_conversion_of_unmapped_candidate_is_rejected(api_db) -> None:
    factory, ids = api_db
    _evaluation_with_failure_cases(factory, ids, _failure_cases())
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    payload = _candidates(editor, ids.project_a)
    unmapped = next(entry for entry in payload["unmapped"] if entry["label"] == "spaceship")

    response = _convert(
        editor,
        ids.project_a,
        unmapped["candidate_key"],
        owner_id=ids.owner_a,
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"

    listed = editor.get(url(ids.project_a, "/data-requirements"))
    assert listed.json()["items"] == []


def test_unknown_candidate_key_is_not_found(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))

    response = _convert(
        editor,
        ids.project_a,
        "sg_0000000000000000",
        owner_id=ids.owner_a,
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "resource_not_found"


# --------------------------------------------------------------------------- #
# 项目隔离与权限
# --------------------------------------------------------------------------- #
def test_candidates_are_project_scoped_and_require_write_permission(api_db) -> None:
    factory, ids = api_db
    _evaluation_with_failure_cases(factory, ids, _failure_cases())
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    candidate = _candidates(editor, ids.project_a)["items"][0]

    # 项目 B 看不到项目 A 的失败事实。
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    assert _candidates(owner, ids.project_b)["items"] == []
    cross = _convert(
        owner,
        ids.project_b,
        candidate["candidate_key"],
        owner_id=ids.owner_a,
    )
    assert cross.status_code == 404, cross.text

    # 只读用户可以查看候选，但不能转需求。
    viewer = make_client(factory, ids.viewer_a)
    assert _candidates(viewer, ids.project_a)["items"] != []
    forbidden = _convert(
        viewer,
        ids.project_a,
        candidate["candidate_key"],
        owner_id=ids.owner_a,
    )
    assert forbidden.status_code == 403, forbidden.text

    # 项目外用户连读都不允许。
    outsider = make_client(factory, ids.outsider)
    denied = outsider.get(url(ids.project_a, "/data-gap-candidates"))
    assert denied.status_code == 403, denied.text


def test_failure_facts_are_project_scoped_at_query_level(api_db) -> None:
    factory, ids = api_db
    _evaluation_with_failure_cases(factory, ids, _failure_cases())
    # 直接向项目 B 写入一条失败事实：A 的候选视图不得包含它。
    owner_b = _client(factory, ids.owner_a, FakeInspector(128))
    model_b = _create_model(owner_b, ids.project_b, ids.owner_a)
    version_b = _ready_version(owner_b, ids.project_b, model_b["model_id"])
    with factory() as session:
        row = EvaluationRow(
            id=uuid4(),
            project_id=ids.project_b,
            model_version_id=UUID(version_b["model_version_id"]),
            dataset_version_id=uuid4(),
            status="QUEUED",
            metrics={},
            thresholds={},
            verdict="UNKNOWN",
            report_ref=None,
            failure_cases=[
                {
                    "id": "fc-b",
                    "category": "PERCEPTION",
                    "severity": "MAJOR",
                    "suggested_gap": "warehouse",
                }
            ],
            version=1,
            created_at=_NOW,
            updated_at=_NOW,
        )
        session.add(row)
        session.flush()
        row.status = "SUCCEEDED"
        row.verdict = "FAIL"
        session.commit()

    editor = _client(factory, ids.editor_a, FakeInspector(128))
    payload = _candidates(editor, ids.project_a)

    assert all(item["label"] != "warehouse" for item in payload["items"])
    assert all(item["label"] != "warehouse" for item in payload["unmapped"])
