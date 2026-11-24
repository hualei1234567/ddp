#!/usr/bin/env python3
"""记忆库 CI 校验（``docs/memory/``）。

**这个脚本存在的唯一理由**：记忆库没有校验必然腐烂——卡片写错格式、check_ref
指向已删除的测试、索引与卡片不一致。三者任何一个发生，"记忆"就变成"不可信的
文档"，比没有更糟（会误导人）。

校验四件事：
1. **格式**：卡片符合模板（ID/类型/状态/tags/check_ref 齐全，类型目录一致）
2. **索引**：``MEMORY_INDEX.md`` 与卡片内容一致（防手改漂移）
3. **check_ref 真实存在**：INC / DEC / SOP 卡片指向的测试/脚本必须能找到
4. **ID 唯一**：不允许重复 ID（会破坏交叉引用）

**不校验内容质量**——那无法自动化。质量靠 review 与 ``recurrence`` 追踪。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from build_memory_index import (
    INDEX_PATH,
    MEMORY_DIR,
    TYPE_DIRS,
    collect_cards,
    parse_card,
    render_index,
)

ROOT = Path(__file__).resolve().parents[1]

#: 这些类型的 check_ref **必填且必须指向真实文件**。
#: 理由：INC 是"这个坑怎么拦"、DEC 是"这个决策怎么守住"、SOP 是"这个流程怎么执行"——
#: 三者都是可执行的，没有拦截点就只是日记。
REQUIRE_CHECK_REF = ("INC", "DEC", "SOP")

#: 允许的 check_ref 占位（确实无法自动化时）。出现这些词会被放行但不要求文件存在。
_PLACEHOLDER_MARKERS = ("待建", "待补", "无（", "N/A")

_TEST_NAME_RE = re.compile(r"::([A-Za-z_][A-Za-z0-9_]*)$")


def _extract_paths(check_ref: str) -> list[str]:
    """从 check_ref 提取文件路径（可能含多个，用 + 或顿号分隔）。

    支持形式：
    - ``path/to/file.py``
    - ``path/to/file.py::test_name``
    - ``path/a.py + path/b.py``
    - ``path/a.py（说明文字）``
    """
    if not check_ref:
        return []
    # 去掉反引号与括号说明
    cleaned = check_ref.replace("`", "")
    cleaned = re.split(r"[（(]", cleaned, maxsplit=1)[0]
    parts = re.split(r"\s*\+\s*", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _check_ref_problems(card) -> list[str]:
    problems: list[str] = []
    if card.memory_type not in REQUIRE_CHECK_REF:
        return problems

    if not card.check_ref:
        return [f"{card.card_id}: {card.memory_type} 类必须填 check_ref"]

    if any(marker in card.check_ref for marker in _PLACEHOLDER_MARKERS):
        # 显式声明"待建"是允许的（坦白优于假装），但会被索引标注出来
        return problems

    for raw in _extract_paths(card.check_ref):
        test_match = _TEST_NAME_RE.search(raw)
        file_part = _TEST_NAME_RE.sub("", raw) if test_match else raw
        target = ROOT / file_part
        if not target.exists():
            problems.append(f"{card.card_id}: check_ref 指向的文件不存在：{file_part}")
            continue
        # 若指定了测试函数名，必须在文件里真的出现
        if test_match:
            test_name = test_match.group(1)
            if test_name not in target.read_text(encoding="utf-8"):
                problems.append(
                    f"{card.card_id}: check_ref 指向的测试不存在：{file_part}::{test_name}"
                )
    return problems


def main() -> int:
    problems: list[str] = []

    if not MEMORY_DIR.is_dir():
        print(f"记忆目录不存在：{MEMORY_DIR}", file=sys.stderr)
        return 1

    # 1) 收集（collect_cards 内部已做格式解析）
    cards = collect_cards()
    for card in cards:
        for problem in card.problems:
            problems.append(f"{card.path}: {problem}")

    # 2) ID 唯一
    seen: dict[str, str] = {}
    for card in cards:
        if not card.card_id:
            continue
        if card.card_id in seen:
            problems.append(
                f"ID 重复：{card.card_id} 同时出现在 {seen[card.card_id]} 与 {card.path}"
            )
        else:
            seen[card.card_id] = card.path

    # 3) check_ref 有效性
    for card in cards:
        problems.extend(_check_ref_problems(card))

    # 4) 索引一致性
    if not INDEX_PATH.is_file():
        problems.append(f"索引不存在：{INDEX_PATH.relative_to(ROOT)}")
    else:
        expected = render_index(cards)
        actual = INDEX_PATH.read_text(encoding="utf-8")
        if actual != expected:
            problems.append(
                "MEMORY_INDEX.md 与卡片不一致；运行 python3 scripts/build_memory_index.py"
            )

    # 5) 模板与架构文档存在性（缺了这套就不完整）
    for required in ("ARCHITECTURE.md", "templates/memory-card.md"):
        if not (MEMORY_DIR / required).is_file():
            problems.append(f"缺少必需文件：docs/memory/{required}")

    # 6) 空目录提示（有类型目录但无卡片，多半是忘了）
    for memory_type, directory in TYPE_DIRS.items():
        target = MEMORY_DIR / directory
        if target.is_dir() and not any(target.glob("MEM-*.md")):
            print(f"提示：docs/memory/{directory}/ 为空（类型 {memory_type} 暂无卡片）")

    if problems:
        print("记忆库校验未通过：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    active = sum(1 for c in cards if c.status == "ACTIVE")
    recurring = [c for c in cards if c.recurrence > 1]
    print(
        f"记忆库校验通过：{len(cards)} 张卡片（ACTIVE {active}），"
        f"check_ref 全部有效，索引一致。"
    )
    if recurring:
        print(
            f"注意：{len(recurring)} 张卡片复发 >1 —— "
            + ", ".join(c.card_id for c in recurring)
            + "（考虑升级自动化约束）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
