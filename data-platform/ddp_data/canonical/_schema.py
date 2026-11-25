"""Canonical 实体的通用字段声明、校验与序列化工具（内部模块）。

这一层只做**机械性**工作：字段类型检查、``missing_fields`` 一致性、JSON 友好
序列化。实体语义（哪个字段必填、跨字段约束）留在 ``v1.py``，避免把业务规则
沉进"通用工具"里变成万能模块。

设计要点（ADR-0035）：

- ``missing_fields`` 不是装饰性字段：任何空值（``None`` / 空字符串 / 空元组 /
  空映射）都必须在此显式登记，否则构造失败。这样"数据没有"和"字段漏填"
  在结构上不可混淆。
- 时间戳字段统一以 ``*_ns`` 结尾，内部用 ``int`` 纳秒；``to_dict`` 输出十进制
  字符串，避免 JSON 客户端超过 2^53 丢精度（对齐 Canonical 草案 §2）。
- ``from_dict`` 拒绝未知字段、缺失/不支持的 ``schema_version``、错误类型；
  不做静默升级或默认值补齐。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

_KINDS = frozenset(
    {
        "text",
        "int",
        "number",
        "bool",
        "str_tuple",
        "int_tuple",
        "number_tuple",
        "mapping",
    }
)
_TUPLE_KINDS = frozenset({"str_tuple", "int_tuple", "number_tuple"})


class CanonicalSchemaError(ValueError):
    """Canonical 数据不符合 Schema。调用方必须显式处理，不得当成空数据。"""


class CanonicalSchemaVersionError(CanonicalSchemaError):
    """Schema 版本缺失或不支持。"""


class CanonicalFieldError(CanonicalSchemaError):
    """字段缺失、类型非法或跨字段约束不满足。"""


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """一个 Canonical 字段的声明。

    ``required`` 表示**身份/语义必需**：这类字段为空时不能靠 ``missing_fields``
    豁免，必须直接失败。可选字段为空则必须写入 ``missing_fields``。
    """

    name: str
    required: bool = False
    kind: str = "text"
    minimum: int | float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("FieldSpec.name 不能为空")
        if self.kind not in _KINDS:
            raise ValueError(f"未知字段类型：{self.kind!r}")


def check_schema_version(value: object, supported: frozenset[str]) -> None:
    """版本必须精确命中；不做"向后兼容猜测"。"""
    if not isinstance(value, str) or value not in supported:
        raise CanonicalSchemaVersionError(
            f"不支持的 Canonical Schema 版本：{value!r}；当前支持 {sorted(supported)}"
        )


def is_empty(value: object) -> bool:
    """空值口径：None、空字符串、空元组/列表、空映射。0/False 是真实值。"""
    if value is None:
        return True
    if isinstance(value, str | tuple | list):
        return len(value) == 0
    if isinstance(value, Mapping):
        return len(value) == 0
    return False


def normalize_missing_fields(entity: object, specs: Sequence[FieldSpec]) -> tuple[str, ...]:
    """校验并规范化 ``missing_fields``：去重排序，禁未知/必填字段。"""
    raw = entity.missing_fields
    if not isinstance(raw, tuple | list):
        raise CanonicalFieldError("missing_fields 必须是字符串序列")
    items = tuple(raw)
    if any(not isinstance(item, str) for item in items):
        raise CanonicalFieldError("missing_fields 只能包含字段名字符串")
    if len(set(items)) != len(items):
        raise CanonicalFieldError("missing_fields 不允许重复字段")
    declared = {spec.name for spec in specs}
    unknown = set(items) - declared
    if unknown:
        raise CanonicalFieldError(f"missing_fields 声明了未定义字段：{sorted(unknown)}")
    required = {spec.name for spec in specs if spec.required}
    illegal = set(items) & required
    if illegal:
        raise CanonicalFieldError(f"必填字段不能声明为缺失：{sorted(illegal)}")
    return tuple(sorted(items))


def validate_entity(
    entity: object,
    specs: Sequence[FieldSpec],
    supported_versions: frozenset[str],
) -> None:
    """统一入口：版本 → 缺失声明规范化 → 类型 → 缺失一致性。"""
    check_schema_version(entity.schema_version, supported_versions)
    missing = normalize_missing_fields(entity, specs)
    object.__setattr__(entity, "missing_fields", missing)
    _validate_types(entity, specs)
    _validate_missing_consistency(entity, specs, missing)


def _validate_types(entity: object, specs: Sequence[FieldSpec]) -> None:
    entity_name = type(entity).__name__
    for spec in specs:
        value = getattr(entity, spec.name)
        if value is None:
            continue
        label = f"{entity_name}.{spec.name}"
        if spec.kind == "text":
            if not isinstance(value, str):
                raise CanonicalFieldError(f"{label} 必须是字符串，得到 {type(value).__name__}")
        elif spec.kind == "int":
            _require_int(value, label, spec.minimum)
        elif spec.kind == "number":
            _require_number(value, label, spec.minimum)
        elif spec.kind == "bool":
            if not isinstance(value, bool):
                raise CanonicalFieldError(f"{label} 必须是布尔值，得到 {type(value).__name__}")
        elif spec.kind == "str_tuple":
            _require_tuple(value, label)
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise CanonicalFieldError(f"{label} 只能包含非空字符串")
        elif spec.kind == "int_tuple":
            _require_tuple(value, label)
            for item in value:
                _require_int(item, label, spec.minimum)
        elif spec.kind == "number_tuple":
            _require_tuple(value, label)
            for item in value:
                _require_number(item, label, None)
        elif spec.kind == "mapping":
            if not isinstance(value, Mapping):
                raise CanonicalFieldError(f"{label} 必须是映射，得到 {type(value).__name__}")


def _require_tuple(value: object, label: str) -> None:
    if not isinstance(value, tuple):
        raise CanonicalFieldError(f"{label} 必须是元组（list 需先经 from_dict 转换）")


def _require_int(value: object, label: str, minimum: int | float | None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanonicalFieldError(f"{label} 必须是整数，得到 {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise CanonicalFieldError(f"{label} 不能小于 {minimum}，得到 {value}")


def _require_number(value: object, label: str, minimum: int | float | None) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CanonicalFieldError(f"{label} 必须是数字，得到 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise CanonicalFieldError(f"{label} 不能是 NaN/Infinity")
    if minimum is not None and number < minimum:
        raise CanonicalFieldError(f"{label} 不能小于 {minimum}，得到 {number}")


def _validate_missing_consistency(
    entity: object,
    specs: Sequence[FieldSpec],
    missing: tuple[str, ...],
) -> None:
    entity_name = type(entity).__name__
    missing_set = set(missing)
    for spec in specs:
        value = getattr(entity, spec.name)
        empty = is_empty(value)
        if empty and spec.name not in missing_set:
            raise CanonicalFieldError(
                f"{entity_name}.{spec.name} 为空，但未在 missing_fields 中声明"
            )
        if not empty and spec.name in missing_set:
            raise CanonicalFieldError(f"{entity_name}.{spec.name} 已声明缺失，但存在值")


def entity_to_dict(entity: object, specs: Sequence[FieldSpec] | None = None) -> dict[str, object]:
    """JSON 友好输出：元组转数组、映射转 dict、``*_ns`` 转十进制字符串。

    当传入 ``specs``（按版本裁剪后的字段表）时，只输出该版本声明的字段 +
    ``schema_version`` + ``missing_fields``。这样 1.0 实例序列化时不会夹带
    1.1 新增的 ``None`` 键，保证 ``1.0 -> to_dict -> from_dict`` 往返与
    历史 1.0 JSON 产物完全同构。
    """
    allowed = (
        {spec.name for spec in specs} | {"schema_version", "missing_fields"}
        if specs is not None
        else None
    )
    result: dict[str, object] = {}
    for item in fields(entity):
        if allowed is not None and item.name not in allowed:
            continue
        value = getattr(entity, item.name)
        if item.name.endswith("_ns") and isinstance(value, int) and not isinstance(value, bool):
            result[item.name] = str(value)
        elif isinstance(value, tuple):
            result[item.name] = list(value)
        elif isinstance(value, Mapping):
            result[item.name] = dict(value)
        else:
            result[item.name] = value
    return result


def entity_from_dict(
    cls: type[Any],
    data: Mapping[str, object],
    specs: Sequence[FieldSpec],
    supported_versions: frozenset[str],
) -> Any:
    """严格反序列化：未知字段/版本/类型一律拒绝，不补默认值。"""
    if not isinstance(data, Mapping):
        raise CanonicalFieldError(f"{cls.__name__}.from_dict 需要映射，得到 {type(data).__name__}")
    allowed = {spec.name for spec in specs} | {"schema_version", "missing_fields"}
    unknown = set(data) - allowed
    if unknown:
        raise CanonicalFieldError(f"{cls.__name__} 出现未知字段：{sorted(unknown)}")
    if "schema_version" not in data:
        raise CanonicalSchemaVersionError(f"{cls.__name__} 缺少 schema_version")
    check_schema_version(data["schema_version"], supported_versions)

    kwargs: dict[str, object] = {}
    for spec in specs:
        kwargs[spec.name] = _decode_value(spec, data.get(spec.name))
    kwargs["missing_fields"] = _decode_missing_fields(data.get("missing_fields"))
    kwargs["schema_version"] = data["schema_version"]
    return cls(**kwargs)


def _decode_value(spec: FieldSpec, value: object) -> object:
    if value is None:
        return None
    label = spec.name
    if spec.name.endswith("_ns"):
        return _decode_ns(value, label)
    if spec.kind == "mapping":
        if not isinstance(value, Mapping):
            raise CanonicalFieldError(f"{label} 必须是映射，得到 {type(value).__name__}")
        return dict(value)
    if spec.kind in _TUPLE_KINDS:
        if not isinstance(value, list | tuple):
            raise CanonicalFieldError(f"{label} 必须是数组，得到 {type(value).__name__}")
        items = list(value)
        if spec.kind == "str_tuple":
            return tuple(items)
        if spec.kind == "int_tuple":
            return tuple(_decode_int(item, label) for item in items)
        return tuple(_decode_number(item, label) for item in items)
    if spec.kind == "int":
        return _decode_int(value, label)
    if spec.kind == "number":
        return _decode_number(value, label)
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise CanonicalFieldError(f"{label} 必须是布尔值，得到 {type(value).__name__}")
        return value
    if not isinstance(value, str):
        raise CanonicalFieldError(f"{label} 必须是字符串，得到 {type(value).__name__}")
    return value


def _decode_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanonicalFieldError(f"{label} 必须是整数，得到 {type(value).__name__}")
    return value


def _decode_number(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CanonicalFieldError(f"{label} 必须是数字，得到 {type(value).__name__}")
    if not math.isfinite(float(value)):
        raise CanonicalFieldError(f"{label} 不能是 NaN/Infinity")
    return value


def _decode_ns(value: object, label: str) -> int:
    """纳秒时间戳允许十进制字符串（契约形式）或整数（内部形式）。"""
    if isinstance(value, bool):
        raise CanonicalFieldError(f"{label} 必须是纳秒整数或十进制字符串")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 10)
        except ValueError as exc:
            raise CanonicalFieldError(f"{label} 必须是十进制纳秒字符串，得到 {value!r}") from exc
    raise CanonicalFieldError(f"{label} 必须是纳秒整数或十进制字符串，得到 {type(value).__name__}")


def _decode_missing_fields(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise CanonicalFieldError("missing_fields 必须是字符串数组")
    return tuple(value)


def compute_missing_fields(
    specs: Sequence[FieldSpec], values: Mapping[str, object]
) -> tuple[str, ...]:
    """按"空值即缺失"推导 ``missing_fields``；供只读投影显式登记缺口。

    这不是"补默认值"：它只把实际为空的可选字段列出来；必填字段为空会直接失败。
    """
    missing: list[str] = []
    for spec in specs:
        value = values.get(spec.name)
        if is_empty(value):
            if spec.required:
                raise CanonicalFieldError(f"{spec.name} 是必填字段，不能为空")
            missing.append(spec.name)
    return tuple(sorted(missing))
