#!/usr/bin/env python3
"""生成记忆索引 ``docs/memory/MEMORY_INDEX.md``。

**为什么不手写索引**：手写索引必然漂移——新增卡片忘了加、卡片改状态索引没更新。
本脚本从卡片文件**推导**索引，CI 再用 ``check_memory.py`` 校验索引与卡片一致，
两边夹住就不可能漂移。

**解析策略**：只认卡片模板里的固定字段行（``- **字段名**：值``）。这不是通用
Markdown 解析器——它是**针对本仓库记忆卡片格式**的解析器，格式变了就该改这里，
而不是猜。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / "docs" / "memory"
INDEX_PATH = MEMORY_DIR / "MEMORY_INDEX.md"

#: 类型 → 目录名。新增类型必须同时改这里与 ARCHITECTURE.md §2.1。
TYPE_DIRS: dict[str, str] = {
    "REQ": "requirements",
    "DEC": "decisions",
    "SOP": "sop",
    "INC": "incidents",
    "CHG": "changes",
    "OPS": "ops",
}

TYPE_LABELS: dict[str, str] = {
    "REQ": "需求与验收口径",
    "DEC": "决策",
    "SOP": "标准操作流程",
    "INC": "事故与缺陷复盘",
    "CHG": "变更记录",
    "OPS": "运维参数与门禁",
}

STATUSES = ("DRAFT", "ACTIVE", "SUPERSEDED", "DEPRECATED")

_TITLE_RE = re.compile(r"^###\s+(MEM-[A-Z]+-\d{3})\s*\|\s*(.+?)\s*$")
_FIELD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*[：:]\s*(.*)$")
#: 卡片 ID 形如 MEM-INC-001
_ID_RE = re.compile(r"^MEM-(REQ|DEC|SOP|INC|CHG|OPS)-(\d{3})$")


@dataclass
class Card:
    card_id: str
    title: str
    memory_type: str
    status: str
    tags: tuple[str, ...] = ()
    check_ref: str = ""
    recurrence: int = 1
    path: str = ""
    problems: list[str] = field(default_factory=list)


def _read_field(fields: dict[str, str], name: str) -> str:
    return fields.get(name, "").strip()


def parse_card(path: Path) -> Card | None:
    """解析一张卡片；格式不符返回带 problems 的 Card（供 check 报告）。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    card_id = ""
    title = ""
    for line in lines:
        m = _TITLE_RE.match(line)
        if m:
            card_id, title = m.group(1), m.group(2)
            break

    if not card_id:
        return Card(
            card_id="",
            title="",
            memory_type="",
            status="",
            path=str(path.relative_to(ROOT)),
            problems=["缺少标题行（形如 '### MEM-INC-001 | 标题'）"],
        )

    id_match = _ID_RE.match(card_id)
    memory_type = id_match.group(1) if id_match else ""

    fields: dict[str, str] = {}
    for line in lines:
        m = _FIELD_RE.match(line)
        if m:
            fields[m.group(1).strip()] = m.group(2)

    # 「类型与状态」是合并字段：``REQ / DEC / ... | 状态：`DRAFT` / ...``
    # 值里可能带反引号（模板要求类型与状态用代码格式），先剥离再匹配。
    type_status = _read_field(fields, "类型与状态").replace("`", "")
    status = ""
    if "状态" in type_status:
        after = type_status.split("状态", 1)[1].lstrip("：: ").strip()
        for candidate in STATUSES:
            if after.startswith(candidate):
                status = candidate
                break

    raw_tags = _read_field(fields, "tags").strip("`")
    tags = tuple(sorted(t.strip() for t in raw_tags.split(",") if t.strip()))

    raw_check = _read_field(fields, "check_ref").strip("`")
    raw_rec = _read_field(fields, "recurrence").strip("`")

    problems: list[str] = []
    if not memory_type:
        problems.append(f"卡片 ID 不含合法类型段：{card_id}")
    if not status:
        problems.append("缺少或无法识别状态（DRAFT/ACTIVE/SUPERSEDED/DEPRECATED）")
    if not tags:
        problems.append("缺少 tags")
    if not raw_check:
        problems.append("缺少 check_ref")
    try:
        recurrence = int(raw_rec) if raw_rec else 1
    except ValueError:
        recurrence = 1
        problems.append(f"recurrence 不是整数：{raw_rec}")

    # 类型必须与所在目录一致
    expected_dir = TYPE_DIRS.get(memory_type)
    if expected_dir and path.parent.name != expected_dir:
        problems.append(
            f"类型 {memory_type} 应放在 {expected_dir}/ 下，实际在 {path.parent.name}/"
        )

    return Card(
        card_id=card_id,
        title=title,
        memory_type=memory_type,
        status=status,
        tags=tags,
        check_ref=raw_check,
        recurrence=recurrence,
        path=str(path.relative_to(ROOT)),
        problems=problems,
    )


def collect_cards() -> list[Card]:
    cards: list[Card] = []
    for directory in TYPE_DIRS.values():
        target = MEMORY_DIR / directory
        if not target.is_dir():
            continue
        for path in sorted(target.glob("MEM-*.md")):
            card = parse_card(path)
            if card is not None:
                cards.append(card)
    return cards


def render_index(cards: list[Card]) -> str:
    active = [c for c in cards if c.status == "ACTIVE"]
    out: list[str] = [
        "# 记忆索引（自动生成，勿手改）",
        "",
        "> 本文件由 `python3 scripts/build_memory_index.py` 生成。",
        "> 手改会被 `scripts/check_memory.py` 判为漂移并让 CI 失败。",
        "> 新增/修改记忆请改卡片文件，然后重新生成本索引。",
        "",
        f"**总计 {len(cards)} 张卡片**："
        + " / ".join(
            f"{status} {sum(1 for c in cards if c.status == status)}" for status in STATUSES
        ),
        "",
        f"其中 **ACTIVE {len(active)} 张**（只有 ACTIVE 可被当作约束引用）。",
        "",
        "---",
        "",
    ]

    # 按类型分组
    for memory_type, label in TYPE_LABELS.items():
        group = [c for c in cards if c.memory_type == memory_type]
        if not group:
            continue
        out.append(f"## {memory_type} · {label}（{len(group)}）")
        out.append("")
        out.append("| ID | 标题 | 状态 | 复发 | tags |")
        out.append("|---|---|---|---|---|")
        for card in sorted(group, key=lambda c: c.card_id):
            tags = ", ".join(f"`{t}`" for t in card.tags)
            recurrence = f"**{card.recurrence}**" if card.recurrence > 1 else str(card.recurrence)
            out.append(
                f"| [{card.card_id}]({_relative_link(card.path)}) | {card.title} "
                f"| {card.status} | {recurrence} | {tags} |"
            )
        out.append("")

    # 复发项单列（这是最需要关注的：说明上一轮记忆没生效）
    recurring = [c for c in cards if c.recurrence > 1]
    out.append("---")
    out.append("")
    out.append("## 复发的坑（recurrence > 1）")
    out.append("")
    if recurring:
        out.append("> 复发说明「记住」没生效，应升级为更强的自动化约束。")
        out.append("")
        out.append("| ID | 标题 | 复发 | 现有 check |")
        out.append("|---|---|---|---|")
        for card in sorted(recurring, key=lambda c: (-c.recurrence, c.card_id)):
            out.append(
                f"| [{card.card_id}]({_relative_link(card.path)}) | {card.title} "
                f"| **{card.recurrence}** | `{card.check_ref}` |"
            )
    else:
        out.append("无。")
    out.append("")

    # 非 ACTIVE 单列
    inactive = [c for c in cards if c.status != "ACTIVE"]
    out.append("---")
    out.append("")
    out.append("## 非 ACTIVE（不可作为现行约束）")
    out.append("")
    if inactive:
        out.append("| ID | 标题 | 状态 |")
        out.append("|---|---|---|")
        for card in sorted(inactive, key=lambda c: c.card_id):
            out.append(
                f"| [{card.card_id}]({_relative_link(card.path)}) | {card.title} | {card.status} |"
            )
    else:
        out.append("无。")
    out.append("")

    # tags 交叉索引（按类型分目录后，跨类型关联只能靠 tag）
    tag_map: dict[str, list[str]] = {}
    for card in cards:
        for tag in card.tags:
            tag_map.setdefault(tag, []).append(card.card_id)
    out.append("---")
    out.append("")
    out.append("## 按 tag 检索（跨类型）")
    out.append("")
    for tag in sorted(tag_map):
        ids = ", ".join(sorted(tag_map[tag]))
        out.append(f"- `{tag}`：{ids}")
    out.append("")

    return "\n".join(out)


def _relative_link(path: str) -> str:
    """索引在 docs/memory/ 下，卡片在子目录，链接用相对路径。"""
    return path.replace("docs/memory/", "", 1)


def main(argv: list[str]) -> int:
    write_mode = "--check" not in argv
    cards = collect_cards()

    with_problems = [c for c in cards if c.problems]
    if with_problems:
        print("卡片格式有问题，索引未生成：", file=sys.stderr)
        for card in with_problems:
            for problem in card.problems:
                print(f"  - {card.path}: {problem}", file=sys.stderr)
        return 1

    rendered = render_index(cards)

    if write_mode:
        INDEX_PATH.write_text(rendered, encoding="utf-8")
        print(f"已生成 {INDEX_PATH.relative_to(ROOT)}（{len(cards)} 张卡片）")
        return 0

    # --check：比对内容
    if not INDEX_PATH.is_file():
        print(f"索引不存在：{INDEX_PATH.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if INDEX_PATH.read_text(encoding="utf-8") != rendered:
        print(
            "索引与卡片不一致（有人手改了索引，或新增卡片后没重新生成）。\n"
            "请运行：python3 scripts/build_memory_index.py",
            file=sys.stderr,
        )
        return 1
    print(f"索引一致（{len(cards)} 张卡片）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
