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

# 事件归类文档：投递类/留痕类的权威登记处。脚本只校验"投递类必须有消费者"，
# 不逐条校验"每个事件是否都写进了文档"——那会让 192 种事件每种都要维护一行，
# 而文档本身的价值在于说清**投递类**与**共用事件**的语义。因此这里只做一件事：
# 保证 DISPATCH_EVENTS 里的每个事件都在文档中出现，避免"代码改了文档没改"。
SEMANTICS_DOC = ROOT / "docs" / "architecture" / "outbox事件语义界定-v1.0.md"


# 投递类事件白名单：有执行方需要据此做事，没有消费者就是缺陷。
# 新增投递类事件必须在这里登记，并在 backend/app/workers 里有消费者。
DISPATCH_EVENTS = {
    "ingestion.raw_asset.validation_requested": "backend/app/workers/outbox.py",
    "dataset.export.requested": "backend/app/workers/exports.py",
    "datasource.import_requested": "backend/app/workers/datasources.py",
    "ingestion.job.created": "backend/app/workers/ingestion_jobs.py",
}

# 事件名允许段内连字符：设备域生命周期动作名沿用 API 路径写法
# （`report-fault` / `enter-maintenance`），历史事件名已经是这个形状。
# R-13 的另一半缺陷就出在这里：旧正则只认 `[a-z0-9_]`，即便把 f-string
# 改成字面量，含连字符的事件仍然一个都提取不到，校验形同虚设。
# 放宽只影响段内字符，不改变"至少两段、首段小写字母开头"的结构约束；
# 实测额外命中只有设备域动作事件与 modelops 的一个既有动作名。
_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$")


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


# 事件名允许的域前缀：只有这些开头的字面量才被当作领域事件，
# 用于把 URL、文件名之类的误命中排除掉（提取与 f-string 守卫共用）。
_EVENT_DOMAINS = frozenset(
    {
        "annotation",
        "calibration",
        "calibration_profile",
        "collection",
        "dataset",
        "datasource",
        "dedup_policy",
        "device",
        "ingestion",
        "leakage_policy",
        "location",
        "modelops",
        # 阶段 8 项目管理（project.created / updated / member_*）：
        # 首段不在此表中时归类校验会静默跳过，等于五个新事件不受守卫。
        "project",
        "quality_policy",
        "robot",
        "sensor",
        "split_policy",
        "supplier",
        "training",
    }
)
# 说明：`robot` 与四个 `*_policy` 首段是**已有事件名**（`robot.retire`、
# `dedup_policy.created` 等），此前不在白名单里导致它们被静默过滤——
# 既不计入总数，也不会因"未归类"失败。事件名是可观测契约，改名会让已落库的
# outbox 记录与历史消费者失配，所以选择补白名单而不是改名。


def _event_like(value: str) -> bool:
    """形如 ``domain.action`` 且首段是已知领域。"""
    return bool(_EVENT_PATTERN.match(value)) and value.split(".")[0] in _EVENT_DOMAINS


def _fstring_event_types(tree: ast.Module) -> list[tuple[int, str]]:
    """找出 ``event_type=f"..."`` 形式的写法，返回 (行号, 源码片段)。

    R-13 的根因就是这种写法：AST 提取拿不到运行时才拼出来的字符串，事件类型
    因而静默逃过归类校验——既不进 events 计数，也不会因"未归类"而失败。
    设备域已全部改成模块级常量；本守卫负责让**新出现的** f-string 立刻失败，
    而不是等到下次人工审计才发现。

    只认 ``event_type=`` 关键字参数，避免把日志文案里的 f-string 误判。
    """
    offenders: list[tuple[int, str]] = []
    for keyword_parent in ast.walk(tree):
        if not isinstance(keyword_parent, ast.Call):
            continue
        for keyword in keyword_parent.keywords:
            if keyword.arg != "event_type":
                continue
            value = keyword.value
            if isinstance(value, ast.JoinedStr):
                literal = "".join(
                    part.value
                    for part in value.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                offenders.append((keyword_parent.lineno, f'event_type=f"{literal}..."'))
    return offenders


def _event_typed_names_outside_domains() -> set[str]:
    """``event_type=`` 位置上出现的、但不属于已知领域的事件名。

    这是"静默过滤"守卫的精确版：只认事件类型出现的位置，不扫全量字符串。
    两类位置：
    1. ``record_change(..., event_type="x.y")`` 的字面量；
    2. ``record_change(..., event_type=SOME_CONST)`` 其中 ``SOME_CONST`` 是模块级字符串常量；
    3. 模块级 ``NAME: dict[str, str] = {"action": "x.y"}`` 映射表的**值**
       （这是本轮把 f-string 改造后的主要形态）。
    """
    found: set[str] = set()
    for path in _source_files():
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _string_constants(tree)
        # 1) event_type= 关键字位置
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "event_type":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
                elif isinstance(value, ast.Name) and value.id in constants:
                    found.add(constants[value.id])
        # 3) 模块级映射表的值（键→事件名）
        for node in tree.body:
            targets: list[ast.expr] = []
            value_node: ast.expr | None = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
                targets = [node.target]
                value_node = node.value
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                targets = list(node.targets)
                value_node = node.value
            if value_node is None or not any(
                isinstance(t, ast.Name) and t.id.endswith("_EVENTS") for t in targets
            ):
                continue
            for item in value_node.values:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    found.add(item.value)
    return {
        item
        for item in found
        if _EVENT_PATTERN.match(item) and item.split(".")[0] not in _EVENT_DOMAINS
    }


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
    # **但被排除的项不能静默丢弃**：`dedup_policy.created` / `split_policy.created`
    # 这类事件的首段不在白名单里，此前会被无声过滤掉——它们既不计入总数，
    # 也不会因"未归类"失败，等于绕过了整道校验。这里把"看起来像事件、
    # 首段却不在白名单"的项收集起来报警，迫使维护者显式决定是补白名单还是改名。
    events = {item for item in events if item.split(".")[0] in _EVENT_DOMAINS}
    # 被过滤掉的不静默丢弃：只在**确实是事件类型的位置**（``event_type=`` 关键字、
    # 或模块级事件常量）判定，否则 ``vote.decision`` / ``value_spec.unit``
    # 这类普通字段路径会全是假阳性。
    unknown_domain = _event_typed_names_outside_domains()

    problems: list[str] = []

    # 0) 事件类型不得用 f-string 拼接。f-string 的事件名提取不到，
    #    会静默逃过下面的归类校验（R-13 的根因）。
    for path in _source_files():
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, snippet in _fstring_event_types(tree):
            rel = path.relative_to(ROOT)
            problems.append(
                f"{rel}:{lineno} 用 f-string 拼接事件类型（{snippet}）；"
                "请改成模块级常量或字面量，否则归类校验无法覆盖它"
            )

    # 0.5) 投递类事件必须在归类文档里登记。文档是团队理解"为什么这个事件
    #      需要消费者"的唯一出处；代码加了白名单却没更新文档，下次维护者
    #      就看不出它为何特殊。
    if SEMANTICS_DOC.is_file():
        semantics_text = SEMANTICS_DOC.read_text(encoding="utf-8")
        for event_type in sorted(DISPATCH_EVENTS):
            if event_type not in semantics_text:
                problems.append(
                    f"投递类 {event_type} 未登记在 {SEMANTICS_DOC.name}，"
                    "请补充其语义说明"
                )
    else:
        problems.append(f"归类文档不存在：{SEMANTICS_DOC}")

    # 0.8) 首段不在白名单、但形态像事件名的字符串必须显式处理，不能静默跳过。
    #      判据没法定死（`dedup_policy.created` 真的可能是事件名，也可能只是
    #      普通文案），因此这里只要求"别无视它"：确有此事件就往 _EVENT_DOMAINS
    #      补首段，不是事件就把名字改成规范前缀。
    for item in sorted(unknown_domain):
        problems.append(
            f"{item} 形态像领域事件但首段不在 _EVENT_DOMAINS，已被归类校验静默跳过；"
            "请确认它是事件（则补首段白名单）还是普通字符串（则改名）"
        )

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

    print(
        "\noutbox 事件自检通过：投递类都有消费者、测试与文档登记；"
        "源码中无 f-string 事件类型。\n"
        "（留痕类不逐条校验是否写进归类文档——文档只登记投递类与共用事件语义。）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
