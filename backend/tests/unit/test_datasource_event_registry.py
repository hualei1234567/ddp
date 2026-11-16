"""数据源导入域事件映射与状态机动作必须同集合。

``DATASOURCE_IMPORT_ACTION_EVENTS`` 的键是「动作名」，动作合法集合由
``domain/datasources/state_machines`` 说了算。两处一旦漂移，表现是：
新增动作能通过状态机校验，但走到 ``record_change`` 时 ``KeyError``——
发生在事务里，是最难查的一类故障。

动作分成两类（见 ``state_machines`` 的 ``_IMPORT_ACTIONS`` /
``_SYSTEM_ACTIONS``）：

- **客户端动作**：``start``/``succeed``/``fail``/``cancel``，经
  ``validate_import_action`` 校验，必须与契约 ``ImportAction`` 枚举一致，
  且会出现在 ``allowed_actions`` 里供前端渲染按钮；
- **系统动作**：``retry``，只由 Worker 调用，客户端触发不了。

事件映射必须覆盖**两类**（Worker 也要留痕），但"状态机认不认"要分别验：
客户端动作走 ``validate_import_action``，系统动作走 ``import_target_status``。
"""

from __future__ import annotations

import pytest
from app.application.datasources.events import DATASOURCE_IMPORT_ACTION_EVENTS
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import DataSourceDomainError
from app.domain.datasources.state_machines import (
    allowed_import_actions,
    import_target_status,
    is_system_action,
    validate_import_action,
)

# 契约 ImportAction 枚举（schemas.stage7.yaml）——客户端动作的唯一权威清单。
CONTRACT_CLIENT_ACTIONS = frozenset({"start", "succeed", "fail", "cancel"})


def test_client_actions_match_contract_enum() -> None:
    """``validate_import_action`` 接受的动作必须与契约枚举逐字一致。

    这是本文件最重要的一条：往状态机加客户端动作而不改契约，会让前端
    拿到一个文档里没有的动作名。
    """
    accepted = {
        name
        for name in ("start", "succeed", "fail", "cancel", "retry", "resume", "abort")
        if _accepted(name)
    }
    assert accepted == CONTRACT_CLIENT_ACTIONS


def test_system_actions_are_not_client_actions() -> None:
    """系统动作不得通过 ``validate_import_action``，否则等于开放给客户端。"""
    assert is_system_action("retry") is True
    for status in ImportStatus:
        assert "retry" not in allowed_import_actions(status)


def test_mapping_covers_every_action_the_state_machine_accepts() -> None:
    """状态机接受的每个动作（客户端 + 系统），事件映射里都必须有对应事件。"""
    for name in DATASOURCE_IMPORT_ACTION_EVENTS:
        if is_system_action(name):
            # 系统动作不需要出现在 allowed_actions；只要某状态下能迁得动即可。
            assert any(
                _can_transition(status, name) for status in ImportStatus
            ), f"系统动作 {name} 在任何状态下都不可迁移"
            continue
        assert validate_import_action(name) == name


def test_mapping_has_no_unknown_action() -> None:
    """反过来：映射表里的每个键都必须是状态机认的动作，不能凭空多出来。"""
    unknown: list[str] = []
    for name in DATASOURCE_IMPORT_ACTION_EVENTS:
        if is_system_action(name):
            continue
        try:
            validate_import_action(name)
        except DataSourceDomainError:
            unknown.append(name)
    assert unknown == [], f"事件映射里有状态机不认的动作：{unknown}"


def test_mapping_keys_equal_client_actions_plus_system_actions() -> None:
    """键集合必须恰好是「契约客户端动作 ∪ 系统动作」，不多不少。

    防止有人把某个动作只写进映射表却没进状态机（或反之），导致
    "能产生事件但状态迁不动"这类半成品。
    """
    system_actions = {name for name in DATASOURCE_IMPORT_ACTION_EVENTS if is_system_action(name)}
    assert set(DATASOURCE_IMPORT_ACTION_EVENTS) == CONTRACT_CLIENT_ACTIONS | system_actions


def test_every_event_name_is_fully_qualified() -> None:
    """事件名必须是 ``datasource.import.<action>`` 形态，不能只写动作名。

    AST 提取脚本按 ``domain.action`` 形态过滤；只写 ``start`` 会提取不到，
    等于绕过了归类校验。
    """
    for action, event in DATASOURCE_IMPORT_ACTION_EVENTS.items():
        assert event == f"datasource.import.{action}", (action, event)


@pytest.mark.parametrize("action", sorted(DATASOURCE_IMPORT_ACTION_EVENTS))
def test_event_lookup_never_raises(action: str) -> None:
    """每个动作都能查到事件名——``record_change`` 里的查表不会 KeyError。"""
    assert DATASOURCE_IMPORT_ACTION_EVENTS[action]


def _accepted(name: str) -> bool:
    try:
        validate_import_action(name)
    except DataSourceDomainError:
        return False
    return True


def _can_transition(status: ImportStatus, action: str) -> bool:
    try:
        import_target_status(status, action)
    except DataSourceDomainError:
        return False
    return True
