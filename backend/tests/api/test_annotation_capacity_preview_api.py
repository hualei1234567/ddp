"""标注分配前容量预览（FR-020）。

FR-020 的缺口是「分配前的实时负载提示未接」：``workload()`` 与
``CapacityPolicy`` 硬阻断都在，但分配抽屉打开时操作者看不到被分配人当前
已有多少活，只能提交后等 409。

本端点填这个缺口，并锁定四条不能被实现者随意改动的语义：

1. **同源判定**：预览的 ``will_exceed`` 必须与提交时的 409 一一对应
   （两边都用 ``CapacityPolicy.exceeded``），否则提示会骗人；
2. **只读**：用 ``ReadScope``，viewer 也能预览；预览不写库、不推进版本；
3. **不把缺失当 0**：上限未配置时 ``max_*``/比率都是 ``null``，
   ``will_exceed`` 为 false —— "没配上限"不等于"容量为 0"；
4. **批量**：多人一次请求、一条聚合查询（传多人不得变成 N+1）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client

PREVIEW = "/annotation-assignment-capacity-preview"


def _harden(*, items: int) -> Settings:
    """收紧容量上限；分钟上限保持默认（2400），避免两条上限互相掩盖。"""
    return Settings(
        env="local",
        annotation_max_open_items=items,
        annotation_max_planned_minutes=2400,
    )


def _work(factory, ids: ApiIds, *, code: str = "task-cap-preview") -> dict[str, object]:
    """准备一条已开始的分配：editor 身上有 1 个进行中项、30 分钟计划。"""
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    return create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        code,
    )


def _preview(
    factory,
    ids: ApiIds,
    *,
    assignee_ids: list[object],
    planned_item_count: int = 0,
    planned_minutes: int = 0,
    actor_id: object | None = None,
    settings: Settings | None = None,
):
    client = make_client(
        factory,
        ids.editor_a if actor_id is None else actor_id,
        settings_override=settings,
    )
    return client.post(
        url(ids.project_a, PREVIEW),
        json={
            "assignee_ids": [str(item) for item in assignee_ids],
            "planned_item_count": planned_item_count,
            "planned_minutes": planned_minutes,
        },
    )


def test_preview_single_assignee_reports_existing_load(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """单人：返回该人员当前进行中的项数/工时，以及本次分配后的投影。"""
    factory, ids = api_db
    _work(factory, ids)

    response = _preview(
        factory,
        ids,
        assignee_ids=[ids.editor_a],
        planned_item_count=2,
        planned_minutes=90,
        settings=_harden(items=10),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    item = body[0]
    assert item["assignee_id"] == str(ids.editor_a)
    # create_ready_work 落下 1 项 / 30 分钟，且分配仍在进行中。
    assert item["open_item_count"] == 1
    assert item["open_planned_minutes"] == 30
    assert item["planned_item_count"] == 2
    assert item["planned_minutes"] == 90
    assert item["projected_item_count"] == 3
    assert item["projected_planned_minutes"] == 120
    assert item["max_open_items"] == 10
    assert item["will_exceed"] is False
    assert item["status"] == "NORMAL"


def test_preview_returns_will_exceed_matching_submit_rejection(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同源：预览说会超，提交就必须 409；预览说没事，提交就必须成功。

    这条是 FR-020 的核心红线——提示与实际阻断不能用两套公式。
    """
    factory, ids = api_db
    work = _work(factory, ids)
    settings = _harden(items=2)

    # 已占 1 项，再派 2 项 → 投影 3 > 上限 2。
    over = _preview(
        factory,
        ids,
        assignee_ids=[ids.editor_a],
        planned_item_count=2,
        settings=settings,
    )
    assert over.status_code == 200, over.text
    over_item = over.json()[0]
    assert over_item["will_exceed"] is True
    assert over_item["exceeds_open_items"] is True
    assert over_item["status"] == "OVER_CAPACITY"

    rejected = make_client(
        factory,
        ids.editor_a,
        settings_override=settings,
    ).post(
        url(ids.project_a, "/annotation-assignments"),
        json={
            "batch_id": work["batch"]["batch_id"],
            "assignee_id": str(ids.viewer_a),
            "assignment_role": "ANNOTATOR",
            "planned_item_count": 3,
            "planned_minutes": 10,
        },
    )
    # 同一上限口径下为 viewer 预演：viewer 没有既有负载，3 > 2 同样超。
    viewer_preview = _preview(
        factory,
        ids,
        assignee_ids=[ids.viewer_a],
        planned_item_count=3,
        settings=settings,
    )
    assert viewer_preview.json()[0]["will_exceed"] is True
    assert viewer_preview.json()[0]["open_item_count"] == 0
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "annotation_assignment_capacity_exceeded"


def test_preview_multi_assignee_batches_into_one_query(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """多人：一次请求返回全部人员，且聚合只发一条 SQL（禁止 N+1）。"""
    factory, ids = api_db
    _work(factory, ids)
    settings = _harden(items=10)

    with factory() as session:
        engine = session.get_bind()
        statements: list[str] = []

        def _record(conn: object, cursor: object, statement: str, *args: object) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            response = _preview(
                factory,
                ids,
                assignee_ids=[ids.editor_a, ids.owner_a, ids.viewer_a],
                planned_item_count=1,
                settings=settings,
            )
        finally:
            event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["assignee_id"] for item in body] == [
        str(ids.editor_a),
        str(ids.owner_a),
        str(ids.viewer_a),
    ]
    # 顺序按选择顺序保留（前端按它渲染），不被去重打乱。
    aggregates = [item for item in statements if "annotation_assignments" in item]
    assert len(aggregates) == 1, statements
    assert "GROUP BY" in aggregates[0].upper() or "group by" in aggregates[0]

    by_id = {item["assignee_id"]: item for item in body}
    assert by_id[str(ids.editor_a)]["open_item_count"] == 1
    # 没有既有负载的人如实为 0，而不是缺失/省略。
    assert by_id[str(ids.owner_a)]["open_item_count"] == 0
    assert by_id[str(ids.owner_a)]["open_planned_minutes"] == 0


def test_preview_reports_both_limits_with_ratios(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """两条上限同时配置时，比率按"投影值 / 上限"计算。

    比率是给前端画进度条用的；`null`（未配置）与 `0`（配了零容量）在
    领域层严格区分，见 ``tests/unit/test_annotation_workload_capacity.py``
    —— HTTP 层的 Settings 目前不允许把上限配成 null，因此那种形态只能在
    领域层覆盖，不能靠伪造请求绕过去。
    """
    factory, ids = api_db
    _work(factory, ids)
    settings = Settings(
        env="local",
        annotation_max_open_items=10,
        annotation_max_planned_minutes=200,
    )

    response = _preview(
        factory,
        ids,
        assignee_ids=[ids.editor_a],
        planned_item_count=3,
        planned_minutes=70,
        settings=settings,
    )

    assert response.status_code == 200, response.text
    item = response.json()[0]
    # 1 项 / 30 分钟既有 + 3 项 / 70 分钟本次 → 4/10 与 100/200。
    assert item["projected_item_count"] == 4
    assert item["projected_planned_minutes"] == 100
    assert item["item_capacity_ratio"] == 0.4
    assert item["minutes_capacity_ratio"] == 0.5
    assert item["will_exceed"] is False
    assert item["status"] == "NORMAL"


def test_preview_rejects_non_member_and_empty_list(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """非项目成员 404（不成为成员枚举旁路）；空列表 422。"""
    factory, ids = api_db
    _work(factory, ids)
    settings = _harden(items=10)

    outsider = _preview(
        factory,
        ids,
        assignee_ids=[ids.outsider],
        settings=settings,
    )
    assert outsider.status_code == 404, outsider.text
    assert outsider.json()["error"]["code"] == "resource_not_found"

    empty = _preview(factory, ids, assignee_ids=[], settings=settings)
    assert empty.status_code == 422, empty.text


def test_preview_is_read_only_and_open_to_viewer(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """只读：viewer 也能预览；预览不写库、不推进分配版本。"""
    factory, ids = api_db
    work = _work(factory, ids)
    settings = _harden(items=10)
    assignment_id = work["assignment"]["assignment_id"]
    version_before = work["assignment"]["version"]

    viewer_response = _preview(
        factory,
        ids,
        assignee_ids=[ids.editor_a],
        planned_item_count=1,
        actor_id=ids.viewer_a,
        settings=settings,
    )
    assert viewer_response.status_code == 200, viewer_response.text

    after = make_client(factory, ids.owner_a).get(
        url(ids.project_a, f"/annotation-assignments/{assignment_id}")
    )
    assert after.status_code == 200, after.text
    assert after.json()["version"] == version_before


def test_preview_does_not_count_terminal_assignments(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """已撤销的分配不再占用容量：预览占用量必须跟着状态走。"""
    factory, ids = api_db
    work = _work(factory, ids)
    settings = _harden(items=10)
    assignment = work["assignment"]

    revoked = make_client(factory, ids.editor_a).post(
        url(
            ids.project_a,
            f"/annotation-assignments/{assignment['assignment_id']}/actions/revoke",
        ),
        headers={
            "If-Match": f'"{assignment["version"]}"',
            "Idempotency-Key": "cap-preview-revoke-0001",
        },
        json={"reason": "改派预览用例"},
    )
    assert revoked.status_code == 200, revoked.text

    response = _preview(
        factory,
        ids,
        assignee_ids=[ids.editor_a],
        planned_item_count=0,
        settings=settings,
    )

    assert response.status_code == 200, response.text
    item = response.json()[0]
    assert item["open_item_count"] == 0
    assert item["open_planned_minutes"] == 0


def test_preview_same_period_as_workload_uses_assignment_facts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """预览的占用量与 workload 的周期口径是两件事，但用的是同一批分配事实。

    预览回答"现在的进行中负载"，workload 回答"周期内发生过什么"；这里只
    锁定两者都能看到同一条已创建分配，避免预览绕开真实数据自造一套。
    """
    factory, ids = api_db
    _work(factory, ids)
    settings = _harden(items=10)
    now = datetime.now(UTC)
    client = make_client(factory, ids.owner_a, settings_override=settings)

    workload = client.get(
        url(ids.project_a, "/annotation-workload"),
        params={
            "assignee_id": str(ids.editor_a),
            "period_start": (now - timedelta(days=7)).isoformat(),
            "period_end": now.isoformat(),
        },
    )
    assert workload.status_code == 200, workload.text
    assert workload.json()["assigned_item_count"] == 1

    preview = _preview(
        factory,
        ids,
        assignee_ids=[ids.editor_a],
        planned_item_count=1,
        settings=settings,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()[0]["open_item_count"] == 1
