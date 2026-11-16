"""``check_contract_parity`` 对根响应为数组的端点判定。

回归背景：``flatten_schema`` 过去只在根节点有 ``properties`` 时置 ``typed=True``，
根响应是 ``array`` 的端点因此恒被归入 ``untyped_responses``——即便实现已声明
``response_model=list[XxxOut]``。实测 ``POST /quality-samples/plan`` 两侧字段集、
required、nullable 完全一致，却仍被报成"未声明 response_model"。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def _load_module():
    path = SCRIPTS_DIR / "check_contract_parity.py"
    spec = importlib.util.spec_from_file_location("check_contract_parity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass 解析注解时需要模块已在 sys.modules 中。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = _load_module()


def _flatten(schema: dict[str, object]):
    document = {"components": {"schemas": {}}}
    return parity.flatten_schema(schema, parity.Base(document, None))


def test_root_array_of_objects_is_typed() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "integer"}, "b": {"type": ["string", "null"]}},
        },
    }
    result = _flatten(schema)
    assert result.typed is True
    assert sorted(result.properties) == ["[].a", "[].b"]
    assert result.required == {"[].a"}
    assert result.nullable == {"[].b"}


def test_root_array_with_untyped_items_stays_untyped() -> None:
    """``items`` 是空 schema（任意值）时，仍然无法比对。"""
    assert _flatten({"type": "array", "items": {}}).typed is False


def test_root_array_of_open_objects_stays_untyped() -> None:
    """``items`` 是开放对象（无 ``properties``）时同属"任意对象"。"""
    schema = {"type": "array", "items": {"type": "object", "additionalProperties": True}}
    assert _flatten(schema).typed is False


def test_nested_array_of_objects_is_typed() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "array",
            "items": {"type": "object", "properties": {"a": {"type": "integer"}}},
        },
    }
    result = _flatten(schema)
    assert result.typed is True
    assert "[].[].a" in result.properties


def test_object_root_still_typed() -> None:
    assert _flatten({"type": "object", "properties": {"a": {"type": "integer"}}}).typed is True
    assert _flatten({"type": "object", "additionalProperties": True}).typed is False


@pytest.fixture(scope="module")
def parity_report():
    contract = parity._load_yaml(parity.CONTRACT_FILE)
    runtime = parity.load_runtime_openapi()
    return parity.build_report(contract, runtime)


def test_quality_sample_plan_is_not_reported_as_untyped(parity_report) -> None:
    """已声明 ``response_model=list[QualitySampleOut]``，不得被归为缺模型。"""
    target = "POST /api/v1/projects/{project_id}/quality-samples/plan"
    assert target not in parity_report.untyped_responses
    assert target not in [item["operation"] for item in parity_report.response_field_diffs]
