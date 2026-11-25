#!/usr/bin/env python3
"""Adapter 六级能力档案与证据存在性校验脚本（ADR-0048 / V51-005）。

**校验目标**

防止格式能力矩阵沦为“纸面虚报”：
1. 遍历 ``BUILTIN_FORMAT_CAPABILITY_MATRIX`` 中的全部平台级格式档案，校验键名与
   ``profile.format_name`` 一致、无空限制、无跳级；
2. 遍历 ``build_default_registry().list_specs()`` 中所有已注册 Adapter 的
   ``capability_profile``，确保内置 Adapter 均显式挂载能力档案；
3. 对每条 ``evidence``（支持 ``path/to/file.py`` 或 ``path/to/file.py::symbol``
   格式）逐一核验仓库内文件真实存在，且当指定 ``::symbol`` 时，用 Python ``ast``
   精确核验顶层或类内函数/类定义确实存在（避免子串巧合命中注释）。

任何一项不通过即打印可读错误并以退出码 ``1`` 终止。
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PLATFORM_DIR = ROOT / "data-platform"
if str(DATA_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_PLATFORM_DIR))

from ddp_data.adapters.builtin import build_default_registry  # noqa: E402
from ddp_data.adapters.registry import AdapterSpec  # noqa: E402
from ddp_data.capability import (  # noqa: E402
    BUILTIN_FORMAT_CAPABILITY_MATRIX,
    FormatCapabilityLevel,
    FormatCapabilityProfile,
)

_EVIDENCE_SYMBOL_RE = re.compile(r"^(?P<path>[^:]+)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)$")


def _defined_symbols(py_file: Path) -> set[str]:
    """解析 Python 源文件并返回所有函数、异步函数与类的定义名。"""
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def verify_evidence_entry(entry: str, *, repo_root: Path = ROOT) -> str | None:
    """校验单条 evidence 引用；合法返回 ``None``，否则返回错误描述。"""
    raw = entry.strip()
    if not raw:
        return "evidence 条目为空字符串"

    match = _EVIDENCE_SYMBOL_RE.match(raw)
    rel_path = match.group("path") if match else raw
    symbol = match.group("symbol") if match else None

    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return f"evidence 必须是仓库内相对路径且不含 '..'：{raw!r}"

    target = (repo_root / candidate).resolve()
    if not target.is_file():
        return f"evidence 指向的文件不存在：{rel_path}"

    if symbol is not None:
        if target.suffix != ".py":
            return f"仅 Python 文件支持 ::symbol 证据语法：{raw}"
        try:
            symbols = _defined_symbols(target)
        except SyntaxError as exc:
            return f"evidence 目标 Python 文件语法错误（{rel_path}）：{exc}"
        if symbol not in symbols:
            return f"evidence 指向的符号不存在：{rel_path}::{symbol}"

    return None


def validate_capability_profiles(
    *,
    matrix: Mapping[str, FormatCapabilityProfile] = BUILTIN_FORMAT_CAPABILITY_MATRIX,
    adapter_specs: Iterable[AdapterSpec] | None = None,
    repo_root: Path = ROOT,
    require_adapter_profiles: bool = True,
) -> list[str]:
    """校验格式能力矩阵与已注册 Adapter 的能力档案，返回全部问题列表。"""
    problems: list[str] = []
    specs = (
        tuple(adapter_specs) if adapter_specs is not None else build_default_registry().list_specs()
    )

    if not matrix:
        problems.append("BUILTIN_FORMAT_CAPABILITY_MATRIX 不能为空")

    for key, profile in sorted(matrix.items()):
        label = f"matrix[{key!r}]"
        if key != profile.format_name:
            problems.append(
                f"{label}: 字典键 {key!r} 与 profile.format_name {profile.format_name!r} 不一致"
            )
        problems.extend(_check_profile_evidence(label, profile, repo_root=repo_root))

    for spec in specs:
        label = f"adapter[{spec.adapter_id!r}]"
        if spec.capability_profile is None:
            if require_adapter_profiles:
                problems.append(f"{label}: 内置 Adapter 未声明 capability_profile")
            continue
        problems.extend(
            _check_profile_evidence(label, spec.capability_profile, repo_root=repo_root)
        )

    return problems


def _check_profile_evidence(
    label: str,
    profile: FormatCapabilityProfile,
    *,
    repo_root: Path,
) -> list[str]:
    errors: list[str] = []
    if profile.max_level.rank > FormatCapabilityLevel.DETECT.rank and not profile.evidence:
        errors.append(f"{label}: max_level={profile.max_level.value} 但 evidence 为空")
    if not profile.limitations:
        errors.append(f"{label}: limitations 为空")
    for entry in profile.evidence:
        problem = verify_evidence_entry(entry, repo_root=repo_root)
        if problem is not None:
            errors.append(f"{label}: {problem}")
    return errors


def main() -> int:
    specs = build_default_registry().list_specs()
    problems = validate_capability_profiles(
        matrix=BUILTIN_FORMAT_CAPABILITY_MATRIX,
        adapter_specs=specs,
        repo_root=ROOT,
    )
    if problems:
        print("Adapter 能力矩阵校验未通过：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(
        f"Adapter 能力矩阵校验通过：{len(BUILTIN_FORMAT_CAPABILITY_MATRIX)} 个平台格式档案、"
        f"{len(specs)} 个内置 Adapter 档案，全部 evidence 均真实存在。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
