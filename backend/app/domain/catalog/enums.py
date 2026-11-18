"""统一资产目录的资源类型。

取值与 ``LineageNodeType`` 中对应的节点类型保持一致，避免客户端为了跨域检索
再学一套命名。新增类型必须同时有权威表与索引前缀字段，否则不得加入本枚举。
"""

from __future__ import annotations

from enum import StrEnum


class AssetKind(StrEnum):
    RAW_ASSET = "RAW_ASSET"
    EPISODE = "EPISODE"
    DATASET = "DATASET"
    DATASET_VERSION = "DATASET_VERSION"
    MEDIA_ASSET = "MEDIA_ASSET"


__all__ = ["AssetKind"]
