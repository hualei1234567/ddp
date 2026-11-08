#!/usr/bin/env python3
"""Agent Bash 守卫（Claude Code PreToolUse hook）。

目的：即使平台给 DDP 开启了 Bash，也只允许测试/构建/lint/只读命令。
- 先过高危 deny 规则；
- 再拒绝未加引号的 shell 操作符（命令链、管道、重定向、命令替换）；
- 最后要求命令匹配前缀白名单。

退出码：0 允许；2 拒绝（Claude Code 会把 stderr 反馈给模型）。
"""

from __future__ import annotations

import json
import re
import shlex
import sys

ALLOWED_PREFIXES: tuple[str, ...] = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git ls-files",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "grep",
    "pwd",
    "sed -n",
    "python -m pytest",
    "python3 -m pytest",
    "pytest",
    "python -m py_compile",
    "python3 -m py_compile",
    "ruff check",
    "ruff format --check",
    "npm run build",
    "npm run lint",
    "npm run test",
    "npx tsc",
    "npx vitest run",
    "npx playwright test",
    "go build",
    "go test",
)

DENIED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bgit\s+push\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\b",
        r"\bgit\s+checkout\s+--\s*\.",
        r"\bgit\s+branch\s+(-[dD]|--delete)\b",
        r"\bgit\s+remote\b",
        r"\brm\s+",
        r"\bmv\s+",
        r"\bdocker\b",
        r"\bkubectl\b",
        r"\bssh\b",
        r"\bscp\b",
        r"\bcurl\b",
        r"\bwget\b",
        r"\bsudo\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\balembic\b",
        r"\bmigrat",
        r"\bdeploy\b",
        r"\bdrop\s+table\b",
        r"\btruncate\b",
        r"\bdelete\s+from\b",
        r"\bfind\b.*\s-(delete|exec)\b",
        r"\bgit\s+config\b",
    )
)

META_CHARS = set(";&|`<>")


def _has_unquoted_meta(text: str) -> bool:
    quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in META_CHARS or char == "\n":
            return True
    return quote is not None


def is_allowed(command: str) -> tuple[bool, str]:
    text = (command or "").strip()
    if not text:
        return False, "空命令"

    for pattern in DENIED_PATTERNS:
        if pattern.search(text):
            return False, f"命中禁止规则 {pattern.pattern}"

    if _has_unquoted_meta(text):
        return False, "不允许命令链、管道、重定向或命令替换"

    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return False, f"命令无法解析：{exc}"

    if not parts:
        return False, "空命令"

    if any(part == ".." or part.startswith("../") for part in parts):
        return False, "不允许越界路径"

    canonical = " ".join(parts)
    if not any(canonical.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return False, "不在 Bash 白名单内"

    return True, ""


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        ok, reason = is_allowed(sys.argv[2])
        print("允许" if ok else f"拒绝：{reason}")
        return 0 if ok else 1

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"DDP Bash 守卫：无法解析 hook 输入：{exc}", file=sys.stderr)
        return 2

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    ok, reason = is_allowed(str(command))
    if not ok:
        print(f"DDP Bash 守卫拒绝：{reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
