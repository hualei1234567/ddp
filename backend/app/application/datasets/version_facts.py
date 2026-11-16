"""数据集版本可核对事实的读取端口（TRN-002）。

交付用例需要「版本里到底有什么」这类事实来核对需求，但它不该知道这些事实
散落在 ``dataset_items`` / ``episodes`` / ``annotation_records`` 的哪些列里。
本端口只暴露一个方法：给定服务端 ``ProjectScope`` 与版本 ID，返回已被投影的
事实；未投影的信息以 ``None`` 表达，由领域层判 ``UNVERIFIABLE``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope


@dataclass(frozen=True, slots=True)
class VersionFactProjection:
    """版本内可核对的明细事实。

    与 ``VersionFacts`` 的区别：这里只放「需要额外查询才能拿到的明细」，
    ``None`` 仍然表示"未投影"，绝不用空集合冒充。
    """

    scene_labels: frozenset[str] | None = None
    annotation_labels: dict[str, frozenset[str]] | None = field(default=None)


class VersionFactDirectory(Protocol):
    def project(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> VersionFactProjection: ...


__all__ = ["VersionFactDirectory", "VersionFactProjection"]
