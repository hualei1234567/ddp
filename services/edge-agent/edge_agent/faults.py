"""崩溃注入钩子。

生产环境使用 ``no_crash``；测试传入会在指定步骤抛异常的钩子，模拟进程在
各步骤之间被杀，再用全新对象从磁盘 + SQLite 恢复。这是真实的故障注入，
不是对文件系统的 mock。
"""

from __future__ import annotations

from collections.abc import Callable

CrashHook = Callable[[str], None]


def no_crash(point: str) -> None:
    """生产默认：不注入任何故障。"""
