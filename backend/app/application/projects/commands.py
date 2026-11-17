"""项目写入命令（与 HTTP 请求模型解耦）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NewProject:
    """创建项目的输入；规范化与校验在领域层 ``new_project`` 完成。"""

    code: str
    name: str
    description: str | None = None


__all__ = ["NewProject"]
