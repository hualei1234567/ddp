"""偏好 JSON 约定值写路径 API 集成测试（V52-032）。

覆盖验收点：
- fork 物化返回的 ``preference_json_value`` 可直接作为 JSON 标签值提交，
  落库后能经 ``PreferenceRecord.from_raw`` 被 llm_export 消费（闭环证据）；
- 非法偏好值提交返回 422 且**不部分写入**（多记录提交里一条非法即整体失败）；
- 修正后重新提交成功，证明失败请求没有留下半套数据。
"""

from __future__ import annotations

from uuid import UUID

from app.domain.annotation.prm_dpo import preference_record_kwargs
from ddp_data.llm_export import PreferenceRecord, Provenance, QcStatus, ReviewStatus
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_ready_work,
    create_snapshot_set,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client

_PRIMARY_PATH = "/prm-trajectory-annotations"
_SUBMISSIONS = "/annotation-submissions"

_PREFERENCE_LABEL = {
    "label_key": "preference",
    "label_type": "JSON",
    "required": False,
    "level": "EPISODE",
    "value_spec": {"json_schema_ref": "MEM-DEC-002#preference"},
}


def _schema_payload(code: str) -> dict[str, object]:
    return {
        "code": code,
        "name": f"偏好标签 {code}",
        "annotation_levels": ["EPISODE"],
        "labels": [
            {
                "label_key": "action",
                "label_type": "ENUM",
                "required": True,
                "level": "EPISODE",
                "value_spec": {"enum_values": ["PICK", "PLACE"]},
            },
            _PREFERENCE_LABEL,
        ],
    }


def _publish_preference_schema(editor, owner, project_id: object, code: str) -> dict[str, object]:
    created = editor.post(url(project_id, "/annotation-schemas"), json=_schema_payload(code))
    assert created.status_code == 201, created.text
    schema = created.json()
    submitted = editor.post(
        url(project_id, f"/annotation-schemas/{schema['schema_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    published = owner.post(
        url(project_id, f"/annotation-schemas/{schema['schema_id']}/actions/publish"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert published.status_code == 200, published.text
    return published.json()


def _fork_preference_value(editor, project_id: object, trajectory_id: str) -> dict[str, object]:
    """从 fork 物化响应里取 MEM-DEC-002 JSON 约定值（不走本地构造）。"""
    created = editor.post(
        url(project_id, _PRIMARY_PATH),
        json={
            "trajectory_id": trajectory_id,
            "task_goal": "把杯子从 A 移到 B",
            "step_actions": ["查表", "抓取", "放置"],
            "marks": [{"step_index": 0, "label": "CORRECT"}],
        },
    )
    assert created.status_code == 201, created.text
    forked = editor.post(
        url(project_id, f"{_PRIMARY_PATH}/{trajectory_id}/fork"),
        json={"at_index": 1, "new_actions": ["重抓杯子", "稳稳放置"], "materialize": True},
    )
    assert forked.status_code == 200, forked.text
    assert forked.json()["materialized_annotation_id"]
    return forked.json()["dpo_pair"]["preference_json_value"]


def _record_payload(episode_id: UUID, preference: object | None) -> dict[str, object]:
    labels: list[dict[str, object]] = [
        {
            "label_key": "action",
            "value": "PICK",
            "source": "HUMAN",
            "label_status": "CONFIRMED",
        }
    ]
    if preference is not None:
        labels.append(
            {
                "label_key": "preference",
                "value": preference,
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        )
    return {
        "episode_id": str(episode_id),
        "episode_version": 1,
        "labels": labels,
    }


def _provenance(submission_id: str) -> Provenance:
    return Provenance(
        dataset_version_id="dv-pref-1",
        sample_id="sample-pref-1",
        submission_id=submission_id,
        review_status=ReviewStatus.APPROVED,
        qc_status=QcStatus.PASSED,
        schema_version="1.0.0",
    )


def test_fork_value_submitted_and_consumable_by_llm_export(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """闭环：fork 物化 → preference_json_value 提交落库 → PreferenceRecord.from_raw。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PREF-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    preference_value = _fork_preference_value(editor, ids.project_a, "traj-pref-loop")
    # canonical 语义：winner 是呈现槽，chosen/rejected 已归一。
    assert preference_value["winner"] == "A"
    assert preference_value["chosen"] != preference_value["rejected"]

    schema = _publish_preference_schema(editor, owner, ids.project_a, "schema-pref")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-pref")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-pref",
    )

    created = editor.post(
        url(ids.project_a, _SUBMISSIONS),
        json={
            "assignment_id": str(work["assignment"]["assignment_id"]),
            "records": [_record_payload(episode_id, preference_value)],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    submission = created.json()
    submitted = editor.post(
        url(ids.project_a, f"{_SUBMISSIONS}/{submission['submission_id']}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text

    # 回读提交（仓储读库）：偏好值原样落库在 annotation_records.labels。
    got = editor.get(url(ids.project_a, f"{_SUBMISSIONS}/{submission['submission_id']}"))
    assert got.status_code == 200, got.text
    labels = got.json()["records"][0]["labels"]
    stored = next(label for label in labels if label["label_key"] == "preference")["value"]
    assert stored == preference_value

    preference = PreferenceRecord.from_raw(
        _provenance(str(submission["submission_id"])),
        **preference_record_kwargs(stored, prompt="任务：把杯子从 A 移到 B"),
    )
    assert preference.chosen == preference_value["chosen"]
    assert preference.rejected == preference_value["rejected"]
    assert preference.is_tie is False


def test_invalid_preference_value_is_422_without_partial_write(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """多记录提交里一条偏好值非法：整体 422，不留下任何提交/记录。"""
    factory, ids = api_db
    first_episode = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PREF-2")
    second_episode = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PREF-3")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = _publish_preference_schema(editor, owner, ids.project_a, "schema-pref-bad")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-pref-bad")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        first_episode,
        ids.editor_a,
        "task-pref-bad",
        episode_ids=[first_episode, second_episode],
    )

    # 旧实现的 winner="chosen" 不在 MEM-DEC-002 的 A/B/TIE 约定内。
    legacy = _fork_preference_value(editor, ids.project_a, "traj-pref-bad")
    invalid_value = {**legacy, "winner": "chosen"}
    rejected = editor.post(
        url(ids.project_a, _SUBMISSIONS),
        json={
            "assignment_id": str(work["assignment"]["assignment_id"]),
            "records": [
                _record_payload(first_episode, legacy),
                _record_payload(second_episode, invalid_value),
            ],
            "data_source": "REAL",
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "annotation_schema_value_invalid"

    listed = editor.get(url(ids.project_a, f"{_SUBMISSIONS}?task_id={work['task']['task_id']}"))
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []

    # 修正后重新提交成功：失败请求没有留下半套数据或挡住后续提交。
    fixed = editor.post(
        url(ids.project_a, _SUBMISSIONS),
        json={
            "assignment_id": str(work["assignment"]["assignment_id"]),
            "records": [
                _record_payload(first_episode, legacy),
                _record_payload(second_episode, legacy),
            ],
            "data_source": "REAL",
        },
    )
    assert fixed.status_code == 201, fixed.text
    assert len(fixed.json()["records"]) == 2


def test_missing_required_preference_field_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """缺必填字段（fork_point_index）→ 422，错误码复用标签值非法。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PREF-4")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = _publish_preference_schema(editor, owner, ids.project_a, "schema-pref-miss")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-pref-miss")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-pref-miss",
    )
    partial = _fork_preference_value(editor, ids.project_a, "traj-pref-miss")
    partial.pop("fork_point_index")
    rejected = editor.post(
        url(ids.project_a, _SUBMISSIONS),
        json={
            "assignment_id": str(work["assignment"]["assignment_id"]),
            "records": [_record_payload(episode_id, partial)],
            "data_source": "REAL",
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "annotation_schema_value_invalid"
