"""N7-003 领域校验：结构化失败用例的分类、上限与归一化。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.failure_cases import (
    MAX_FAILURE_CASES,
    MAX_FAILURE_CASES_BYTES,
    FailureCase,
    parse_failure_cases,
)


def _case(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "fc-1",
        "category": "PERCEPTION",
        "severity": "MAJOR",
        "episode_key": "ep-001",
        "episode_id": None,
        "evidence": {"frame": 42, "object": "cup"},
        "metric": "grasp_success",
        "observed": 0,
        "expected": 1,
        "suggested_gap": "kitchen/cup",
    }
    payload.update(overrides)
    return payload


def test_valid_case_is_normalized_and_serialized() -> None:
    episode_id = uuid4()
    cases = parse_failure_cases(
        [
            _case(
                episode_id=str(episode_id),
                observed=0,
                expected="success",
                evidence="frame 42: cup dropped",
            )
        ]
    )

    assert len(cases) == 1
    case = cases[0]
    assert isinstance(case, FailureCase)
    assert case.episode_id == episode_id
    assert case.observed == 0.0
    assert case.expected == "success"
    assert case.to_dict()["category"] == "PERCEPTION"
    assert case.to_dict()["episode_id"] == str(episode_id)


def test_none_and_empty_mean_no_reported_cases() -> None:
    assert parse_failure_cases(None) == []
    assert parse_failure_cases([]) == []


@pytest.mark.parametrize("category", ["UNKNOWN", "perception", ""])
def test_unknown_category_is_rejected(category: str) -> None:
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(category=category)])


def test_unknown_severity_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(severity="CRITICAL")])


def test_too_many_cases_are_rejected() -> None:
    payload = [_case(id=f"fc-{index}") for index in range(MAX_FAILURE_CASES + 1)]
    with pytest.raises(DomainValidationError):
        parse_failure_cases(payload)


def test_total_serialized_size_is_bounded() -> None:
    # 单条 400+ 字节、200 条合计超过 64 KiB；逐条证据仍在上限内。
    payload = [
        _case(id=f"fc-{index}", evidence={"blob": "x" * 400}) for index in range(MAX_FAILURE_CASES)
    ]
    with pytest.raises(DomainValidationError):
        parse_failure_cases(payload)

    assert len(parse_failure_cases(payload[:10])) == 10
    assert MAX_FAILURE_CASES_BYTES == 64 * 1024


def test_oversized_evidence_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(evidence={"blob": "x" * 5000})])
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(evidence="x" * 3000)])
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(evidence={})])


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(), _case()])


def test_unknown_fields_and_bool_scalars_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(unexpected="x")])
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(observed=True)])
    with pytest.raises(DomainValidationError):
        parse_failure_cases([_case(episode_id="not-a-uuid")])
