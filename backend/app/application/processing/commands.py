"""处理运行应用层命令 DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewProcessingRun:
    """创建一个 ProcessingRun 所需的最小输入。

    ``project_id`` 与创建人不在此处：它们只能来自服务端校验后的
    :class:`app.domain.identity.models.ProjectScope`。

    ``raw_asset_id`` 与 ``input_refs`` 二选一（V31-015）：

    - 给 ``raw_asset_id``：服务端从 RawAsset 记录解析对象 key，并校验资产属于
      当前项目、状态为 ``VALID`` / ``ARCHIVED``。客户端因此不需要知道对象存储
      的 key 布局。
    - 给 ``input_refs``：兼容既有调用方，直接使用裸引用（如 canonical 前缀）。
    """

    pipeline_version: str
    input_refs: tuple[str, ...] = ()
    raw_asset_id: UUID | None = None


__all__ = ["NewProcessingRun"]
