#!/usr/bin/env python3
"""OpenAPI 契约与实现一致性核查（只读，可进 CI）。

## 为什么需要这个脚本

`packages/contracts/openapi.yaml` 有 1 万行、拆成 `fragments/` 多个文件；靠肉眼
比对必然漏。历史上反复出现双向漂移：

- 实现了但契约没声明（`frame_id` / `frame_convention` 曾遗漏，G-20）；
- 契约声明了但实现从不读（`ActionRequest.maintenance_record_id` / `evidence_refs`）。

本脚本把"契约"和"运行时"两侧都归一成可比的结构，输出三类差异：

1. **实现超前**：运行时路由不在契约里；
2. **契约超前**：契约路径不在运行时（已删除但没清理）；
3. **字段差异**：同一 (路径, 方法) 的请求/响应 schema，properties 与 required 不一致。

另有两个必须单列、否则会淹没真差异的类别：

- **实现侧未声明 response_model / 请求模型**：FastAPI 会导出
  ``{"type": "object", "additionalProperties": true}``，即"任意对象"。把它当作
  空对象去比，会把"端点缺模型"误报成"契约多出 N 个字段"。这类单独列出。
- **错误码可达性**：从契约的四处来源（openapi.ErrorCode 枚举、阶段 4/5 注册表、
  ``docs/architecture/contract-draft/*/error-codes.v1.json``）取全部 code，
  用 AST 在 ``backend/app`` 里找**产生点**（六类：类属性、异常类构造、``AppError(...)``、
  ``super().__init__(...)``、``{"code": ...}`` 字面量、helper 位置参数传码），
  输出"有源 / 实现超前 / 契约超前 / 只在映射表里转发但从无源"四张表。
  第 3 类会再切一刀，把"仅存在于草案、未进生效契约"的预留码单列——
  草稿码不算生效契约超前。

## 用法

    backend/.venv/bin/python scripts/check_contract_parity.py
    backend/.venv/bin/python scripts/check_contract_parity.py --errors-only
    backend/.venv/bin/python scripts/check_contract_parity.py --json > parity.json
    backend/.venv/bin/python scripts/check_contract_parity.py --strict   # 有差异即退出码 1

## 已知的、有意不比的项

- 运行时 `/openapi.json`、`/docs`、`/redoc` 是框架自带的文档端点，不属于业务契约；
- 契约侧 `parameters`、`responses` 的状态码集合不参与"字段差异"，
  只比成功响应的 body schema（其余差异由 `redocly lint` 与冻结脚本负责）；
- 枚举取值不参与归一（运行时 FastAPI 导出的是字面量类型，契约是 `$ref` 到枚举，
  两边表达方式不同，强行比会全是噪声）；
- 可选字段在契约里写 `type: [string, "null"]`、在运行时导出成 `anyOf`，两种写法
  都归一为"可空"，不计差异。

脚本只读文件，不写任何东西。退出码：0 = 无差异或非严格模式；1 = `--strict` 且存在差异。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
CONTRACT_FILE = REPO_ROOT / "packages" / "contracts" / "openapi.yaml"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
# 框架自带的文档端点，不属于业务契约，比对时忽略。
IGNORED_RUNTIME_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
SUCCESS_STATUSES = ("200", "201", "202", "204")
MAX_SCHEMA_DEPTH = 6


# --------------------------------------------------------------------------- #
# 契约侧：带 base 文件追踪的 fragment 解析
# --------------------------------------------------------------------------- #
_yaml_cache: dict[Path, object] = {}


class Base:
    """引用解析上下文：当前文档 + 该文档所在文件（运行时无文件，为 None）。

    ``$ref`` 有两种形态，必须区分：

    - 外部文件引用 ``./fragments/paths.stage1.yaml#/robots``：换文档也换文件；
    - 文档内引用 ``#/components/schemas/Robot``：文档不变。
    """

    __slots__ = ("document", "file")

    def __init__(self, document: object, file: Path | None = None) -> None:
        self.document = document
        self.file = file


def _load_yaml(path: Path) -> object:
    path = path.resolve()
    if path not in _yaml_cache:
        with open(path, encoding="utf-8") as handle:
            _yaml_cache[path] = yaml.safe_load(handle)
    return _yaml_cache[path]


def _walk_pointer(document: object, pointer: str) -> object:
    current = document
    if not pointer or pointer == "/":
        return current
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        current = current[part]  # type: ignore[index]
    return current


def deref(node: object, base: Base, seen: tuple[str, ...] = ()) -> tuple[object, Base]:
    """展开顶层 ``$ref`` 链，返回 (节点, 节点所属上下文)。"""
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        ref = node["$ref"]
        if ref in seen:
            return {}, base
        seen = seen + (ref,)
        file_part, _, pointer = ref.partition("#")
        if file_part and base.file is not None:
            target_file = (base.file.parent / file_part).resolve()
            base = Base(_load_yaml(target_file), target_file)
        node = _walk_pointer(base.document, pointer)
    return node, base


def resolve_path_item(contract: dict[str, object], path: str) -> tuple[dict[str, object], Base]:
    """把 ``paths.<path>`` 的 ``$ref`` 展开成真正的 path item。"""
    node, base = deref(contract["paths"][path], Base(contract, CONTRACT_FILE))
    return node, base  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# schema 归一：压成 "字段路径 → 类型" 的扁平结构
# --------------------------------------------------------------------------- #
@dataclass
class FieldSet:
    """一个 schema 展开后的字段集合（用于跨侧比较）。

    ``typed`` 尤其关键：FastAPI 端点在**没有声明 ``response_model``** 时，
    导出的 schema 是 ``{"type": "object", "additionalProperties": true}``——
    它是一个"任意对象"，不是"空对象"。把它当成空对象比，会把
    "实现侧没写响应模型"误报成"契约多出 N 个字段"。
    """

    properties: dict[str, str] = field(default_factory=dict)
    required: set[str] = field(default_factory=set)
    typed: bool = False

    def only_in(self, other: FieldSet) -> list[str]:
        return sorted(set(self.properties) - set(other.properties))


def _normalize_type(schema: dict[str, object]) -> str:
    kind = schema.get("type")
    if isinstance(kind, list):
        non_null = [item for item in kind if item != "null"]
        return str(non_null[0]) if len(non_null) == 1 else "union"
    if isinstance(kind, str):
        return kind
    if "enum" in schema:
        return "enum"
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return "any"


def flatten_schema(
    schema: object,
    base: Base,
    prefix: str = "",
    depth: int = 0,
    result: FieldSet | None = None,
    seen: tuple[str, ...] = (),
) -> FieldSet:
    """把（可能有 $ref / anyOf / allOf / array 的）schema 压成字段路径集合。"""
    result = FieldSet() if result is None else result
    if depth > MAX_SCHEMA_DEPTH:
        return result
    schema, base = deref(schema, base, seen)
    if not isinstance(schema, dict) or not schema:
        return result

    for combinator in ("anyOf", "oneOf"):
        options = schema.get(combinator)
        if not isinstance(options, list):
            continue
        non_null = [
            option
            for option in options
            if not (isinstance(option, dict) and option.get("type") == "null")
        ]
        if len(non_null) == 1:
            # 只有 null 与非 null 两支 —— 就是可空字段，按非空那支比。
            schema = {**schema, **{combinator: None}}
            merged = flatten_schema(non_null[0], base, prefix, depth, result, seen)
            result = merged
            schema.pop(combinator, None)
            continue
        # 多个非 null 分支：字段取并集，required 取交集
        # （某字段只有在每个分支都必填时，才是"契约说必填"）。
        branches = [
            flatten_schema(option, base, prefix, depth, FieldSet(), seen) for option in non_null
        ]
        for branch in branches:
            for key, kind in branch.properties.items():
                result.properties.setdefault(key, kind)
        common_required = set(branches[0].required)
        for branch in branches[1:]:
            common_required &= branch.required
        result.required |= common_required

    for sub in schema.get("allOf") or ():
        flatten_schema(sub, base, prefix, depth, result, seen)

    kind = _normalize_type(schema)
    if kind == "array" and isinstance(schema.get("items"), dict):
        flatten_schema(schema["items"], base, prefix + "[].", depth + 1, result, seen)
        return result

    properties = schema.get("properties")
    if isinstance(properties, dict):
        if not prefix:
            # 只有根节点能判定"这个 schema 是否真的描述了一个对象"。
            result.typed = True
        required = set(schema.get("required") or ())
        for name, sub in properties.items():
            key = prefix + name
            result.properties[key] = kind
            if name in required:
                result.required.add(key)
            flatten_schema(sub, base, key + ".", depth + 1, result, seen)
    elif not prefix and schema.get("additionalProperties") is True and kind == "object":
        # 根节点是无 properties 的开放对象（FastAPI 缺 response_model 的典型导出）：
        # 内容未知，不能当作"零字段"参与逐字段比较。
        result.typed = False
    return result


def _success_schema(responses: object, base: Base) -> tuple[object, Base]:
    if not isinstance(responses, dict):
        return None, base
    for status in SUCCESS_STATUSES:
        response = responses.get(status)
        if not response:
            continue
        response, response_base = deref(response, base)
        if not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        if not isinstance(content, dict):
            continue
        for media in content.values():
            if isinstance(media, dict) and media.get("schema") is not None:
                return media["schema"], response_base
    return None, base


def _request_schema(operation: object, base: Base) -> tuple[object, Base]:
    if not isinstance(operation, dict):
        return None, base
    body = operation.get("requestBody")
    if not body:
        return None, base
    body, body_base = deref(body, base)
    if not isinstance(body, dict):
        return None, base
    content = body.get("content") or {}
    if not isinstance(content, dict):
        return None, base
    for media in content.values():
        if isinstance(media, dict) and media.get("schema") is not None:
            return media["schema"], body_base
    return None, base


# --------------------------------------------------------------------------- #
# 运行时侧
# --------------------------------------------------------------------------- #
def load_runtime_openapi() -> dict[str, object]:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app.main import create_app  # 延迟导入：只读模式下不引入数据库依赖

    return create_app().openapi()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
@dataclass
class ParityReport:
    runtime_only_operations: list[str] = field(default_factory=list)
    contract_only_operations: list[str] = field(default_factory=list)
    request_field_diffs: list[dict[str, Any]] = field(default_factory=list)
    response_field_diffs: list[dict[str, Any]] = field(default_factory=list)
    # 实现侧未声明 response_model / 请求模型，导出为"任意对象"，无法逐字段比对。
    untyped_responses: list[str] = field(default_factory=list)
    untyped_requests: list[str] = field(default_factory=list)

    @property
    def has_diff(self) -> bool:
        return bool(
            self.runtime_only_operations
            or self.contract_only_operations
            or self.request_field_diffs
            or self.response_field_diffs
        )


def _diff_fields(
    contract_schema: object,
    contract_base: Base,
    runtime_schema: object,
    runtime_base: Base,
) -> dict[str, object] | None:
    contract_fields = flatten_schema(contract_schema, contract_base)
    runtime_fields = flatten_schema(runtime_schema, runtime_base)
    if not runtime_fields.typed:
        # 实现侧导出的是"任意对象"：这不是字段漂移，是端点缺 response_model。
        return {
            "untyped_runtime": True,
            "contract_field_count": len(contract_fields.properties),
        }
    contract_only = contract_fields.only_in(runtime_fields)
    runtime_only = runtime_fields.only_in(contract_fields)
    contract_required_only = sorted(contract_fields.required - runtime_fields.required)
    runtime_required_only = sorted(runtime_fields.required - contract_fields.required)
    if not (contract_only or runtime_only or contract_required_only or runtime_required_only):
        return None
    return {
        "contract_only": contract_only,
        "runtime_only": runtime_only,
        "required_contract_only": contract_required_only,
        "required_runtime_only": runtime_required_only,
    }


def _operations(paths: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, item in paths.items()
        if isinstance(item, dict)
        for method in item
        if method in HTTP_METHODS
    }


def contract_operations(contract: dict[str, Any]) -> set[tuple[str, str]]:
    """契约侧方法名藏在 ``$ref`` 指向的 fragment 里，必须先展开。"""
    operations: set[tuple[str, str]] = set()
    for path in contract["paths"]:
        item, _ = resolve_path_item(contract, path)
        if not isinstance(item, dict):
            continue
        for method in item:
            if method in HTTP_METHODS:
                operations.add((path, method))
    return operations


def build_report(
    contract: dict[str, Any],
    runtime: dict[str, Any],
) -> ParityReport:
    report = ParityReport()
    runtime_paths = {
        path: item for path, item in runtime["paths"].items() if path not in IGNORED_RUNTIME_PATHS
    }
    runtime_root = Base(runtime, None)

    contract_ops = contract_operations(contract)
    runtime_ops = _operations(runtime_paths)

    report.runtime_only_operations = [
        f"{method.upper()} {path}" for path, method in sorted(runtime_ops - contract_ops)
    ]
    report.contract_only_operations = [
        f"{method.upper()} {path}" for path, method in sorted(contract_ops - runtime_ops)
    ]

    for path, method in sorted(contract_ops & runtime_ops):
        contract_item, contract_item_base = resolve_path_item(contract, path)
        contract_op, contract_base = deref(contract_item[method], contract_item_base)
        runtime_op = runtime_paths[path][method]

        contract_req, contract_req_base = _request_schema(contract_op, contract_base)
        runtime_req, runtime_req_base = _request_schema(runtime_op, runtime_root)
        if contract_req is not None and runtime_req is not None:
            diff = _diff_fields(contract_req, contract_req_base, runtime_req, runtime_req_base)
            if diff and diff.get("untyped_runtime"):
                report.untyped_requests.append(f"{method.upper()} {path}")
            elif diff:
                report.request_field_diffs.append({"operation": f"{method.upper()} {path}", **diff})

        contract_resp, contract_resp_base = _success_schema(
            (contract_op or {}).get("responses") or {}, contract_base
        )
        runtime_resp, runtime_resp_base = _success_schema(
            (runtime_op or {}).get("responses") or {}, runtime_root
        )
        if contract_resp is not None and runtime_resp is not None:
            diff = _diff_fields(contract_resp, contract_resp_base, runtime_resp, runtime_resp_base)
            if diff and diff.get("untyped_runtime"):
                report.untyped_responses.append(f"{method.upper()} {path}")
            elif diff:
                report.response_field_diffs.append(
                    {"operation": f"{method.upper()} {path}", **diff}
                )
    return report


def _print_report(report: ParityReport) -> None:
    print("=" * 78)
    print("一、实现超前：运行时路由不在契约里")
    print("=" * 78)
    if report.runtime_only_operations:
        for item in report.runtime_only_operations:
            print(f"  + {item}")
    else:
        print("  （无）")

    print()
    print("=" * 78)
    print("二、契约超前：契约路径不在运行时")
    print("=" * 78)
    if report.contract_only_operations:
        for item in report.contract_only_operations:
            print(f"  - {item}")
    else:
        print("  （无）")

    for title, key in (
        ("三、请求体字段差异", "request_field_diffs"),
        ("四、响应体字段差异", "response_field_diffs"),
    ):
        print()
        print("=" * 78)
        print(title)
        print("=" * 78)
        diffs = getattr(report, key)
        if not diffs:
            print("  （无）")
            continue
        for entry in diffs:
            print(f"  {entry['operation']}")
            if entry["contract_only"]:
                print(f"      契约多字段: {', '.join(entry['contract_only'])}")
            if entry["runtime_only"]:
                print(f"      实现多字段: {', '.join(entry['runtime_only'])}")
            if entry["required_contract_only"]:
                print(f"      契约必填但实现非必填: {', '.join(entry['required_contract_only'])}")
            if entry["required_runtime_only"]:
                print(f"      实现必填但契约非必填: {', '.join(entry['required_runtime_only'])}")

    for title, items in (
        ("五、实现侧未声明请求模型（导出为任意对象，无法比对）", report.untyped_requests),
        ("六、实现侧未声明 response_model（导出为任意对象，无法比对）", report.untyped_responses),
    ):
        print()
        print("=" * 78)
        print(title)
        print("=" * 78)
        if items:
            for item in items:
                print(f"  ? {item}")
        else:
            print("  （无）")

    print()
    print("-" * 78)
    print(
        f"汇总：实现超前 {len(report.runtime_only_operations)} 个操作，"
        f"契约超前 {len(report.contract_only_operations)} 个操作，"
        f"请求体差异 {len(report.request_field_diffs)} 个操作，"
        f"响应体差异 {len(report.response_field_diffs)} 个操作，"
        f"实现侧无类型声明 请求 {len(report.untyped_requests)} / "
        f"响应 {len(report.untyped_responses)} 个"
    )


# --------------------------------------------------------------------------- #
# 错误码可达性：契约声明 ↔ 实现产生点
# --------------------------------------------------------------------------- #
ERROR_CODE_REGISTRIES = (
    REPO_ROOT / "packages" / "contracts" / "fragments" / "schemas.common.yaml",
    REPO_ROOT / "packages" / "contracts" / "schemas" / "error-codes.v1.json",
    REPO_ROOT / "packages" / "contracts" / "schemas" / "modelops-error-codes.v1.json",
)
ERRORS_MAPPING_FILE = BACKEND_DIR / "app" / "api" / "errors.py"

# 错误码形态：全小写 ASCII + 可选下划线（单段也合法，如 conflict / forbidden）。
# 用它过滤 ``super().__init__("中文消息")`` 这类"第一个位置参数不是 code"的构造，
# 以及 ``"DUPLICATE"`` 之类的枚举字面量（含大写）。
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# 第 6 类（helper 位置参数）用更严的形态，避免把日志文案当码：
# 至少 3 字符、必须出现下划线分隔，且还要在映射表里存在（双重保险）。
HELPER_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,}$")


def _looks_like_code(value: str) -> bool:
    return bool(CODE_PATTERN.match(value))


DRAFT_REGISTRY_DIR = REPO_ROOT / "docs" / "architecture" / "contract-draft"


def contract_error_codes(*, include_drafts: bool = False) -> dict[str, set[str]]:
    """各契约来源分别声明的错误码。

    ``packages/contracts`` 三处是权威来源。但阶段 3 标注域的冻结错误码注册表
    与阶段 4 草案只存在于 ``docs/architecture/contract-draft/*/error-codes.v1.json``，
    openapi 的 ``ErrorCode`` 枚举从未收录它们。这本身是一处契约缺口，
    因此把草案注册表也列为来源并标明出处，避免把"文档里声明过"误报成
    "实现凭空多出来"。比对区间仍是"实现 vs 全部来源"。
    """
    common = _load_yaml(ERROR_CODE_REGISTRIES[0])
    enum = common.get("ErrorCode", {}).get("enum") or []
    stage4 = json.loads(ERROR_CODE_REGISTRIES[1].read_text(encoding="utf-8"))
    modelops = json.loads(ERROR_CODE_REGISTRIES[2].read_text(encoding="utf-8"))
    registries: dict[str, set[str]] = {
        "openapi.ErrorCode": set(enum),
        "error-codes.v1(阶段4注册表)": {item["code"] for item in stage4.get("codes", [])}
        | set(stage4.get("reused_codes", [])),
        "modelops-error-codes.v1(阶段5注册表)": {
            item["code"] for item in modelops.get("codes", [])
        },
    }
    if include_drafts and DRAFT_REGISTRY_DIR.is_dir():
        for path in sorted(DRAFT_REGISTRY_DIR.glob("*/error-codes.v1.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            registries[f"contract-draft/{path.parent.name}"] = {
                item["code"] for item in document.get("codes", [])
            } | set(document.get("reused_codes", []))
    return registries


@dataclass
class ErrorCodeScan:
    # code → [(相对路径, 行号, 产生方式)]
    sources: dict[str, list[tuple[str, int, str]]] = field(default_factory=dict)
    # 领域异常类名 → code
    class_codes: dict[str, str] = field(default_factory=dict)
    # 映射表里出现的 code（errors.py 各域的 STATUS/MESSAGES 表）
    mapped_codes: set[str] = field(default_factory=set)


def scan_error_sources() -> ErrorCodeScan:
    """用 AST 找每个错误码的**产生点**，而不是 grep 字符串。

    产生点共六类。少算任何一类都会把"有源"误报成"只在映射表里转发"：

    1. 领域异常类的 ``code = "..."`` 类属性——**只声明、不产生**，单独记录在
       ``class_codes`` 里，不计入 ``sources``；
    2. ``SomeError(...)`` / ``some_error(...)`` 构造调用，且该类声明过 code
       （含构造时用 ``code=`` 关键字覆盖的情况）；
    3. ``AppError("code", ...)`` 直接构造；
    4. ``super().__init__("code", ...)``——基类把 code 写进构造函数，
       子类（``AuthenticationError`` / ``NotFoundError`` / ``PreconditionRequiredError``
       等）走这条路；
    5. 原始 JSON 响应里的 ``"code": "..."`` 字面量（限流中间件走这条路）；
    6. **helper 方法/函数以位置参数传码**：``self._fail(scope, job, now, "export_job_failed", ...)``
       / ``_emit("some_code", ...)`` / ``self._fail(..., SomeError.code, ...)``。
       这类调用不构造异常对象，码只是普通字符串参数——前五类都抓不到，
       会把 ``export_partial`` / ``export_job_failed`` 误报成"无源"。
       判定条件收紧为三条同时成立，避免把日志文案当码：
       (a) 调用目标形如 ``self.xxx(...)`` 或模块级函数 ``xxx(...)``；
       (b) 位置参数里的字符串字面量匹配 ``^[a-z][a-z0-9_]{2,}$``；
       (c) 该字面量在 ``api/errors.py`` 的映射表里出现过。
    """
    scan = ErrorCodeScan()
    classes: dict[str, str] = {}
    trees: dict[Path, Any] = {}

    for path in sorted((BACKEND_DIR / "app").rglob("*.py")):
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(trees[path]):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                value = None
                if isinstance(stmt, ast.Assign):
                    targets = stmt.targets
                    if any(
                        isinstance(target, ast.Name) and target.id == "code" for target in targets
                    ):
                        value = stmt.value
                elif (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "code"
                ):
                    value = stmt.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    classes[node.name] = value.value
    scan.class_codes = dict(classes)

    # 映射表必须先算：第 6 类靠它过滤普通文案。
    text = ERRORS_MAPPING_FILE.read_text(encoding="utf-8")
    for match in re.finditer(
        r"^_(?:[A-Z]+_)?(?:STATUS_BY_CODE|MESSAGES)\s*=\s*\{(.*?)^\}", text, re.S | re.M
    ):
        scan.mapped_codes |= set(re.findall(r'"([a-z_0-9]+)":', match.group(1)))

    def _positional_codes(node: ast.Call) -> list[str]:
        """调用里出现在位置参数上的错误码（字面量或 ``SomeError.code``）。"""
        found: list[str] = []
        for arg in node.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and HELPER_CODE_PATTERN.match(arg.value)
                and "_" in arg.value
                and arg.value in scan.mapped_codes
            ):
                found.append(arg.value)
            elif (
                isinstance(arg, ast.Attribute)
                and arg.attr == "code"
                and isinstance(arg.value, ast.Name)
                and arg.value.id in classes
            ):
                found.append(classes[arg.value.id])
        return found

    for path, tree in trees.items():
        rel = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values, strict=True):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "code"
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)
                            and _looks_like_code(value.value)
                        ):
                            scan.sources.setdefault(value.value, []).append(
                                (rel, node.lineno, '{"code": ...}')
                            )
                continue

            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name in classes:
                code = classes[name]
                for keyword in node.keywords:
                    if (
                        keyword.arg == "code"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        code = keyword.value.value
                if _looks_like_code(code):
                    scan.sources.setdefault(code, []).append((rel, node.lineno, f"{name}(...)"))
                continue
            if name == "AppError" and node.args:
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and _looks_like_code(first.value)
                ):
                    scan.sources.setdefault(first.value, []).append(
                        (rel, node.lineno, "AppError(...)")
                    )
                continue
            if name == "__init__" and node.args:
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and _looks_like_code(first.value)
                ):
                    scan.sources.setdefault(first.value, []).append(
                        (rel, node.lineno, "super().__init__(...)")
                    )
                continue
            # 第 6 类：helper 把码当普通参数传（self._fail(...) / 模块函数）
            target_is_self = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
            )
            target_is_module_fn = isinstance(func, ast.Name)
            if target_is_self or target_is_module_fn:
                for code in _positional_codes(node):
                    scan.sources.setdefault(code, []).append(
                        (rel, node.lineno, f"{name}(<code>...)")
                    )

    return scan


def print_error_report(scan: ErrorCodeScan, registries: dict[str, set[str]]) -> None:
    declared_all: set[str] = set().union(*registries.values())
    print("=" * 78)
    print(f"错误码可达性（来源：{'、'.join(registries)}）")
    print("=" * 78)
    print(f"契约声明合计 {len(declared_all)} 个 code；实现有产生点 {len(scan.sources)} 个 code")
    print()
    print("一、有产生点（契约已声明，可达）")
    ready = sorted(code for code in scan.sources if code in declared_all)
    for code in ready:
        where = scan.sources[code][0]
        print(f"  ✓ {code:46s} {where[0]}:{where[1]}")
    print(f"  合计 {len(ready)}")
    print()
    print("二、有产生点但契约未声明（实现超前，客户端无法按 code 分支）")
    undeclared = sorted(code for code in scan.sources if code not in declared_all)
    for code in undeclared:
        where = scan.sources[code][0]
        print(f"  + {code:46s} {where[0]}:{where[1]}")
    print(f"  合计 {len(undeclared)}")
    print()
    print("三、契约声明但无产生点（契约超前）")
    unreachable = sorted(code for code in declared_all if code not in scan.sources)
    for code in unreachable:
        owners = [name for name, codes in registries.items() if code in codes]
        mapped = "映射表有" if code in scan.mapped_codes else "映射表亦无"
        print(f"  - {code:46s} 声明于 {owners}；{mapped}")
    print(f"  合计 {len(unreachable)}")

    # 草稿（docs/architecture/contract-draft）不是生效契约，不能与
    # packages/contracts 的权威声明混为一谈，否则会把"阶段 3 草案预留码"
    # 全部算成"契约超前"。这里按来源再切一刀。
    draft_codes: set[str] = (
        set().union(
            *(codes for name, codes in registries.items() if name.startswith("contract-draft/")),
        )
        if any(name.startswith("contract-draft/") for name in registries)
        else set()
    )
    effective_codes = set().union(
        *(codes for name, codes in registries.items() if not name.startswith("contract-draft/")),
    )
    effective_unreachable = sorted(code for code in effective_codes if code not in scan.sources)
    draft_only_unreachable = sorted(
        code for code in unreachable if code not in effective_codes and code in draft_codes
    )
    print(
        f"    其中：生效契约（packages/contracts）超前 {len(effective_unreachable)} 条；"
        f"仅出现在 docs/architecture/contract-draft 草案、未进生效契约的预留码 "
        f"{len(draft_only_unreachable)} 条（不算生效契约超前）"
    )
    print()
    print("四、只在映射表里转发、却无任何产生点（最隐蔽的一类）")
    forwarded = sorted(code for code in scan.mapped_codes if code not in scan.sources)
    for code in forwarded:
        print(f"  ~ {code}")
    print(f"  合计 {len(forwarded)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument(
        "--packages-only",
        action="store_true",
        help="错误码只比对 packages/contracts 的权威来源，不含 docs/architecture/contract-draft",
    )
    parser.add_argument(
        "--errors-only",
        action="store_true",
        help="只跑错误码可达性分析（不加载后端 app，秒级）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在任一差异时退出码 1（CI 用）",
    )
    args = parser.parse_args(argv)

    contract = _load_yaml(CONTRACT_FILE)
    registries = contract_error_codes(include_drafts=not args.packages_only)
    scan = scan_error_sources()

    if args.errors_only:
        if args.json:
            declared_all = set().union(*registries.values())
            print(
                json.dumps(
                    {
                        "declared": sorted(declared_all),
                        "reachable": sorted(c for c in scan.sources if c in declared_all),
                        "implementation_ahead": sorted(
                            c for c in scan.sources if c not in declared_all
                        ),
                        "contract_ahead": sorted(c for c in declared_all if c not in scan.sources),
                        "mapped_but_unsourced": sorted(
                            c for c in scan.mapped_codes if c not in scan.sources
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print_error_report(scan, registries)
        return 0

    runtime = load_runtime_openapi()
    report = build_report(contract, runtime)

    if args.json:
        declared_all = set().union(*registries.values())
        print(
            json.dumps(
                {
                    "contract_version": contract.get("info", {}).get("version"),
                    "contract_paths": len(contract["paths"]),
                    "runtime_paths": len(runtime["paths"]),
                    "runtime_only_operations": report.runtime_only_operations,
                    "contract_only_operations": report.contract_only_operations,
                    "request_field_diffs": report.request_field_diffs,
                    "response_field_diffs": report.response_field_diffs,
                    "untyped_requests": report.untyped_requests,
                    "untyped_responses": report.untyped_responses,
                    "error_codes": {
                        "reachable": sorted(c for c in scan.sources if c in declared_all),
                        "implementation_ahead": sorted(
                            c for c in scan.sources if c not in declared_all
                        ),
                        "contract_ahead": sorted(c for c in declared_all if c not in scan.sources),
                        "mapped_but_unsourced": sorted(
                            c for c in scan.mapped_codes if c not in scan.sources
                        ),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"契约版本: {contract.get('info', {}).get('version')}")
        print(f"契约路径数: {len(contract['paths'])}　运行时路径数: {len(runtime['paths'])}")
        print()
        _print_report(report)
        print()
        print_error_report(scan, registries)

    return 1 if (args.strict and report.has_diff) else 0


if __name__ == "__main__":
    raise SystemExit(main())
