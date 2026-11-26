"""采集任务列表 N+1 回归测试（V52-035）。

压测基线（docs/testing/evidence/2026-11-26-prod-readonly-perf/README.md）发现
``GET /collection-tasks`` 每行各查 2 次关联（设备/传感器），页大小 N 时是
2N+1 次查询，limit 50 时延迟达 87.7ms。修复后列表固定为 3 次查询（任务行 +
设备批量 + 传感器批量），**与页大小无关**。

本测试用 SQLAlchemy ``before_cursor_execute`` 事件计数真实执行 SQL，
不 mock 仓储、不断言 SQL 文本，只断言"查询次数与页大小无关"这一行为契约。
任务不挂设备/传感器也可测出 N+1——修复前每行**无条件**各查 2 次。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.collection.models import CollectionTask
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.collection_tasks import SqlCollectionTaskRepository
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds

# 修复后固定 3 次查询；留 1 条余量容忍会话/实现细节（如 count 查询），
# 但上限必须与页大小无关。修复前 20 行会产生 41 次。
_MAX_QUERIES_WITHOUT_NPLUS1 = 4


def _seed_tasks(session: Session, scope: ProjectScope, count: int) -> None:
    """写入 count 个采集任务（不挂关联，聚焦列表查询路径本身）。"""
    repo = SqlCollectionTaskRepository(session)
    for i in range(count):
        repo.add(
            scope,
            CollectionTask(
                task_id=uuid4(),
                project_id=scope.project_id,
                code=f"nplus1-{i}",
                name=f"任务 {i}",
            ),
        )
    session.commit()


def _count_queries(engine, callable_under_test) -> int:
    """执行一次被测调用，返回期间真实执行的 SQL 语句数。"""
    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        callable_under_test()
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    return counter["n"]


@pytest.mark.parametrize("page_size", [1, 5, 20])
def test_list_query_count_is_constant_across_page_sizes(
    api_db: tuple[sessionmaker[Session], ApiIds], page_size: int
) -> None:
    """查询次数不随页大小增长（修复前为 2N+1）。"""
    factory, ids = api_db
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)

    with factory() as session:
        _seed_tasks(session, scope, page_size)

    engine = factory.kw["bind"]

    def _list() -> None:
        with factory() as session:
            rows = SqlCollectionTaskRepository(session).list(scope, limit=page_size)
            # 消费结果，确保关联查询真实发生（惰性加载会掩盖 N+1）
            assert len(rows) == page_size
            for row in rows:
                assert row.assigned_device_ids == ()
                assert row.assigned_sensor_ids == ()

    queries = _count_queries(engine, _list)
    assert queries <= _MAX_QUERIES_WITHOUT_NPLUS1, (
        f"页大小 {page_size} 时查询 {queries} 次，超过上限 "
        f"{_MAX_QUERIES_WITHOUT_NPLUS1}——N+1 回归（修复前 2N+1）"
    )
