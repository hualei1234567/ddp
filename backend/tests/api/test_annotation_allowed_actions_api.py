"""标注域响应 ``allowed_actions`` 的接口测试。

前端按规范「动作按钮只按服务端 ``allowed_actions`` 渲染」。本文件逐个资源
验证三件事：

1. 响应里确实有该字段（契约已声明 required）；
2. 取值随状态机变化，而不是常量；
3. 永远不含系统内部动作（``system-*``）——它们由应用层自行发起，
   不应渲染成用户可点的按钮。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_arbiter,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def _actions(payload: dict[str, object]) -> list[str]:
    """取出 allowed_actions 并校验其基本契约。"""
    assert "allowed_actions" in payload
    actions = payload["allowed_actions"]
    assert isinstance(actions, list)
    assert all(isinstance(item, str) for item in actions)
    assert all(not item.startswith("system-") for item in actions)
    return actions


def _ready_work(
    factory: sessionmaker[Session],
    ids: ApiIds,
    code: str,
) -> tuple[UUID, TestClient, TestClient, dict[str, object], dict[str, object]]:
    """搭出 IN_PROGRESS 的任务 + 批次 + 指派，供各资源用例复用。"""
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
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
    )
    return episode_id, editor, owner, schema, work


def test_task_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-TASK-AA")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-task-aa")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-task-aa")

    created = editor.post(
        url(ids.project_a, "/annotation-tasks"),
        json={
            "code": "task-aa",
            "name": "任务 AA",
            "sample_set_id": sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": 1,
            "target_episode_count": 1,
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "DRAFT"
    assert _actions(task) == ["cancel", "submit-review"]

    submitted = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task['task_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "PENDING_REVIEW"
    assert _actions(submitted.json()) == ["approve", "cancel", "return-draft"]

    # 列表与详情共用同一份计算，不能只在某一个入口生效。
    detail = editor.get(url(ids.project_a, f"/annotation-tasks/{task['task_id']}"))
    assert detail.status_code == 200
    assert _actions(detail.json()) == ["approve", "cancel", "return-draft"]
    listed = editor.get(url(ids.project_a, "/annotation-tasks"))
    assert listed.status_code == 200
    assert _actions(listed.json()["items"][0]) == ["approve", "cancel", "return-draft"]

    returned = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task['task_id']}/actions/return-draft"),
        headers={"If-Match": '"2"'},
        json={"reason": "补充目标数量"},
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["status"] == "DRAFT"
    assert _actions(returned.json()) == ["cancel", "submit-review"]
    assert episode_id  # 样本集链路已真实落库，避免未使用变量


def test_batch_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _, editor, _, _, work = _ready_work(factory, ids, "batch-aa")
    batch = work["batch"]
    assert batch["status"] == "IN_PROGRESS"
    assert _actions(batch) == ["block", "cancel", "pause", "submit"]

    paused = editor.post(
        url(ids.project_a, f"/annotation-batches/{batch['batch_id']}/actions/pause"),
        headers={"If-Match": f'"{batch["version"]}"'},
        json={"reason": "等待标注员"},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"
    assert _actions(paused.json()) == ["cancel", "resume", "submit"]

    detail = editor.get(url(ids.project_a, f"/annotation-batches/{batch['batch_id']}"))
    assert detail.status_code == 200
    assert _actions(detail.json()) == ["cancel", "resume", "submit"]


def test_assignment_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, editor, _, _, work = _ready_work(factory, ids, "assign-aa")
    assignment = work["assignment"]
    assert assignment["status"] == "IN_PROGRESS"
    assert _actions(assignment) == ["revoke", "submit"]

    create_submitted(
        editor,
        ids.project_a,
        assignment["assignment_id"],
        episode_id,
    )
    after = editor.get(url(ids.project_a, f"/annotation-assignments/{assignment['assignment_id']}"))
    assert after.status_code == 200, after.text
    assert after.json()["status"] == "SUBMITTED"
    # 系统动作 system-expire 只在租约到期时由应用层发起，绝不出现在这里。
    assert _actions(after.json()) == ["complete", "rework"]


def test_submission_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, editor, _, _, work = _ready_work(factory, ids, "submission-aa")
    created = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": work["assignment"]["assignment_id"],
            "records": [
                {
                    "episode_id": str(episode_id),
                    "episode_version": 1,
                    "labels": [
                        {
                            "label_key": "action",
                            "value": "PICK",
                            "source": "HUMAN",
                            "label_status": "CONFIRMED",
                        }
                    ],
                }
            ],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "DRAFT"
    assert _actions(created.json()) == ["submit", "withdraw"]

    submitted = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{created.json()['submission_id']}/actions/submit",
        ),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "SUBMITTED"
    # 提交资源只对外暴露 submit/withdraw/require-rework；SUBMITTED 下状态机
    # 唯一出边是系统动作 system-review-start，取交集后必须为空。
    assert _actions(submitted.json()) == []


def test_review_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, editor, owner, _, work = _ready_work(factory, ids, "review-aa")
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    created = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert created.status_code == 201, created.text
    review = created.json()
    assert review["status"] == "PENDING"
    assert _actions(review) == ["cancel", "claim"]

    claimed = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review['review_id']}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "IN_REVIEW"
    assert _actions(claimed.json()) == ["approve", "cancel", "escalate", "reject"]


def test_arbitration_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    arbiter_id = seed_arbiter(factory, ids.project_a)
    episode_id, editor, owner, _, work = _ready_work(factory, ids, "arbitration-aa")
    arbiter = make_client(factory, arbiter_id)
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    claimed = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    escalated = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/escalate"),
        headers={"If-Match": '"2"'},
        json={"reason": "意见不一致"},
    )
    assert escalated.status_code == 200, escalated.text

    created = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/arbitrations"),
        json={
            "review_id": review_id,
            "arbiter_ids": [str(arbiter_id)],
            "quorum": 1,
            "trigger": "REVIEWER_DISAGREEMENT",
        },
    )
    assert created.status_code == 201, created.text
    arbitration = created.json()
    assert arbitration["status"] == "PENDING"
    assert _actions(arbitration) == ["cancel", "claim"]

    arbiter_claim = arbiter.post(
        url(
            ids.project_a,
            f"/annotation-arbitrations/{arbitration['arbitration_id']}/actions/claim",
        ),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert arbiter_claim.status_code == 200, arbiter_claim.text
    assert arbiter_claim.json()["status"] == "IN_ARBITRATION"
    assert _actions(arbiter_claim.json()) == [
        "cancel",
        "recuse",
        "resolve-approved",
        "resolve-rejected",
        "resolve-rework",
    ]


def test_gold_set_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-GOLD-AA")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-gold-aa")

    created = editor.post(
        url(ids.project_a, "/gold-sets"),
        json={
            "code": "gold-aa",
            "schema_id": schema["schema_id"],
            "items": [
                {
                    "gold_item_id": str(uuid4()),
                    "episode_id": str(episode_id),
                    "expected_labels": [{"label_key": "action", "value": "PICK"}],
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    gold_set = created.json()
    assert gold_set["status"] == "DRAFT"
    assert _actions(gold_set) == ["revoke", "submit-review"]

    submitted = editor.post(
        url(ids.project_a, f"/gold-sets/{gold_set['gold_set_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "PENDING_REVIEW"
    assert _actions(submitted.json()) == ["activate", "return-draft", "revoke"]

    activated = owner.post(
        url(ids.project_a, f"/gold-sets/{gold_set['gold_set_id']}/actions/activate"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert _actions(activated.json()) == ["retire", "revoke"]

    listed = owner.get(url(ids.project_a, "/gold-sets"))
    assert listed.status_code == 200
    assert _actions(listed.json()["items"][0]) == ["retire", "revoke"]


def test_quality_sample_allowed_actions_follow_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, editor, owner, _, work = _ready_work(factory, ids, "quality-aa")
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    created = owner.post(
        url(ids.project_a, "/quality-samples"),
        json={
            "task_id": work["task"]["task_id"],
            "submission_id": submission["submission_id"],
            "annotator_id": str(ids.editor_a),
            "sampling_policy_version": "quality/1",
            "sample_type": "RANDOM",
            "sample_reason": "例行抽检",
        },
    )
    assert created.status_code == 201, created.text
    sample = created.json()
    sample_id = sample["quality_sample_id"]
    assert sample["status"] == "PLANNED"
    assert _actions(sample) == ["cancel", "sample"]

    sampled = owner.post(
        url(ids.project_a, f"/quality-samples/{sample_id}/actions/sample"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert sampled.status_code == 200, sampled.text
    assert sampled.json()["status"] == "SAMPLED"
    assert _actions(sampled.json()) == ["assign", "cancel"]

    assigned = owner.post(
        url(ids.project_a, f"/quality-samples/{sample_id}/actions/assign"),
        headers={"If-Match": '"2"'},
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "ASSIGNED"
    assert _actions(assigned.json()) == ["cancel", "start"]

    started = owner.post(
        url(ids.project_a, f"/quality-samples/{sample_id}/actions/start"),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "IN_REVIEW"
    assert _actions(started.json()) == ["escalate", "fail", "pass"]

    listed = owner.get(url(ids.project_a, f"/quality-samples?task_id={work['task']['task_id']}"))
    assert listed.status_code == 200
    assert _actions(listed.json()["items"][0]) == ["escalate", "fail", "pass"]
