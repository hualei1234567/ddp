"""N7-003：结构化 failure_cases 的回调写入、上限与项目隔离。

单独成文件：``test_modelops_api.py`` 已超过 800 行，按既有 ``modelops_support``
的拆分口径不再继续堆叠；本文件复用同一套客户端与签名辅助。
"""

from __future__ import annotations

from uuid import uuid4

from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version
from tests.api.modelops_support import FakeInspector
from tests.api.modelops_support import callback as _callback
from tests.api.modelops_support import client as _client
from tests.api.modelops_support import create_model as _create_model
from tests.api.modelops_support import ready_version as _ready_version

_THRESHOLDS = {"success_rate": {"min": 0.9}}


def _create_evaluation(factory, editor, ids) -> dict[str, object]:
    # 同一项目内可能多次造评测；Episode code 与 RawAsset hash 都要唯一。
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
    return created.json()


def _completion(evaluation_id: str, ids, failure_cases: list[dict]) -> dict:
    return {
        "event_id": f"evt-{uuid4().hex[:8]}",
        "adapter_version": "1.0.0",
        "event_type": "evaluation.completed",
        "project_id": str(ids.project_a),
        "evaluation_id": evaluation_id,
        "metrics": {"success_rate": 0.42},
        "failure_cases": failure_cases,
    }


def test_failure_cases_roundtrip(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    evaluation = _create_evaluation(factory, editor, ids)

    cases = [
        {
            "id": "fc-1",
            "category": "PERCEPTION",
            "severity": "MAJOR",
            "episode_key": "ep-001",
            "episode_id": None,
            "evidence": {"frame": 42, "object": "cup"},
            "metric": "grasp_success",
            "observed": 0,
            "expected": 1,
            "suggested_gap": "kitchen/cup",
        },
        {
            "id": "fc-2",
            "category": "CONTROL",
            "severity": "MINOR",
        },
    ]
    response = _callback(editor, _completion(evaluation["evaluation_id"], ids, cases))
    assert response.status_code == 204, response.text

    stored = editor.get(url(ids.project_a, f"/evaluations/{evaluation['evaluation_id']}")).json()
    assert stored["status"] == "SUCCEEDED"
    assert stored["verdict"] == "FAIL"
    assert [item["id"] for item in stored["failure_cases"]] == ["fc-1", "fc-2"]
    assert stored["failure_cases"][0]["category"] == "PERCEPTION"
    assert stored["failure_cases"][0]["evidence"] == {"frame": 42, "object": "cup"}
    assert stored["failure_cases"][0]["observed"] == 0
    assert stored["failure_cases"][0]["expected"] == 1
    # 未提供的可选字段按 null 返回，客户端不需要区分"缺失"与"null"。
    assert stored["failure_cases"][1]["episode_key"] is None
    assert stored["failure_cases"][1]["evidence"] is None

    # 显式空数组：合法，且与"迁移前存量"归一化结果一致。
    empty = _create_evaluation(factory, editor, ids)
    empty_response = _callback(editor, _completion(empty["evaluation_id"], ids, []))
    assert empty_response.status_code == 204, empty_response.text
    empty_stored = editor.get(url(ids.project_a, f"/evaluations/{empty['evaluation_id']}")).json()
    assert empty_stored["failure_cases"] == []


def test_invalid_or_oversized_failure_cases_are_rejected_without_side_effects(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    evaluation = _create_evaluation(factory, editor, ids)
    evaluation_id = evaluation["evaluation_id"]

    invalid_category = _callback(
        editor,
        _completion(
            evaluation_id,
            ids,
            [{"id": "fc-bad", "category": "NOT_A_CATEGORY", "severity": "MAJOR"}],
        ),
    )
    assert invalid_category.status_code == 422
    assert invalid_category.json()["error"]["code"] == "validation_failed"

    too_many = _callback(
        editor,
        _completion(
            evaluation_id,
            ids,
            [
                {"id": f"fc-{index}", "category": "OTHER", "severity": "MINOR"}
                for index in range(201)
            ],
        ),
    )
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "validation_failed"

    # 领域层字节上限：非法请求不得消耗 nonce，修正后可用同一 nonce 重试。
    nonce = f"nonce-{uuid4().hex[:8]}"
    oversized = _callback(
        editor,
        _completion(
            evaluation_id,
            ids,
            [
                {
                    "id": "fc-large",
                    "category": "PERCEPTION",
                    "severity": "BLOCKER",
                    "evidence": "x" * 5000,
                }
            ],
        ),
        nonce=nonce,
    )
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "validation_failed"

    unchanged = editor.get(url(ids.project_a, f"/evaluations/{evaluation_id}")).json()
    assert unchanged["status"] == "QUEUED"
    assert unchanged["failure_cases"] == []

    retried = _callback(
        editor,
        _completion(
            evaluation_id,
            ids,
            [{"id": "fc-ok", "category": "PLANNING", "severity": "MAJOR"}],
        ),
        nonce=nonce,
    )
    assert retried.status_code == 204, retried.text
    completed = editor.get(url(ids.project_a, f"/evaluations/{evaluation_id}")).json()
    assert completed["status"] == "SUCCEEDED"
    assert [item["id"] for item in completed["failure_cases"]] == ["fc-ok"]


def test_failure_cases_do_not_leak_across_projects(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    outsider = _client(factory, ids.outsider, FakeInspector(128))
    evaluation = _create_evaluation(factory, editor, ids)
    response = _callback(
        editor,
        _completion(
            evaluation["evaluation_id"],
            ids,
            [
                {
                    "id": "fc-1",
                    "category": "LANGUAGE",
                    "severity": "BLOCKER",
                    "evidence": "指令未被理解",
                }
            ],
        ),
    )
    assert response.status_code == 204, response.text

    forbidden = outsider.get(url(ids.project_a, f"/evaluations/{evaluation['evaluation_id']}"))
    assert forbidden.status_code in {403, 404}
    assert "failure_cases" not in forbidden.text


def test_failure_cases_are_not_overwritten_after_success(api_db) -> None:
    """成功评测是终态：再次回调按幂等处理，已落库的 failure_cases 不得被覆盖。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    evaluation = _create_evaluation(factory, editor, ids)
    evaluation_id = evaluation["evaluation_id"]

    original = [
        {
            "id": "fc-keep",
            "category": "PERCEPTION",
            "severity": "MAJOR",
            "observed": 0,
            "expected": 1,
        }
    ]
    first = _callback(editor, _completion(evaluation_id, ids, original))
    assert first.status_code == 204, first.text

    replacement = [
        {
            "id": "fc-new",
            "category": "CONTROL",
            "severity": "MINOR",
            "observed": 1,
            "expected": 1,
        }
    ]
    again = _callback(editor, _completion(evaluation_id, ids, replacement))
    assert again.status_code == 204, again.text

    stored = editor.get(url(ids.project_a, f"/evaluations/{evaluation_id}")).json()
    assert stored["status"] == "SUCCEEDED"
    assert [item["id"] for item in stored["failure_cases"]] == ["fc-keep"]
    assert stored["failure_cases"][0]["category"] == "PERCEPTION"
