"""资产目录检索词规则：长度、空白、NUL 与前缀上界。"""

from __future__ import annotations

import pytest
from app.domain.catalog.errors import DomainValidationError
from app.domain.catalog.search import MAX_QUERY_LENGTH, SearchPrefix


@pytest.mark.parametrize(
    ("raw", "value", "upper"),
    [
        ("ep-1", "ep-1", "ep-2"),
        ("  raw-  ", "raw-", "raw."),
        ("a", "a", "b"),
        ("􏿿", "􏿿", None),
    ],
)
def test_parse_strips_and_builds_prefix_bounds(raw: str, value: str, upper: str | None) -> None:
    prefix = SearchPrefix.parse(raw)
    assert prefix.value == value
    assert prefix.upper_bound == upper


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_blank_query_is_rejected(raw: str) -> None:
    with pytest.raises(DomainValidationError):
        SearchPrefix.parse(raw)


def test_nul_and_overlong_query_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        SearchPrefix.parse("a\x00b")
    with pytest.raises(DomainValidationError):
        SearchPrefix.parse("a" * (MAX_QUERY_LENGTH + 1))
