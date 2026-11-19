"""采集会话产物只读投影（N9-008）。

会话与产物的归属是产物的**固有属性**（``raw_assets.capture_session_id`` /
``episodes.capture_session_id``），本模块只把两张权威表投影成统一列表，
不落库、不参与状态机、不反向修改任何实体。

为什么单独成文件而不是塞进 ``models.py``：后者已接近 500 行评审阈值；
产物投影是只读视图，与可写的会话聚合职责不同，拆开两边都保持可审阅。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class CaptureSessionArtifactKind(StrEnum):
    """会话产物的权威来源类型；取值与 ``AssetKind`` 中对应项一致。"""

    RAW_ASSET = "RAW_ASSET"
    EPISODE = "EPISODE"


@dataclass(frozen=True, slots=True)
class CaptureSessionArtifact:
    """一条归属某采集会话的产物；``artifact_id`` 是来源表主键。"""

    kind: CaptureSessionArtifactKind
    artifact_id: UUID
    code: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactCursor:
    """复合键集游标：``(created_at DESC, kind ASC, artifact_id ASC)``。

    两张来源表的主键各自独立，单 UUID 游标无法表达跨表排序，因此用复合游标；
    格式与 ``application/catalog/cursor.py`` 一致（base64url(JSON)），但不复用
    那个类型——它绑定 ``AssetKind``，强行复用会把两个只读视图耦合成一处。
    """

    created_at: datetime
    kind: CaptureSessionArtifactKind
    artifact_id: UUID


__all__ = [
    "ArtifactCursor",
    "CaptureSessionArtifact",
    "CaptureSessionArtifactKind",
]
