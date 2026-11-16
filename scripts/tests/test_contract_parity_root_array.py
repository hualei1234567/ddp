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


@pytest.fixture(scope="module")
def parity():
    """延迟加载：``check_contract_parity`` 顶层 ``import yaml``。

    不在模块级加载，是因为 CI 的 security job 只装 ``pytest`` + ``pip-audit``
    + ``pyyaml``，**不装后端依赖**——模块级导入会让整份文件在那种环境下
    直接 collect error，把无关的守卫用例一起带红。真正需要后端 ``app``
    包的那个用例自己声明依赖并跳过（见 ``parity_report``）。
    """
    return _load_module()


def _flatten(module, schema: dict[str, object]):
    document = {"components": {"schemas": {}}}
    return module.flatten_schema(schema, module.Base(document, None))


def test_root_array_of_objects_is_typed(parity) -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "integer"}, "b": {"type": ["string", "null"]}},
        },
    }
    result = _flatten(parity, schema)
    assert result.typed is True
    assert sorted(result.properties) == ["[].a", "[].b"]
    assert result.required == {"[].a"}
    assert result.nullable == {"[].b"}


def test_root_array_with_untyped_items_stays_untyped(parity) -> None:
    """``items`` 是空 schema（任意值）时，仍然无法比对。"""
    assert _flatten(parity, {"type": "array", "items": {}}).typed is False


def test_root_array_of_open_objects_stays_untyped(parity) -> None:
    """``items`` 是开放对象（无 ``properties``）时同属"任意对象"。"""
    schema = {"type": "array", "items": {"type": "object", "additionalProperties": True}}
    assert _flatten(parity, schema).typed is False


def test_nested_array_of_objects_is_typed(parity) -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "array",
            "items": {"type": "object", "properties": {"a": {"type": "integer"}}},
        },
    }
    result = _flatten(parity, schema)
    assert result.typed is True
    assert "[].[].a" in result.properties


def test_object_root_still_typed(parity) -> None:
    assert _flatten(parity, {"type": "object", "properties": {"a": {"type": "integer"}}}).typed is True
    assert _flatten(parity, {"type": "object", "additionalProperties": True}).typed is False


@pytest.fixture(scope="module")
def parity_report(parity):
    """构建完整报告；需要后端 ``app`` 包（读运行时路由表）。

    CI 的 security job 只装 ``pytest`` / ``pip-audit`` / ``pyyaml``，不装后端
    依赖，因此这个 fixture 在那里**明确跳过**而不是报 ImportError——测试文件
    其余纯函数用例不依赖后端，不该被一起带红。
    """
    try:
        import app.main  # noqa: F401 - 只探测后端依赖是否可用
    except ImportError:
        pytest.skip("未安装后端依赖（app 包），跳过需要运行时路由表的用例")
    contract = parity._load_yaml(parity.CONTRACT_FILE)
    runtime = parity.load_runtime_openapi()
    return parity.build_report(contract, runtime)


def test_quality_sample_plan_is_not_reported_as_untyped(parity_report) -> None:
    """已声明 ``response_model=list[QualitySampleOut]``，不得被归为缺模型。"""
    target = "POST /api/v1/projects/{project_id}/quality-samples/plan"
    assert target not in parity_report.untyped_responses
    assert target not in [item["operation"] for item in parity_report.response_field_diffs]
