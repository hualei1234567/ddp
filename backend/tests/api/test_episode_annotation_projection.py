"""标注状态 → Episode 投影：真实 HTTP 驱动，验证「标注批准后 Episode 变 APPROVED」。

**这条链曾经完全不存在**

``grep annotation_status app/application/annotation/`` 只命中样本集里写死的
``NOT_STARTED``。后果是即使标注全流程批准，Episode 仍是 ``NOT_STARTED``，
N5 数据集选择（只认 ``APPROVED``）会把这些 Episode 全部以
``ANNOTATION_NOT_APPROVED`` 排除。既有 N5 测试靠手工
``UPDATE episodes SET annotation_status='APPROVED'`` 绕过，把断链掩盖了。

本文件断言的是真实行为：走完复核批准后，Episode 的 annotation_status
在库里确实变成 APPROVED。
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_episode,
    url,
)
from tests.api.conftest import make_client


def _episode_annotation_status(factory: sessionmaker[Session], episode_id: UUID) -> str:
    from app.infrastructure.db.models.episodes import EpisodeModel

    with factory() as session:
        return session.scalar(
            select(EpisodeModel.annotation_status).where(EpisodeModel.id == episode_id)
        )


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_approval_projects_approved_to_episode(api_db) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PROJ-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    assert _episode_annotation_status(factory, episode_id) == "NOT_STARTED"

    schema = create_published_schema(editor, owner, ids.project_a, "schema-proj")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-proj")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-proj",
    )
    submission = create_submitted(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    # 提交后应投影为 SUBMITTED。
    assert _episode_annotation_status(factory, episode_id) == "SUBMITTED"

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
    approved = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/approve"),
        headers={"If-Match": '"2"'},
        # 领域要求：通过复核必须提供全部通过的检查项。
        json={"checklist": [{"key": "labels_match", "passed": True, "comment": "逐条核对"}]},
    )
    assert approved.status_code == 200, approved.text

    assert _episode_annotation_status(factory, episode_id) == "APPROVED"


def test_rejection_projects_rejected_to_episode(api_db) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PROJ-2")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-rej")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-rej")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-rej",
    )
    submission = create_submitted(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    review_id = review.json()["review_id"]
    owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    rejected = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/reject"),
        headers={"If-Match": '"2"'},
        # 领域要求：驳回必须填写问题项。
        json={
            "reason": "标注与视频不符",
            "issues": [
                {
                    "code": "LABEL_MISMATCH",
                    "severity": "MAJOR",
                    "message": "第 1 段标签与画面不符",
                }
            ],
        },
    )
    assert rejected.status_code == 200, rejected.text

    assert _episode_annotation_status(factory, episode_id) == "REJECTED"


def test_projection_never_downgrades_approved(api_db) -> None:
    """已 APPROVED 的 Episode 不得被后续驳回覆盖。

    同一 Episode 可能有多份标注提交（返工后重新提交、多标注员并行）。
    先批准的那份结论不应被后来者的驳回冲掉——否则数据集准入会在"其实
    已经有合格标注"的情况下把这批数据排除掉。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PROJ-3")

    # 先手工置为 APPROVED（模拟"已有一份被批准的标注"）。
    from app.infrastructure.db.models.episodes import EpisodeModel

    with factory() as session:
        row = session.get(EpisodeModel, episode_id)
        assert row is not None
        row.annotation_status = "APPROVED"
        session.commit()

    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-keep")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-keep")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-keep",
    )
    create_submitted(editor, ids.project_a, work["assignment"]["assignment_id"], episode_id)

    # 投影只推进不回退：仍应是 APPROVED。
    assert _episode_annotation_status(factory, episode_id) == "APPROVED"
