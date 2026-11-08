"""Agent Bash 守卫测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GUARD_PATH = Path(__file__).resolve().parents[1] / "agent_bash_guard.py"
spec = importlib.util.spec_from_file_location("agent_bash_guard", GUARD_PATH)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff --stat",
        "python -m pytest -q",
        "ruff check app",
        "npm run build",
        "npx tsc --noEmit",
        "ls -la",
        "grep -r 'foo|bar' src",
        "sed -n '1,20p' README.md",
    ],
)
def test_allowed(command: str) -> None:
    ok, reason = guard.is_allowed(command)
    assert ok, reason


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git reset --hard HEAD~1",
        "git branch -D main",
        "rm -rf /tmp/x",
        "docker compose up -d",
        "ssh user@host",
        "curl http://example.com",
        "alembic upgrade head",
        "echo hi && git push",
        "cat README.md | grep foo",
        "python -m pytest > /tmp/out",
        "find . -name '*.py' -delete",
        "../outside/script.sh",
        "",
    ],
)
def test_denied(command: str) -> None:
    ok, _ = guard.is_allowed(command)
    assert not ok, f"{command!r} 不应被允许"
