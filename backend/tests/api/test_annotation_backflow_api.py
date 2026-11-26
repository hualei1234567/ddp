"""回流端点测试（V52-030，蓝图 N5 / G5）。

覆盖：受理与逐条判定、幂等重发（DUPLICATE）、限流、项目隔离、权限
（editor 可写、viewer 不可写、owner 可读、外部人 403）、列表视图。
"""

from __future__ import annotations

from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _url(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/annotation-backflow"


def _cases(*refs: str, source: str = "eval-platform") -> list[dict[str, object]]:
    return [
        {
            "source": source,
            "case_ref": ref,
            "capability": "tool_use",
            "failure_summary": f"案例 {ref} 工具调用失败",
            "payload": {"score": 0.1, "ref": ref},
        }
        for ref in refs
    ]


def test_ingest_accepts_and_records(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """首次回调：全部受理，逐条判定 CREATE_TASK，落库为 RECEIVED。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.post(_url(ids.project_a), json={"cases": _cases("c1", "c2")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["accepted"], body["duplicated"], body["rate_limited"]) == (2, 0, 0)
    assert [d["decision"] for d in body["decisions"]] == ["CREATE_TASK", "CREATE_TASK"]
    assert all(d["suggested_priority"] in {"P0", "P1", "P2", "P3"} for d in body["decisions"])

    with factory() as session:
        rows = session.execute(
            sa.text(
                "SELECT source, case_ref, status FROM annotation_backflow_cases "
                "WHERE project_id = :pid ORDER BY case_ref"
            ),
            {"pid": ids.project_a.hex},
        ).all()
    assert [tuple(row) for row in rows] == [
        ("eval-platform", "c1", "RECEIVED"),
        ("eval-platform", "c2", "RECEIVED"),
    ]


def test_repeat_callback_is_idempotent(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同一批重发：不报错，全部判 DUPLICATE_SKIPPED（幂等核心）。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    assert editor.post(_url(ids.project_a), json={"cases": _cases("c1")}).status_code == 200

    again = editor.post(_url(ids.project_a), json={"cases": _cases("c1")})
    assert again.status_code == 200, again.text
    body = again.json()
    assert (body["accepted"], body["duplicated"]) == (0, 1)
    assert body["decisions"][0]["decision"] == "DUPLICATE_SKIPPED"

    # 回归（MEM-INC-023）：跳过判决**不落库**——表上 (source, case_ref) 唯一，
    # 若把重复跳过也写成行，第二次回调会撞唯一键返回 409（生产演练实测）。
    with factory() as session:
        count = session.execute(
            sa.text(
                "SELECT count(*) FROM annotation_backflow_cases "
                "WHERE project_id = :pid AND case_ref = 'c1'"
            ),
            {"pid": ids.project_a.hex},
        ).scalar_one()
    assert count == 1  # 只有一条受理行，重复回调没有制造第二行


def test_rate_limit_rejects_over_quota(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """单 source 超每小时上限：超出部分判 RATE_LIMITED。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    response = editor.post(
        _url(ids.project_a), json={"cases": _cases("r1", "r2", "r3"), "limit": 2}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["accepted"], body["rate_limited"]) == (2, 1)


def test_batch_dedup_within_payload(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同批次内重复 case_ref：只受理第一条。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    body = editor.post(_url(ids.project_a), json={"cases": _cases("d1", "d1")}).json()
    assert (body["accepted"], body["duplicated"]) == (1, 1)


def test_viewer_cannot_ingest_but_owner_can_list(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """写权限：viewer 403；读权限：owner 200、editor 403（运营视图限 owner）。"""
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)
    owner = make_client(factory, ids.owner_a)
    editor = make_client(factory, ids.editor_a)

    assert viewer.post(_url(ids.project_a), json={"cases": _cases("v1")}).status_code == 403
    assert editor.post(_url(ids.project_a), json={"cases": _cases("e1")}).status_code == 200
    assert owner.get(_url(ids.project_a)).status_code == 200
    assert editor.get(_url(ids.project_a)).status_code == 403


def test_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """项目隔离：A 项目受理的案例不出现在 B 项目列表；外部人 404/403。"""
    factory, ids = api_db
    editor_a = make_client(factory, ids.editor_a)
    owner_a = make_client(factory, ids.owner_a)
    outsider = make_client(factory, ids.outsider)

    assert editor_a.post(_url(ids.project_a), json={"cases": _cases("iso1")}).status_code == 200
    # 案例只落在 project_a：project_b 的列表为空
    page_b = owner_a.get(_url(ids.project_b))
    assert page_b.status_code == 200
    assert page_b.json()["items"] == []
    # 外部人既能写也不能读
    assert outsider.post(_url(ids.project_a), json={"cases": _cases("x")}).status_code in {403, 404}
    assert outsider.get(_url(ids.project_a)).status_code in {403, 404}


def test_list_returns_cases_desc(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """列表返回案例明细（含状态与优先级）。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    editor.post(_url(ids.project_a), json={"cases": _cases("l1", "l2")})

    page = owner.get(_url(ids.project_a), params={"limit": 10}).json()
    refs = {item["case_ref"] for item in page["items"]}
    assert refs == {"l1", "l2"}
    assert all(item["status"] == "RECEIVED" for item in page["items"])


def test_invalid_payload_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """缺关键字段：422（不静默丢弃）。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    bad = editor.post(_url(ids.project_a), json={"cases": [{"source": "x"}]})
    assert bad.status_code == 422


@pytest.mark.parametrize("count", [0, 201])
def test_batch_size_bounds(api_db: tuple[sessionmaker[Session], ApiIds], count: int) -> None:
    """批大小边界：0 与 >200 都被契约层拒绝。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    cases = _cases(*[f"b{i}" for i in range(count)])
    response = editor.post(_url(ids.project_a), json={"cases": cases})
    assert response.status_code == 422
