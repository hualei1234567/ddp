"""导入任务重试：失败不得把任务锁死在终态。

**这组测试锁定的是一个曾经真实存在的死锁**

Worker 执行导入失败 -> ``ImportRunner._fail`` 把任务写成 ``FAILED``；
``FAILED`` 按契约是终态（"终态之后任何动作返回 state_transition_invalid"）。
但 Worker 同时已经把 outbox 事件 ``mark_retry`` 排到了下一轮。下一轮
``ImportRunner.execute`` 要求任务状态为 ``PENDING``，状态机也拒绝
``FAILED -> RUNNING``：

    一次网络抖动 -> 任务 FAILED（终态） -> 事件反复重试 -> 全部必败 -> 死信

结果是**任何一次可恢复的失败都会永久丢掉这条导入**，且没有任何客户端 API 能救。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import DomainValidationError, InvalidStateTransitionError
from app.domain.datasources.models import DataSourceImport
from app.domain.datasources.state_machines import (
    allowed_import_actions,
    import_target_status,
    is_system_action,
    validate_import_action,
)


def _import(**overrides: object) -> DataSourceImport:
    base: dict[str, object] = {
        "import_id": uuid4(),
        "project_id": uuid4(),
        "data_source_id": uuid4(),
        "status": ImportStatus.FAILED,
        "scope": "SAMPLE",
        "tool_name": "huggingface_hub",
        "tool_version": "0.27.1",
        "sample_limit": 10,
    }
    base.update(overrides)
    return DataSourceImport(**base)  # type: ignore[arg-type]


def test_failed_can_be_released_back_to_pending() -> None:
    """系统动作 retry：FAILED -> PENDING，这是解除死锁的那一步。"""
    assert import_target_status(ImportStatus.FAILED, "retry") is ImportStatus.PENDING


def test_retry_is_not_a_client_action() -> None:
    """retry 不得出现在 allowed_actions：前端不该看到"重试"按钮，
    契约 ImportAction 枚举里也没有它。"""
    assert is_system_action("retry") is True
    for status in ImportStatus:
        assert "retry" not in allowed_import_actions(status)


def test_validate_import_action_rejects_system_action() -> None:
    """HTTP 用例入口经 ``validate_import_action`` 过滤：系统动作必须被显式拒绝。

    这是 Worker 重试路径之外的唯一入口；只要这里拒绝，客户端就无权把
    FAILED 任务打回 PENDING，而 Worker 仍可经 ``import_target_status`` 重试。
    """
    with pytest.raises(DomainValidationError) as excinfo:
        validate_import_action("retry")
    assert "系统内部使用" in str(excinfo.value)
    for action in ("start", "succeed", "fail", "cancel"):
        assert validate_import_action(action) == action


def test_client_actions_still_refuse_after_failed() -> None:
    """契约语义不变：FAILED 对**客户端**仍是终态，任何客户端动作都报错。"""
    for action in ("start", "succeed", "fail", "cancel"):
        with pytest.raises(InvalidStateTransitionError):
            import_target_status(ImportStatus.FAILED, action)


def test_succeeded_and_cancelled_are_absolute_terminal() -> None:
    """SUCCEEDED / CANCELLED 连系统重试也不接受——只有 FAILED 例外。"""
    for status in (ImportStatus.SUCCEEDED, ImportStatus.CANCELLED):
        with pytest.raises(InvalidStateTransitionError):
            import_target_status(status, "retry")


def test_can_retry_respects_max_attempts() -> None:
    assert _import(attempt=1, max_attempts=3).can_retry is True
    assert _import(attempt=3, max_attempts=3).can_retry is False


def test_can_retry_requires_failed_status() -> None:
    """只有 FAILED 才谈得上重试；PENDING/RUNNING 不是"重试"而是重新执行。"""
    assert _import(status=ImportStatus.PENDING).can_retry is False
    assert _import(status=ImportStatus.RUNNING).can_retry is False
    assert _import(status=ImportStatus.SUCCEEDED).can_retry is False


def test_attempt_cannot_exceed_max_attempts() -> None:
    with pytest.raises(DomainValidationError) as excinfo:
        _import(attempt=4, max_attempts=3)
    assert "attempt" in str(excinfo.value)


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        import_target_status(ImportStatus.FAILED, "not-an-action")
