"""数据源导入域事件映射与状态机动作白名单必须同集合。

``DATASOURCE_IMPORT_ACTION_EVENTS`` 的键是「动作名」，动作合法集合由
``domain/datasources/state_machines.validate_import_action`` 说了算。两处
一旦漂移，表现是：新增动作能通过状态机校验，但走到 ``record_change`` 时
``KeyError``——发生在事务里，是最难查的一类故障。

因此这里用**行为探针**而不是读私有常量：对一批候选动作名调用
``validate_import_action``，把"被接受"的集合与映射表的键集合对比。
"""

from __future__ import annotations

import pytest
from app.application.datasources.events import DATASOURCE_IMPORT_ACTION_EVENTS
from app.domain.datasources.errors import DataSourceDomainError
from app.domain.datasources.state_machines import validate_import_action


def test_mapping_covers_every_valid_import_action() -> None:
    """状态机接受的每个动作，事件映射里都必须有对应事件。"""
    candidates = set(DATASOURCE_IMPORT_ACTION_EVENTS) | {
        # 覆盖"将来可能新增但当前不合法"的候选，确认映射表不会漏掉合法动作。
        "start",
        "succeed",
        "fail",
        "cancel",
        "retry",
        "resume",
        "abort",
    }
    accepted: set[str] = set()
    for name in sorted(candidates):
        try:
            accepted.add(validate_import_action(name))
        except DataSourceDomainError:
            continue

    missing = accepted - set(DATASOURCE_IMPORT_ACTION_EVENTS)
    assert missing == set(), f"状态机接受但事件映射缺失动作：{sorted(missing)}"


def test_mapping_has_no_unknown_action() -> None:
    """反过来：映射表里的每个键都必须是状态机接受的动作。"""
    unknown: list[str] = []
    for name in DATASOURCE_IMPORT_ACTION_EVENTS:
        try:
            validate_import_action(name)
        except DataSourceDomainError:
            unknown.append(name)

    assert unknown == [], f"事件映射里有状态机不认的动作：{unknown}"


def test_every_event_name_is_fully_qualified() -> None:
    """事件名必须是 ``datasource.import.<action>`` 形态，不能只写动作名。

    AST 提取脚本按 ``domain.action`` 形态过滤；只写 ``start`` 会提取不到，
    等于绕过了归类校验。
    """
    for action, event in DATASOURCE_IMPORT_ACTION_EVENTS.items():
        assert event == f"datasource.import.{action}", (action, event)


@pytest.mark.parametrize("action", sorted(DATASOURCE_IMPORT_ACTION_EVENTS))
def test_event_lookup_never_raises_for_valid_action(action: str) -> None:
    """防止映射表与状态机在同一动作上口径不一致（如大小写、连字符）。"""
    assert DATASOURCE_IMPORT_ACTION_EVENTS[validate_import_action(action)]
