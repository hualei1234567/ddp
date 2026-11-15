"""标注域三个列表端点的游标分页（D1 / D2）。

覆盖点：
- ``/annotation-submissions``：超过 ``limit`` 能翻页、末页 ``next_cursor`` 为 None、
  翻页不重复不遗漏；**他人 DRAFT 混在其中的翻页**（可见性必须下推到查询，
  否则他人草稿占名额会出现「页内条数不足却仍有下一页」）。
- ``/quality-samples`` 与 ``/iaa-reports``：同样翻页、末页收尾、非法游标 422。

数据播种见 ``annotation_pagination_support``。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_pagination_quality_support import (
    _seed_iaa_reports,
    _seed_passed_quality_sample,
    _seed_quality_samples,
    _seed_submission_for_quality,
    _walk_pages,
)
from tests.api.annotation_pagination_support import (
    _add_submission,
    _seed_batch_and_assignment,
    _seed_submissions,
    _seed_work_chain,
    url,
)
from tests.api.conftest import ApiIds, make_client

# --------------------------------------------------------------------------- #
# /annotation-submissions
# --------------------------------------------------------------------------- #


def test_submissions_cursor_pagination_has_no_gap_or_duplicate(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """25 条已提交提交按 limit=10 翻页：3 页、末页 next_cursor 为 None、无重无漏。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    chain = _seed_work_chain(factory, project_id=ids.project_a, actor_id=ids.editor_a, code="pg")
    seeded = _seed_submissions(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        chain=chain,
        submitted=25,
    )

    first = client.get(
        url(ids.project_a, "/annotation-submissions"),
        params={"task_id": str(chain["task_id"]), "limit": 10},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 10
    assert first.json()["next_cursor"]

    collected = _walk_pages(
        client,
        ids.project_a,
        "/annotation-submissions",
        limit=10,
        id_key="submission_id",
        extra_params={"task_id": str(chain["task_id"])},
    )
    assert len(collected) == 25
    assert len(set(collected)) == 25, "翻页出现重复项"
    assert set(collected) == {str(item) for item in seeded["submitted"]}, "翻页遗漏了提交"


def test_submissions_pagination_skips_foreign_drafts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """他人 DRAFT 混在结果中时，翻页仍要满页且不重复不遗漏。

    可见性如果先取 ``limit+1`` 再在内存过滤，他人草稿会占掉名额，
    导致某些页条数不足（甚至为空）却仍有下一页。这里用 3 条本人提交
    + 7 条他人草稿（ID 交错）明确构造这种场景。
    """
    factory, ids = api_db
    viewer = make_client(factory, ids.editor_a)
    chain = _seed_work_chain(factory, project_id=ids.project_a, actor_id=ids.editor_a, code="mix")

    # 同一任务下再建一条属于 owner 的批次/分配，用于承载 owner 的 DRAFT
    # （提交表要求 project+task+batch 三者一致，不能跨任务复用批次）。
    foreign_batch_id, foreign_assignment_id = uuid4(), uuid4()
    with factory() as session:
        _seed_batch_and_assignment(
            session,
            project_id=ids.project_a,
            actor_id=ids.owner_a,
            task_id=chain["task_id"],
            batch_id=foreign_batch_id,
            assignment_id=foreign_assignment_id,
            assignee_id=ids.owner_a,
            code="batch-owner-mix",
            sequence_no=2,
        )
        session.commit()

    with factory() as session:
        mine: list[UUID] = []
        for index in range(3):
            mine.append(
                _add_submission(
                    session,
                    project_id=ids.project_a,
                    task_id=chain["task_id"],
                    batch_id=chain["batch_id"],
                    assignment_id=chain["assignment_id"],
                    annotator_id=ids.editor_a,
                    schema_id=chain["schema_id"],
                    status="SUBMITTED",
                    submission_version=index + 1,
                )
            )
        foreign: list[UUID] = []
        for index in range(7):
            foreign.append(
                _add_submission(
                    session,
                    project_id=ids.project_a,
                    task_id=chain["task_id"],
                    batch_id=foreign_batch_id,
                    assignment_id=foreign_assignment_id,
                    annotator_id=ids.owner_a,
                    schema_id=chain["schema_id"],
                    status="DRAFT",
                    submission_version=index + 1,
                )
            )
        session.commit()

    # 单页 2 条：若过滤在分页之后，第 2 页很可能只返回 1 条或 0 条却仍有下一页。
    collected = _walk_pages(
        viewer,
        ids.project_a,
        "/annotation-submissions",
        limit=2,
        id_key="submission_id",
        extra_params={"task_id": str(chain["task_id"])},
    )
    assert collected == [
        str(item) for item in sorted(mine)
    ], "本人提交的翻页结果应与按 ID 升序的完整集合一致"
    assert not set(collected) & {str(item) for item in foreign}, "他人草稿不得出现在列表里"

    # 每一页都必须恰好 2 条（末页除外），否则说明过滤发生在分页之后。
    cursor: str | None = None
    sizes: list[int] = []
    for _ in range(10):
        params: dict[str, str] = {"task_id": str(chain["task_id"]), "limit": "2"}
        if cursor is not None:
            params["cursor"] = cursor
        response = viewer.get(url(ids.project_a, "/annotation-submissions"), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        sizes.append(len(body["items"]))
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sizes == [2, 1], f"页大小序列应为 [2, 1]，实际 {sizes}"


def test_submissions_pagination_rejects_invalid_cursor(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    chain = _seed_work_chain(factory, project_id=ids.project_a, actor_id=ids.editor_a, code="bad")

    response = client.get(
        url(ids.project_a, "/annotation-submissions"),
        params={"task_id": str(chain["task_id"]), "cursor": "not-base64"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_quality_samples_cursor_pagination(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)
    chain = _seed_work_chain(factory, project_id=ids.project_a, actor_id=ids.owner_a, code="qs")
    submission_id = _seed_submission_for_quality(
        factory, project_id=ids.project_a, actor_id=ids.owner_a, chain=chain
    )
    seeded = _seed_quality_samples(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        chain=chain,
        submission_id=submission_id,
        count=7,
    )

    first = client.get(
        url(ids.project_a, "/quality-samples"),
        params={"task_id": str(chain["task_id"]), "limit": 3},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 3
    assert first.json()["next_cursor"]

    collected = _walk_pages(
        client,
        ids.project_a,
        "/quality-samples",
        limit=3,
        id_key="quality_sample_id",
        extra_params={"task_id": str(chain["task_id"])},
    )
    assert len(collected) == 7
    assert len(set(collected)) == 7
    assert collected == [
        str(item) for item in sorted(seeded)
    ], "抽检列表按 id 升序分页，翻页结果应与完整集合一致"


def test_quality_samples_status_filter_combines_with_cursor(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``status`` 与游标同时生效：不同状态的抽检互不串页。"""
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)
    chain = _seed_work_chain(factory, project_id=ids.project_a, actor_id=ids.owner_a, code="qsf")
    submission_id = _seed_submission_for_quality(
        factory, project_id=ids.project_a, actor_id=ids.owner_a, chain=chain
    )
    planned = _seed_quality_samples(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        chain=chain,
        submission_id=submission_id,
        count=4,
    )
    passed_id = _seed_passed_quality_sample(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        chain=chain,
        submission_id=submission_id,
    )

    collected = _walk_pages(
        client,
        ids.project_a,
        "/quality-samples",
        limit=3,
        id_key="quality_sample_id",
        extra_params={"task_id": str(chain["task_id"]), "status": "PLANNED"},
    )
    assert collected == [str(item) for item in sorted(planned)]
    assert str(passed_id) not in collected


# --------------------------------------------------------------------------- #
# /iaa-reports
# --------------------------------------------------------------------------- #


def test_iaa_reports_cursor_pagination(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """IAA 列表沿用 created_at 倒序，游标必须在倒序下不重不漏。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    chain = _seed_work_chain(factory, project_id=ids.project_a, actor_id=ids.editor_a, code="iaa")
    seeded = _seed_iaa_reports(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        chain=chain,
        count=6,
    )

    first = client.get(
        url(ids.project_a, "/iaa-reports"),
        params={"task_id": str(chain["task_id"]), "limit": 4},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 4
    assert first.json()["next_cursor"]

    collected = _walk_pages(
        client,
        ids.project_a,
        "/iaa-reports",
        limit=4,
        id_key="report_id",
        extra_params={"task_id": str(chain["task_id"])},
    )
    assert len(collected) == 6
    assert len(set(collected)) == 6
    assert set(collected) == {str(item) for item in seeded}
    # 倒序：最新一条（最后播种）必须出现在第一页首位。
    assert collected[0] == str(seeded[-1])


def test_iaa_reports_pagination_handles_identical_timestamps(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``created_at`` 全部相同时，倒序游标必须靠 ``id`` 兜底，不重不漏。

    这是复合键集的关键场景：只用 ``created_at`` 比较会在并列时漏页或重复。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    chain = _seed_work_chain(
        factory, project_id=ids.project_a, actor_id=ids.editor_a, code="iaatie"
    )
    seeded = _seed_iaa_reports(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        chain=chain,
        count=5,
        same_created_at=True,
    )

    collected = _walk_pages(
        client,
        ids.project_a,
        "/iaa-reports",
        limit=2,
        id_key="report_id",
        extra_params={"task_id": str(chain["task_id"])},
    )
    assert len(collected) == 5
    assert len(set(collected)) == 5
    # 同时间戳下按 id 倒序，结果必须与按 UUID 倒序的完整集合一致。
    assert collected == [str(item) for item in sorted(seeded, reverse=True)]
