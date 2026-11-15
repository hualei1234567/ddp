"""G-15：RFC 8785 JCS 唯一实现与"旧实现确实不同"的证据。

这些用例锁定两件事：

1. 规范化结果必须符合 RFC 8785，而不是 Python ``json.dumps(sort_keys=True)``
   的近似——差异点逐个写死在用例里，任何人想"简化"实现都会撞到。
2. 超范围整数、NaN、非字符串键必须显式拒绝，不能悄悄改写数值。
"""

from __future__ import annotations

import json
from types import MappingProxyType

import pytest
from app.domain.common.canonical_json import (
    MAX_SAFE_INTEGER,
    CanonicalJSONError,
    canonical_json,
    canonical_json_bytes,
    sha256_text,
    sha256_value,
)


def _old_implementation(value: object) -> str:
    """G-15 之前各处用的写法，仅用于证明与新实现不同。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # 浮点：JCS 用最短往返表示，整数型浮点去掉 .0
        ({"f": 900.0}, '{"f":900}'),
        ({"f": 1.0}, '{"f":1}'),
        ({"f": 0.1}, '{"f":0.1}'),
        # 指数阈值：1e-07 与 1e-7 是不同字符串，JCS 是后者
        ({"f": 1e-07}, '{"f":1e-7}'),
        ({"f": 1e16}, '{"f":10000000000000000}'),
        # -0 归一为 0
        ({"f": -0.0}, '{"f":0}'),
        # 大但不超 2^53 的整数保持原样
        ({"i": MAX_SAFE_INTEGER}, f'{{"i":{MAX_SAFE_INTEGER}}}'),
        # 键序：UTF-16 码元序（补充平面字符排在 ￿ 之前）
        ({"￿": 2, "\U0001f600": 1}, '{"\U0001f600":1,"￿":2}'),
        # 无多余空白
        ({"a": 1, "b": [1, 2, {"c": None}]}, '{"a":1,"b":[1,2,{"c":null}]}'),
        # 中文直出，不转义
        ({"名": "标定"}, '{"名":"标定"}'),
    ],
)
def test_canonical_json_matches_rfc8785(value: object, expected: str) -> None:
    assert canonical_json(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        {"f": 900.0},
        {"f": 1e-07},
        {"f": 1e16},
        {"f": -0.0},
        {"￿": 2, "\U0001f600": 1},
    ],
)
def test_canonical_json_differs_from_plain_sort_keys(value: object) -> None:
    """这些正是"看起来一样、比 Hash 却不同"的陷阱，必须钉住。"""
    assert canonical_json(value) != _old_implementation(value)


def test_canonical_json_rejects_unsafe_integers() -> None:
    """纳秒时间戳约 1.8e18，超出 JCS 精确表示范围，必须要求调用方转字符串。"""
    nanosecond_timestamp = 1_794_700_800_000_000_000
    with pytest.raises(CanonicalJSONError, match="2\\^53"):
        canonical_json({"start_ns": nanosecond_timestamp})
    # 契约里的 int64_string 写法可以通过
    assert canonical_json({"start_ns": str(nanosecond_timestamp)}) == (
        '{"start_ns":"1794700800000000000"}'
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"f": float("nan")}, "NaN"),
        ({"f": float("inf")}, "NaN"),
        ({"f": float("-inf")}, "NaN"),
        ({1: "x"}, "键必须是字符串"),
        ({"v": object()}, "不支持的类型"),
        ({"v": b"bytes"}, "不支持的类型"),
    ],
)
def test_canonical_json_rejects_non_json_values(value: object, message: str) -> None:
    with pytest.raises(CanonicalJSONError, match=message):
        canonical_json(value)


def test_canonical_json_rejects_deep_nesting() -> None:
    deep: object = "leaf"
    for _ in range(20):
        deep = {"a": deep}
    with pytest.raises(CanonicalJSONError, match="嵌套过深"):
        canonical_json(deep)


def test_canonical_json_accepts_frozen_domain_mappings() -> None:
    """领域层传进来的是 MappingProxyType 与 tuple，必须能直接规范化。"""
    assert canonical_json(MappingProxyType({"a": 1, "b": (1, 2)})) == '{"a":1,"b":[1,2]}'


def test_canonical_json_is_key_order_independent() -> None:
    left = {"b": 2, "a": 1, "c": {"z": 1, "y": 2}}
    right = {"c": {"y": 2, "z": 1}, "a": 1, "b": 2}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_value(left) == sha256_value(right)


def test_sha256_helpers_are_prefixed_and_stable() -> None:
    digest = sha256_value({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == 71
    assert sha256_text('{"a":1}') == digest
    assert canonical_json_bytes({"a": 1}) == b'{"a":1}'


def test_manifest_and_dataset_hash_share_the_single_implementation() -> None:
    """两个领域入口必须指向同一实现，否则同一份数据会算出两个 Hash。"""
    from app.domain.datasets.canonical import canonical_json as dataset_canonical
    from app.domain.episodes.manifest import canonical_manifest_json
    from app.domain.modelops.canonical import canonical_json as modelops_canonical

    payload = {"b": 1.0, "a": [1e-07, 2]}
    assert dataset_canonical(payload) == canonical_json(payload)
    assert modelops_canonical(payload) == canonical_json(payload)

    # Episode manifest 额外禁止浮点（契约要求整数），用整数载荷验证同一实现。
    integer_payload = {"b": 1, "a": [2, 3]}
    assert canonical_manifest_json(integer_payload) == canonical_json(integer_payload)
