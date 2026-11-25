"""回流判定领域纯函数测试（V52-030）。

重点：三层幂等的前两层（批次内去重、库内既有键）、限流窗口、优先级映射、
payload 解析与不变量。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.annotation.backflow import (
    BackflowDecisionKind,
    BackflowStatus,
    classify_batch,
    map_priority,
    parse_case,
)
from app.domain.annotation.enums import TaskPriority
from app.domain.annotation.errors import DomainValidationError

_NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)
_PROJECT = UUID("11111111-1111-1111-1111-111111111111")


def _raw(
    ref: str, *, source: str = "eval", capability: str = "tool_use", **extra: object
) -> dict[str, object]:
    raw: dict[str, object] = {
        "source": source,
        "case_ref": ref,
        "capability": capability,
        "payload": {"score": 0.2},
    }
    raw.update(extra)
    return raw


def test_priority_mapping_promotes_on_failure_count() -> None:
    """失败次数越多优先级越高；未知 capability 走默认。"""
    assert map_priority("tool_use", 1) in set(TaskPriority)
    high = map_priority("code_edit", 50)
    low = map_priority("code_edit", 1)
    assert list(TaskPriority).index(high) <= list(TaskPriority).index(low)


def test_batch_dedup_keeps_first_only() -> None:
    """同批次重复 (source, case_ref)：只有第一条受理。"""
    batch, accepted = classify_batch(
        (_raw("c1"), _raw("c1"), _raw("c2")),
        frozenset(),
        {},
        project_id=_PROJECT,
        received_at=_NOW,
    )
    assert batch.accepted == 2
    assert batch.duplicated == 1
    assert [d.decision for d in batch.decisions] == [
        BackflowDecisionKind.CREATE_TASK,
        BackflowDecisionKind.DUPLICATE_SKIPPED,
        BackflowDecisionKind.CREATE_TASK,
    ]
    assert {c.case_ref for c in accepted} == {"c1", "c2"}


def test_existing_refs_dedup() -> None:
    """库内既有键 → DUPLICATE_SKIPPED（幂等：平台重发不报错）。"""
    batch, accepted = classify_batch(
        (_raw("old"), _raw("new")),
        frozenset({("eval", "old")}),
        {},
        project_id=_PROJECT,
        received_at=_NOW,
    )
    assert batch.duplicated == 1
    assert accepted[0].case_ref == "new"


def test_rate_limit_only_affects_that_source() -> None:
    """限流按 source 独立计算；超限只拒绝该 source 的案例。"""
    batch, accepted = classify_batch(
        (_raw("a1", source="s1"), _raw("a2", source="s1"), _raw("b1", source="s2")),
        frozenset(),
        {"s1": 2},  # 窗口内 s1 已达 2
        project_id=_PROJECT,
        received_at=_NOW,
        limit=2,
    )
    assert batch.rate_limited == 2
    assert batch.accepted == 1
    assert accepted[0].source == "s2"


def test_batch_size_cap() -> None:
    """单批超上限直接拒绝（防回调风暴打爆内存）。"""
    with pytest.raises(DomainValidationError):
        classify_batch(
            tuple(_raw(f"c{i}") for i in range(201)),
            frozenset(),
            {},
            project_id=_PROJECT,
            received_at=_NOW,
        )


def test_parse_case_rejects_missing_fields_and_naive_time() -> None:
    """关键字段缺失即校验失败；received_at 必须带时区。"""
    with pytest.raises(DomainValidationError):
        parse_case({"source": "s"}, project_id=_PROJECT, received_at=_NOW)
    with pytest.raises(DomainValidationError):
        parse_case(_raw("x"), project_id=_PROJECT, received_at=datetime(2026, 1, 1))


def test_case_id_is_generated_not_deterministic() -> None:
    """case_id 必须每次不同（禁止用批次序号伪造主键）。"""
    first = parse_case(_raw("x"), project_id=_PROJECT, received_at=_NOW)
    second = parse_case(_raw("x"), project_id=_PROJECT, received_at=_NOW)
    assert first.case_id != second.case_id


def test_status_enum_values_match_db_check() -> None:
    """领域枚举与迁移 CHECK 列表一致（MEM-INC-012 教训）。"""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2] / "migrations/versions/0082_backflow_cases.py"
    ).read_text()
    for status in BackflowStatus:
        assert f"'{status.value}'" in migration
