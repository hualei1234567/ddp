"""N7-004：Data Gap 多来源证据的接口级验收。

覆盖四类真实来源与边界：

- 评测失败用例（``suggested_gap`` 提示值）能归到既有维度，匹配不上进未映射；
- 低分评测作为项目级证据进入未映射，不硬塞进某个维度；
- 未满足数据需求按自身维度计数；
- 标注双标一致性失败（``ESCALATED``）按 ``label_key`` 归到 ``LABEL`` 轴；
- 项目隔离与读权限：跨项目证据不得泄漏。

证据只挂在既有维度键上，且不改变 ``current_count`` 口径。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.infrastructure.db.models.modelops import EvaluationRow

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_arbiter,
    seed_episode,
)
from tests.api.conftest import ApiIds, make_client
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
_DOUBLE_POLICY = {
    "review_mode": "DOUBLE",
    "agreement_threshold": 0.8,
    "agreement_metric": "PERCENT_AGREEMENT",
}


def _gaps(client, project_id: UUID, **params) -> dict:
    response = client.get(url(project_id, "/data-gaps"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _item(gaps: dict, dimension_type: str, dimension_value: str) -> dict:
    return next(
        item
        for item in gaps["items"]
        if item["dimension_type"] == dimension_type and item["dimension_value"] == dimension_value
    )


def _sources(item: dict) -> dict[str, int]:
    return {entry["source"]: entry["count"] for entry in item["sources"]}


def _unmapped(gaps: dict) -> dict[tuple[str, str], int]:
    return {(entry["source"], entry["reason"]): entry["count"] for entry in gaps["unmapped"]}


def _feedback_version(factory, ids, project_id: UUID | None = None):
    project = project_id or ids.project_a
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, project, ids.owner_a)
    version = _ready_version(editor, project, model["model_id"])
    return editor, version["model_version_id"]


def _evaluation_with_failure_cases(factory, editor, ids, failure_cases: list[dict]) -> dict:
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
    payload = {
        "event_id": f"evt-{uuid4().hex[:8]}",
        "adapter_version": "1.0.0",
        "event_type": "evaluation.completed",
        "project_id": str(ids.project_a),
        "evaluation_id": evaluation["evaluation_id"],
        # 低于阈值 → verdict=FAIL，构成低分证据。
        "metrics": {"success_rate": 0.42},
        "failure_cases": failure_cases,
    }
    response = _callback(editor, payload)
    assert response.status_code == 204, response.text
    return evaluation


# --------------------------------------------------------------------------- #
# 评测失败用例 / 低分评测
# --------------------------------------------------------------------------- #
def test_failure_cases_feed_evidence_and_low_score_stays_unmapped(api_db) -> None:
    factory, ids = api_db
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="SCENE",
        dimension_value="kitchen",
        target_count=20,
    )
    _evaluation_with_failure_cases(
        factory,
        _client(factory, ids.editor_a, FakeInspector(128)),
        ids,
        [
            {
                "id": "fc-match",
                "category": "PERCEPTION",
                "severity": "MAJOR",
                "suggested_gap": "  KiTcHeN ",
            },
            {
                "id": "fc-no-hint",
                "category": "OTHER",
                "severity": "MINOR",
            },
            {
                "id": "fc-unknown",
                "category": "PLANNING",
                "severity": "MINOR",
                "suggested_gap": "spaceship",
            },
        ],
    )
    gaps = _gaps(editor, ids.project_a)

    kitchen = _item(gaps, "SCENE", "kitchen")
    assert _sources(kitchen) == {"FEEDBACK": 1, "FAILURE_CASE": 1}
    # 证据不改变覆盖口径：数据集种子的 10 个 Episode 仍是实测覆盖，建议补采
    # 只由 target（20）与 current（10）决定，失败用例条数不参与。
    assert kitchen["coverage_source"] == "MEASURED"
    assert kitchen["current_count"] == 10
    assert kitchen["suggested_episode_count"] == 10

    unmapped = _unmapped(gaps)
    assert unmapped[("LOW_SCORE", "NO_DIMENSION")] == 1
    assert unmapped[("FAILURE_CASE", "NO_DIMENSION")] == 1
    assert unmapped[("FAILURE_CASE", "VALUE_NOT_FOUND")] == 1
    assert gaps["truncated_sources"] == []


# --------------------------------------------------------------------------- #
# 未满足数据需求
# --------------------------------------------------------------------------- #
def test_open_requirement_is_counted_as_gap_source(api_db) -> None:
    factory, ids = api_db
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="SCENE",
        dimension_value="kitchen",
    )
    created = editor.post(
        url(ids.project_a, "/data-requirements"),
        json={
            "code": f"DR-{uuid4().hex[:6]}",
            "title": "厨房场景补数",
            "source_type": "MANUAL",
            "dimensions": [{"dimension_type": "SCENE", "dimension_value": "kitchen"}],
            "owner_id": str(ids.owner_a),
        },
    )
    assert created.status_code == 201, created.text

    gaps = _gaps(editor, ids.project_a)
    kitchen = _item(gaps, "SCENE", "kitchen")
    assert _sources(kitchen)["REQUIREMENT"] == 1
    assert str(created.json()["requirement_id"]) in kitchen["requirement_ids"]
    # 需求本身没有产生未映射证据。
    assert all(entry["source"] != "REQUIREMENT" for entry in gaps["unmapped"])


# --------------------------------------------------------------------------- #
# 标注分歧（IAA 冲突 / ESCALATED）
# --------------------------------------------------------------------------- #
def _add_assignment(client, project_id: UUID, batch_id: UUID, assignee_id: UUID) -> dict:
    created = client.post(
        url(project_id, "/annotation-assignments"),
        json={
            "batch_id": str(batch_id),
            "assignee_id": str(assignee_id),
            "planned_item_count": 1,
            "planned_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _claim_and_start(client, project_id: UUID, assignment: dict) -> dict:
    claimed = client.post(
        url(
            project_id,
            f"/annotation-assignments/{assignment['assignment_id']}/actions/claim",
        ),
        headers={"If-Match": f'"{assignment["version"]}"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    started = client.post(
        url(
            project_id,
            f"/annotation-assignments/{assignment['assignment_id']}/actions/start",
        ),
        headers={"If-Match": f'"{claimed.json()["version"]}"'},
        json={},
    )
    assert started.status_code == 200, started.text
    return started.json()


def _escalated_double_annotation(factory, ids: ApiIds) -> None:
    """制造一次双标一致性失败：两份提交都进入 ESCALATED。"""
    code = f"gap-{uuid4().hex[:6]}"
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    second_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    second = make_client(factory, second_id)
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        f"task-{code}",
        iaa_policy=dict(_DOUBLE_POLICY),
    )
    assignment = _add_assignment(editor, ids.project_a, work["batch"]["batch_id"], second_id)
    _claim_and_start(second, ids.project_a, assignment)
    create_submitted(editor, ids.project_a, work["assignment"]["assignment_id"], episode_id)
    second_submission = create_submitted(
        second,
        ids.project_a,
        assignment["assignment_id"],
        episode_id,
        action="PLACE",
        segment=(1_200, 1_600),
    )
    assert second_submission["consistency"]["status"] == "FAILED"


def test_annotation_conflicts_attach_to_label_keys(api_db) -> None:
    factory, ids = api_db
    _escalated_double_annotation(factory, ids)
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="LABEL",
        dimension_value="action",
        target_count=5,
    )

    gaps = _gaps(editor, ids.project_a, dimension_type="LABEL")
    action = _item(gaps, "LABEL", "action")
    assert _sources(action)["ANNOTATION_CONFLICT"] == 2
    # ``segment`` 不是既有缺口键：两份提交的分歧都必须进入未映射，不得丢弃。
    unmapped = _unmapped(gaps)
    assert unmapped[("ANNOTATION_CONFLICT", "VALUE_NOT_FOUND")] == 2


# --------------------------------------------------------------------------- #
# 项目隔离与权限
# --------------------------------------------------------------------------- #
def _seed_project_b_evaluation(factory, ids: ApiIds) -> None:
    owner = _client(factory, ids.owner_a, FakeInspector(128))
    model = _create_model(owner, ids.project_b, ids.owner_a)
    version = _ready_version(owner, ids.project_b, model["model_id"])
    with factory() as session:
        row = EvaluationRow(
            id=uuid4(),
            project_id=ids.project_b,
            model_version_id=UUID(str(version["model_version_id"])),
            dataset_version_id=uuid4(),
            # 初始状态受状态机守卫约束（QUEUED → SUCCEEDED），不能直接插入终态。
            status="QUEUED",
            metrics={},
            thresholds={},
            verdict="UNKNOWN",
            report_ref=None,
            failure_cases=[
                {
                    "id": "fc-b",
                    "category": "OTHER",
                    "severity": "MINOR",
                    "suggested_gap": "kitchen",
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


def test_gap_evidence_is_project_scoped_and_requires_membership(api_db) -> None:
    factory, ids = api_db
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="SCENE",
        dimension_value="kitchen",
    )
    _seed_project_b_evaluation(factory, ids)

    # 项目 A 只能看到自己的证据：B 的失败用例与低分不得出现。
    gaps_a = _gaps(editor, ids.project_a)
    sources_a = _sources(_item(gaps_a, "SCENE", "kitchen"))
    assert "FAILURE_CASE" not in sources_a
    unmapped_a = _unmapped(gaps_a)
    assert not any(source in {"FAILURE_CASE", "LOW_SCORE"} for source, _ in unmapped_a)

    # 项目 B 的证据确实存在，只是被隔离在 B：提示值 kitchen 在 B 没有既有键，
    # 因此以"未映射"呈现，而不是被丢弃或跨项目挂到 A。
    owner_b = _client(factory, ids.owner_a, FakeInspector(128))
    gaps_b = _gaps(owner_b, ids.project_b)
    unmapped_b = _unmapped(gaps_b)
    assert unmapped_b[("LOW_SCORE", "NO_DIMENSION")] == 1
    assert unmapped_b[("FAILURE_CASE", "VALUE_NOT_FOUND")] == 1

    viewer = _client(factory, ids.viewer_a, FakeInspector(128))
    assert viewer.get(url(ids.project_a, "/data-gaps")).status_code == 200
    outsider = _client(factory, ids.outsider, FakeInspector(128))
    forbidden = outsider.get(url(ids.project_a, "/data-gaps"))
    assert forbidden.status_code in {403, 404}
