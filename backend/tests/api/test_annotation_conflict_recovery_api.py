"""FR-019 标注冲突恢复：409 必须带出「我基于哪一版、服务端现在是哪一版」。

背景（V4.1 审计 PARTIAL）：If-Match 乐观锁与 409 ``version_conflict`` 早就有了，
但错误体只有一句固定文案。前端要展示「我的版本 vs 服务端最新版本」的差异、
并基于最新版本重新提交，就必须知道两侧版本号——否则恢复流只能退化成
"刷新后重填"，用户输入事实上被丢弃。

本文件只断言**协议层事实**：

1. 提交动作使用过期 If-Match → 409 + ``details.expected_version`` +
   ``details.current_version``，且两者等于真实版本；
2. 追平版本后同一份内容可以重新提交成功（恢复闭环真的能走通，而不是
   只有错误码正确）；
3. details 只含版本号，不回显业务内容/内部标识（契约 details_rule）。

"合并"与"复制草稿"是用户显式操作，属于前端职责（见
``apps/web/src/services/annotationConflict.ts`` 与其 vitest 用例）；这里不
声称服务端会自动合并任何内容。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    record_payload,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def _draft_submission(
    editor,
    project_id: UUID,
    assignment_id: object,
    episode_id: UUID,
    *,
    action: str = "PICK",
) -> dict[str, object]:
    created = editor.post(
        url(project_id, "/annotation-submissions"),
        json={
            "assignment_id": str(assignment_id),
            "records": [record_payload(episode_id, action=action)],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _ready_work(api_db: tuple[sessionmaker[Session], ApiIds]):
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-FR019-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-fr019")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-fr019")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-fr019",
    )
    return ids, editor, work, episode_id


def test_stale_if_match_exposes_both_versions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """过期 If-Match 的 409 必须回显 expected_version 与 current_version。

    版本语义：DRAFT 提交创建后 ``version == 1``；提交动作把它推到 2。
    第二次提交仍用 ``If-Match: "1"`` 就是典型的"他人已更新/我已提交过"场景，
    客户端据此才知道该基于 2 重新合并。
    """
    ids, editor, work, episode_id = _ready_work(api_db)
    draft = _draft_submission(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    submission_id = draft["submission_id"]
    assert draft["version"] == 1

    first = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2

    stale = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert stale.status_code == 409, stale.text
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    # FR-019 的核心断言：两侧版本都要有，且与真实状态一致。
    assert error["details"]["expected_version"] == 1
    assert error["details"]["current_version"] == 2


def test_conflict_details_do_not_leak_business_fields(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """details 只含版本号：不得回显标签值、备注或内部状态机细节。

    契约 details_rule 允许 expected/actual 这类请求方本就知道的数值；把
    提交内容塞进错误体会让"错误响应"变成一条额外的数据外泄通道。
    """
    ids, editor, work, episode_id = _ready_work(api_db)
    draft = _draft_submission(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
        action="PLACE",
    )
    submission_id = draft["submission_id"]
    editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )

    stale = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert stale.status_code == 409, stale.text
    details = stale.json()["error"]["details"]
    assert set(details) == {"expected_version", "current_version"}
    body = stale.text
    # 用户输入与内部标识都不得出现在错误体里。
    assert "PLACE" not in body
    assert "segment" not in body


def test_recovering_with_current_version_succeeds(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """按 409 给出的 current_version 重新提交必须成功（恢复闭环可走通）。

    这正是"追平版本后重提交"这一步：客户端拿着过期的 If-Match 提交 →
    服务端 409 并给出 current_version → 客户端用最新版本号重放同一动作。
    若服务端在这条路径上还要求其它隐藏条件，恢复流就是假的。
    """
    ids, editor, work, episode_id = _ready_work(api_db)
    draft = _draft_submission(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    submission_id = draft["submission_id"]
    path = url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/submit")

    # 客户端手里是过期版本（7），服务端实际是 1。
    stale = editor.post(path, headers={"If-Match": '"7"'}, json={})
    assert stale.status_code == 409, stale.text
    current_version = stale.json()["error"]["details"]["current_version"]
    assert current_version == 1

    # 用 409 给出的真实版本重放同一动作 → 成功，状态与版本都前进。
    recovered = editor.post(
        path,
        headers={"If-Match": f'"{current_version}"'},
        json={},
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "SUBMITTED"
    assert recovered.json()["version"] == current_version + 1


def test_conflict_expected_version_echoes_client_value(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """expected_version 必须回显客户端实际发送的 If-Match（不是猜测值）。

    客户端要靠这个数字判断"我手上的稿子是哪一版"，回显成别的数会让合并
    基准错位。
    """
    ids, editor, work, episode_id = _ready_work(api_db)
    draft = _draft_submission(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    submission_id = draft["submission_id"]

    # 送一个明显不存在的"未来版本"，服务端当前仍是 1。
    stale = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/withdraw"),
        headers={"If-Match": '"7"'},
        json={},
    )
    assert stale.status_code == 409, stale.text
    details = stale.json()["error"]["details"]
    assert details["expected_version"] == 7
    assert details["current_version"] == 1
