"""数据源领域测试：许可/gated 一致性、revision 可复现性、导入状态机。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.datasources import (
    DataSource,
    DomainValidationError,
    InvalidStateTransitionError,
    allowed_import_actions,
    import_target_status,
)

_SHA = "a" * 40


def _source(**overrides: object) -> DataSource:
    base: dict[str, object] = {
        "data_source_id": uuid4(),
        "project_id": uuid4(),
        "code": "hf-1",
        "name": "示例数据集",
        "kind": "HUGGINGFACE",
        "repo_id": "lerobot/aloha_sim_insertion_human",
        "revision": _SHA,
        "license": "Apache-2.0",
        "access_condition": "PUBLIC",
        "is_gated": False,
    }
    base.update(overrides)
    return DataSource(**base)  # type: ignore[arg-type]


def test_public_source_without_gate_is_accepted() -> None:
    assert _source().access_condition == "PUBLIC"


def test_gated_condition_requires_is_gated_true() -> None:
    """标了 GATED 却没打 gated 标记会让下游以为可直接商用。"""
    with pytest.raises(DomainValidationError, match="is_gated"):
        _source(access_condition="GATED", is_gated=False)


def test_is_gated_true_cannot_be_public() -> None:
    with pytest.raises(DomainValidationError, match="PUBLIC"):
        _source(access_condition="PUBLIC", is_gated=True)


def test_gated_condition_with_flag_is_accepted() -> None:
    assert _source(access_condition="GATED", is_gated=True).is_gated is True


def test_blank_code_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="code"):
        _source(code="   ")


def test_license_is_required() -> None:
    """没有许可证就无法判断可用性；领域层不允许空字符串。"""
    with pytest.raises(DomainValidationError, match="license"):
        _source(license="")


def test_row_version_must_be_positive() -> None:
    with pytest.raises(DomainValidationError, match="row_version"):
        _source(row_version=0)


# --- 导入状态机 -------------------------------------------------------------


def test_pending_allows_start_and_cancel() -> None:
    assert allowed_import_actions("PENDING") == frozenset({"start", "cancel"})


def test_succeeded_is_terminal() -> None:
    assert allowed_import_actions("SUCCEEDED") == frozenset()


@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_terminal_import_rejects_any_action(terminal: str) -> None:
    with pytest.raises(InvalidStateTransitionError):
        import_target_status(terminal, "start")


def test_running_can_finish_in_any_of_three_ways() -> None:
    assert import_target_status("RUNNING", "succeed") == "SUCCEEDED"
    assert import_target_status("RUNNING", "fail") == "FAILED"
    assert import_target_status("RUNNING", "cancel") == "CANCELLED"


def test_pending_cannot_succeed_directly() -> None:
    """必须先 start：否则成功时间戳与"导入真的跑过"无法区分。"""
    with pytest.raises(InvalidStateTransitionError):
        import_target_status("PENDING", "succeed")


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="未知导入动作"):
        import_target_status("PENDING", "explode")
