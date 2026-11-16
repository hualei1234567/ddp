"""场地域命令对象。

与既有各域一致：命令只承载"客户端可提交的字段"，状态、``project_id``、
``version``、审计字段等权威值一律由服务端派生，不接受客户端传入。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NewLocation:
    code: str
    name: str
    kind: str = "SITE"
    address: str | None = None
    notes: str | None = None


__all__ = ["NewLocation"]
