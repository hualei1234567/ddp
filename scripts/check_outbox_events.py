"""outbox 事件自检：投递类必须有消费者与测试（R-13 / T-4、T-5）。

背景：`outbox_events` 同时承担"变更留痕"与"集成投递"两种职责。历史上有 83 种
事件类型、只有 2 种有消费者，其余永久停在 PENDING，无法区分"漏了消费者"与
"本来就不该有消费"。`docs/architecture/outbox事件语义界定-v1.0.md` 定下规则：
每个事件类型必须显式归入投递类或留痕类。

本脚本做三件事：

1. **提取**：扫描源码里所有事件类型字符串（字面量与模块级常量两种写法）。
   旧脚本只按 `event_type="..."` 字面匹配，漏掉了常量形式（如
   `dataset.export.requested` 来自 `EXPORT_REQUESTED_EVENT`）——那正是 T-5。
2. **校验归类**：投递类事件必须有 worker 侧消费者，且在测试里被引用。
3. **防止回归**：投递类集合与 `DISPATCH_EVENTS` 白名单必须完全一致，
   新增投递类必须同时更新白名单与消费者，否则 CI 失败。

用法：``python3 scripts/check_outbox_events.py``（CI 与 `make check` 调用）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# 投递类事件白名单：有执行方需要据此做事，没有消费者就是缺陷。
# 新增投递类事件必须在这里登记，并在 backend/app/workers 里有消费者。
DISPATCH_EVENTS = {
    "ingestion.raw_asset.validation_requested": "backend/app/workers/outbox.py",
    "dataset.export.requested": "backend/app/workers/exports.py",
    "datasource.import_requested": "backend/app/workers/datasources.py",
}

_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """收集模块级 ``NAME = "event.type"`` 常量。"""
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if _EVENT_PATTERN.match(value.value):
                constants[target.id] = value.value
    return constants


def _collect(path: Path) -> tuple[set[str], set[str]]:
    """返回 (字面量事件, 常量引用事件)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _string_constants(tree)
    literals: set[str] = set()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _EVENT_PATTERN.match(node.value):
                literals.add(node.value)
        elif isinstance(node, ast.Name) and node.id in constants:
            referenced.add(constants[node.id])
    return literals, referenced


def _literal_or_constant(node: ast.expr | None, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _dispatch_events(tree: ast.Module, constants: dict[str, str]) -> set[str]:
    """找出所有"event_type=X 且 dispatch=True"的构造点。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if not keywords:
            continue
        dispatch = keywords.get("dispatch")
        if not (isinstance(dispatch, ast.Constant) and dispatch.value is True):
            continue
        event_type = _literal_or_constant(keywords.get("event_type"), constants)
        if event_type:
            found.add(event_type)
    return found


def _source_files() -> list[Path]:
    files: list[Path] = []
    for base in (BACKEND / "app", BACKEND / "tests"):
        files.extend(sorted(base.rglob("*.py")))
    return files


def main() -> int:
    if not BACKEND.is_dir():
        print(f"缺少后端目录：{BACKEND}", file=sys.stderr)
        return 2

    app_literals: set[str] = set()
    app_constants: set[str] = set()
    test_text: list[str] = []
    app_text: list[str] = []

    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        literals, constants = _collect(path)
        if "tests" in path.parts:
            test_text.append(text)
        else:
            app_literals |= literals
            app_constants |= constants
            app_text.append(text)

    events = app_literals | app_constants
    # 只保留形如 domain.action 的领域事件，排除 URL、文件名等误命中。
    events = {
        item
        for item in events
        if item.split(".")[0]
        in {
            "annotation",
            "collection",
            "dataset",
            "datasource",
            "device",
            "sensor",
            "ingestion",
            "modelops",
            "supplier",
            "training",
        }
    }

    problems: list[str] = []

    # 1) 投递类必须有消费者文件，且该文件里确实出现该事件类型。
    for event_type, consumer in sorted(DISPATCH_EVENTS.items()):
        consumer_path = ROOT / consumer
        if not consumer_path.is_file():
            problems.append(f"投递类 {event_type} 的消费者文件不存在：{consumer}")
            continue
        if event_type not in consumer_path.read_text(encoding="utf-8"):
            problems.append(f"投递类 {event_type} 未在 {consumer} 中出现（声明与实现不符）")
        # 2) 投递类必须有测试引用（T-4 的核心要求）。
        if not any(event_type in text for text in test_text):
            problems.append(f"投递类 {event_type} 没有任何测试引用它")

    # 3) 源码里标记为 dispatch=True 的事件必须在白名单里，反之亦然。
    #    用 AST 找"同一次构造里既有 event_type 又有 dispatch=True"的位置，
    #    而不是文本正则——正则会被注释与跨语句误匹配。
    declared_dispatch: set[str] = set()
    for path in _source_files():
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _string_constants(tree)
        declared_dispatch |= _dispatch_events(tree, constants)
    for event_type in sorted(declared_dispatch - set(DISPATCH_EVENTS)):
        problems.append(
            f"{event_type} 在源码里标了 dispatch=True，但未登记在 DISPATCH_EVENTS 白名单"
        )
    for event_type in sorted(set(DISPATCH_EVENTS) - declared_dispatch):
        problems.append(
            f"{event_type} 在白名单里，但源码里找不到 dispatch=True 的写入点"
        )

    print(f"扫描到 {len(events)} 种事件类型；投递类 {len(DISPATCH_EVENTS)} 种，"
          f"留痕类 {len(events) - len(DISPATCH_EVENTS)} 种。")
    for event_type in sorted(DISPATCH_EVENTS):
        print(f"  [DISPATCH] {event_type} → {DISPATCH_EVENTS[event_type]}")

    if problems:
        print("\noutbox 事件自检未通过：", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("\noutbox 事件自检通过：投递类都有消费者与测试，归类与源码一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
