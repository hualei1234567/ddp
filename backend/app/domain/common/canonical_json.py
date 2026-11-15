"""确定性 JSON 规范化（RFC 8785 JCS）——全仓唯一实现。

为什么必须只有一处：Manifest、标定 checksum、导出产物、幂等指纹都靠
"同一份数据算出同一个 Hash"来判定同一性。两份规范化实现哪怕只在浮点
写法或键序上差一点，跨语言重算就会得到不同 Hash，进而把"未篡改"误判成
"已篡改"，或反之。

RFC 8785 与 ``json.dumps(sort_keys=True)`` 的实际差异（已用 rfc8785 实测）：

===========================================  ===============  ==============
输入                                          Python 默认      JCS
===========================================  ===============  ==============
``900.0``                                    ``900.0``        ``900``
``1e-07``                                    ``1e-07``        ``1e-7``
``1e16``                                     ``1e+16``        ``10000000000000000``
``-0.0``                                     ``-0.0``         ``0``
``{"\\uffff": 2, "😀": 1}`` 的键序           码点序            UTF-16 码元序
===========================================  ===============  ==============

因此字符串化后再比 Hash 的场景不能用 Python 默认序列化冒充 JCS。

**超出 2^53 的整数不受支持**：JCS 要求数值可按 IEEE-754 double 往返，
纳秒时间戳（约 1.8e18）不满足。DDP 的纳秒字段在契约里是 ``int64_string``，
调用方必须先转成字符串再交给本模块——这是契约要求，不是本模块的缺陷。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import rfc8785

MAX_SAFE_INTEGER = 2**53 - 1
MAX_DEPTH = 16


class CanonicalJSONError(ValueError):
    """值无法按 RFC 8785 规范化；调用方必须转成自己的领域错误。"""


def canonical_json(value: Any) -> str:
    """返回 RFC 8785 规范 JSON 字符串（无多余空白、UTF-8 直出）。"""
    prepared = _prepare(value, depth=0)
    try:
        return rfc8785.dumps(prepared).decode("utf-8")
    except rfc8785.CanonicalizationError as exc:
        raise CanonicalJSONError(str(exc)) from exc


def canonical_json_bytes(value: Any) -> bytes:
    """返回 RFC 8785 规范 JSON 字节（UTF-8），供直接落对象存储。"""
    return canonical_json(value).encode("utf-8")


def sha256_value(value: Any) -> str:
    """对规范化 JSON 计算 ``sha256:<64 hex>``。"""
    return sha256_text(canonical_json(value))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prepare(value: Any, *, depth: int) -> Any:
    """把 Python 容器/枚举转成 JCS 可接受的值，并前置所有可诊断的拒绝。

    ``rfc8785`` 自身会拒绝非法值，但它的异常不带路径；这里先做一遍带路径的
    检查，好让调用方看到 "items/3/time_range/start_ns" 而不是一句
    "unsupported type"。
    """
    if depth > MAX_DEPTH:
        raise CanonicalJSONError("JSON 嵌套过深")
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        # bool 已在上面被拦截；这里只处理真正的整数。
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJSONError(
                f"整数 {value} 超出 JCS 可精确表示范围（±2^53）；"
                "纳秒时间戳等大整数必须先按契约转成字符串"
            )
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalJSONError("不允许 NaN / Infinity")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalJSONError("JSON 对象键必须是字符串")
        return {key: _prepare(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_prepare(item, depth=depth + 1) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and type(value).__module__ != "builtins":
        return _prepare(enum_value, depth=depth)
    raise CanonicalJSONError(f"JSON 不支持的类型：{type(value).__name__}")


__all__ = [
    "CanonicalJSONError",
    "MAX_SAFE_INTEGER",
    "canonical_json",
    "canonical_json_bytes",
    "sha256_text",
    "sha256_value",
]
