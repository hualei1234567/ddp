"""标注域「有异常类但无产生点」错误码的接口级证据。

覆盖 8 个多轮审计遗留码中的 6 个（另两个在领域单测
``test_annotation_domain.py`` 内以纯领域路径覆盖：``annotation_sample_duplicate``
与 ``annotation_task_not_approved``）。每个用例都断言 ``error["code"]`` 与
关键 ``details``，避免只断言状态码而放过错误码漂移。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.episodes import EpisodeModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    NOW,
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def _seed_ineligible_episode(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    code: str,
) -> UUID:
    """造一个被门禁拒绝的 Episode（质量 FAIL 且状态 REJECTED）。"""
    episode_id = uuid4()
    with factory() as session:
        session.add(
            EpisodeModel(
                id=episode_id,
                project_id=project_id,
                episode_lineage_id=uuid4(),
                episode_version=1,
                code=code,
                status="PENDING",
                quality_status="NOT_CHECKED",
                annotation_status="NOT_STARTED",
                scene_labels=["kitchen"],
                start_time_ns=1_000,
                end_time_ns=2_000,
                duration_ns=1_000,
                time_domain="UTC",
                time_base={},
                canonical_schema_version="ddp.canonical/1.0.0-draft",
                pipeline_id="parser",
                pipeline_version="1.0.0",
                segmentation_rule_version="seg-v1",
                calibration_snapshot=[],
                manifest_key=f"canonical/{project_id}/{episode_id}/1/manifest.json",
                manifest_sha256="sha256:" + "b" * 64,
                frame_count=10,
                attempt=1,
                max_attempts=3,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        # Episode 状态机不允许 PENDING 直跳 REJECTED，按真实迁移逐步推进。
        for status, quality in (
            ("PROCESSING", "NOT_CHECKED"),
            ("READY", "NOT_CHECKED"),
            ("QUALITY_CHECKING", "FAIL"),
            ("REJECTED", "FAIL"),
        ):
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status=status, quality_status=quality)
            )
        session.commit()
    return episode_id


def test_explicit_ineligible_episode_returns_contract_code(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:78 —— 显式点名的 Episode 门禁不合格必须 422。

    默认筛选路径是"过滤 + 记录排除理由"；显式点名则不同：样本集内容必须与
    请求一致，静默排除会让人误以为选中了它。
    """
    factory, ids = api_db
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-OK-1")
    rejected = _seed_ineligible_episode(factory, ids.project_a, ids.editor_a, "EP-BAD-1")
    editor = make_client(factory, ids.editor_a)

    created = editor.post(
        url(ids.project_a, "/annotation-sample-sets"),
        json={"code": "sample-explicit", "filters": {}},
    )
    assert created.status_code == 201, created.text
    sample_set_id = created.json()["sample_set_id"]

    previewed = editor.post(
        url(ids.project_a, f"/annotation-sample-sets/{sample_set_id}/actions/preview"),
        headers={"If-Match": '"1"'},
        json={"filters": {"include_episode_ids": [str(rejected)]}},
    )
    assert previewed.status_code == 422, previewed.text
    error = previewed.json()["error"]
    assert error["code"] == "annotation_sample_episode_ineligible"
    rejected_items = error["details"]["rejected_episodes"]
    assert [item["episode_id"] for item in rejected_items] == [str(rejected)]
    assert rejected_items[0]["reason"] == "episode_status_ineligible"
    assert rejected_items[0]["status"] == "REJECTED"
    assert rejected_items[0]["quality_status"] == "FAIL"


def test_cancelled_sample_set_cannot_be_referenced_by_task(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:77 —— 任务引用已取消样本集必须 409。"""
    factory, ids = api_db
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-CANCEL-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    sample_set = create_snapshot_set(editor, ids.project_a, "sample-cancel")
    cancelled = editor.post(
        url(
            ids.project_a,
            f"/annotation-sample-sets/{sample_set['sample_set_id']}/actions/cancel",
        ),
        headers={"If-Match": f'"{sample_set["version"]}"'},
        json={"reason": "改用新样本集"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    schema = create_published_schema(editor, owner, ids.project_a, "schema-cancel")
    created = editor.post(
        url(ids.project_a, "/annotation-tasks"),
        json={
            "code": "task-cancelled-set",
            "name": "引用已取消样本集",
            "sample_set_id": sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": 1,
            "target_episode_count": 1,
        },
    )
    assert created.status_code == 409, created.text
    error = created.json()["error"]
    assert error["code"] == "annotation_sample_set_cancelled"
    assert error["details"]["sample_set_id"] == sample_set["sample_set_id"]
    assert error["details"]["status"] == "CANCELLED"


def test_duplicate_assignment_returns_contract_code(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:91 —— 同批次同人同角色重复分配必须 409。

    数据库唯一约束 ``uq_annotation_assignments_batch_assignee_role`` 会挡住插入，
    但若不先翻译，调用方只会看到笼统的 ``duplicate_resource``。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-DUP-ASSIGN")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-dup-assign")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-dup-assign")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-dup-assign",
    )
    first = work["assignment"]

    duplicate = editor.post(
        url(ids.project_a, "/annotation-assignments"),
        json={
            "batch_id": work["batch"]["batch_id"],
            "assignee_id": str(ids.editor_a),
            "assignment_role": "ANNOTATOR",
            "planned_item_count": 1,
            "planned_minutes": 10,
        },
    )
    assert duplicate.status_code == 409, duplicate.text
    error = duplicate.json()["error"]
    assert error["code"] == "annotation_item_already_assigned"
    assert error["details"]["assignment_id"] == first["assignment_id"]
    assert error["details"]["batch_id"] == work["batch"]["batch_id"]
    assert error["details"]["assignee_id"] == str(ids.editor_a)
    assert error["details"]["assignment_role"] == "ANNOTATOR"


def test_revoked_assignment_returns_gone_contract_code(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:90 —— 撤销终态必须 410 且带专属码。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-REVOKE-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-revoke")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-revoke")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-revoke",
    )
    assignment_id = work["assignment"]["assignment_id"]
    revoked = owner.post(
        url(ids.project_a, f"/annotation-assignments/{assignment_id}/actions/revoke"),
        headers={"If-Match": f'"{work["assignment"]["version"]}"'},
        json={"reason": "人员调整"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"

    rejected = editor.post(
        url(ids.project_a, f"/annotation-assignments/{assignment_id}/actions/claim"),
        headers={"If-Match": f'"{revoked.json()["version"]}"'},
        json={},
    )
    assert rejected.status_code == 410, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "annotation_assignment_revoked"
    assert error["details"]["assignment_id"] == assignment_id
    assert error["details"]["assignment_status"] == "REVOKED"


def test_task_accept_requires_finished_review(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:101 —— 提交未完成复核时不能验收任务。

    与 ``annotation_task_has_open_work`` 的区别：后者是"分配还没提交完"，
    本码是"提交已完成但结论没出"，责任人是复核/仲裁人而不是标注员。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-REVIEW-REQUIRED")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-review-required")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-review-required")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-review-required",
    )
    task_id = work["task"]["task_id"]
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )

    batch_submitted = editor.post(
        url(ids.project_a, f"/annotation-batches/{work['batch']['batch_id']}/actions/submit"),
        headers={"If-Match": f'"{work["batch"]["version"]}"'},
        json={},
    )
    assert batch_submitted.status_code == 200, batch_submitted.text
    task_submitted = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/submit"),
        headers={"If-Match": f'"{work["task"]["version"]}"'},
        json={},
    )
    assert task_submitted.status_code == 200, task_submitted.text
    task_reviewing = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/system-review"),
        headers={"If-Match": f'"{task_submitted.json()["version"]}"'},
        json={},
    )
    assert task_reviewing.status_code == 200, task_reviewing.text

    accepted = owner.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/accept"),
        headers={"If-Match": f'"{task_reviewing.json()["version"]}"'},
        json={},
    )
    assert accepted.status_code == 409, accepted.text
    error = accepted.json()["error"]
    assert error["code"] == "annotation_review_required"
    assert error["details"]["task_id"] == task_id
    pending = error["details"]["pending_submissions"]
    assert [item["submission_id"] for item in pending] == [submission["submission_id"]]
    assert pending[0]["status"] == "SUBMITTED"


def test_decided_submission_cannot_be_reviewed_again(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:96 —— 已终结的提交不能重复决定。

    与 ``state_transition_invalid`` 的区分：非法迁移是"这一状态根本没有该动作"，
    本码是"动作当时合法、结论已定且不可覆盖"，客户端应展示结论而不是重试。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-DECIDED-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-decided")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-decided")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-decided",
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    submission_id = submission["submission_id"]

    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/reviews"),
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
    approved = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/approve"),
        headers={"If-Match": '"2"'},
        json={
            "checklist": [{"key": "labels_correct", "passed": True}],
            "review_duration_sec": 30,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    again = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert again.status_code == 409, again.text
    error = again.json()["error"]
    assert error["code"] == "annotation_submission_already_decided"
    assert error["details"]["submission_id"] == submission_id
    assert error["details"]["status"] == "APPROVED"


def test_task_not_ready_blocks_assignment_creation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """契约 error-codes.v1.json:82 —— 已批准但未就绪的任务不能分配。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-NOT-READY")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-not-ready")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-not-ready")

    created = editor.post(
        url(ids.project_a, "/annotation-tasks"),
        json={
            "code": "task-not-ready",
            "name": "未就绪任务",
            "sample_set_id": sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": 1,
            "target_episode_count": 1,
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    submitted = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    approved = owner.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    batch = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/batches"),
        json={"planned_item_count": 1, "item_refs": [str(episode_id)]},
    )
    assert batch.status_code == 201, batch.text

    assignment = editor.post(
        url(ids.project_a, "/annotation-assignments"),
        json={
            "batch_id": batch.json()["batch_id"],
            "assignee_id": str(ids.editor_a),
            "planned_item_count": 1,
            "planned_minutes": 10,
        },
    )
    assert assignment.status_code == 409, assignment.text
    error = assignment.json()["error"]
    assert error["code"] == "annotation_task_not_ready"
    assert error["details"]["task_id"] == task_id
    assert error["details"]["status"] == "APPROVED"
