"""处理运行应用层命令 DTO。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NewProcessingRun:
    """创建一个 ProcessingRun 所需的最小输入。

    ``project_id`` 与创建人不在此处：它们只能来自服务端校验后的
    :class:`app.domain.identity.models.ProjectScope`。
    """

    pipeline_version: str
    input_refs: tuple[str, ...]


__all__ = ["NewProcessingRun"]
