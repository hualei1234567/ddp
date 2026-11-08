#!/usr/bin/env python3
"""Secret 扫描：阻断明显的密钥/令牌误提交。

阶段 0 扫描代码与配置；文档中的示例文本不扫，避免把规范里的占位符当密钥。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "docs",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".docx", ".pptx", ".lock"}
SKIP_NAMES = {".env.example", "package-lock.json"}
MAX_BYTES = 1024 * 1024

PLACEHOLDERS = re.compile(
    r"change-me|replace|example|placeholder|minioadmin|your-|xxx|todo",
    re.IGNORECASE,
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private Key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("GitHub PAT", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("API Key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    (
        "Generic Secret",
        re.compile(
            r"(?i)\b(password|passwd|secret|token)\b\s*[:=]\s*['\"]([^'\"]{8,})['\"]"
        ),
    ),
]


def iter_files() -> list[Path]:
    try:
        output = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        files = [ROOT / line for line in output.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [path for path in ROOT.rglob("*") if path.is_file()]

    result: list[Path] = []
    for path in files:
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        if path.stat().st_size > MAX_BYTES:
            continue
        result.append(path)
    return result


def scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings

    for line_number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if name == "Generic Secret" and PLACEHOLDERS.search(match.group(2)):
                continue
            findings.append(f"{path.relative_to(ROOT)}:{line_number}: {name}")
    return findings


def main() -> int:
    findings: list[str] = []
    for path in iter_files():
        findings.extend(scan_file(path))

    if findings:
        print("发现疑似密钥/令牌，请移除后重试：", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print("Secret 扫描通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
