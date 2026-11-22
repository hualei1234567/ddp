"""从处理输入引用解析权威 ID（V31-018）。

处理运行的输入是对象 key；多个阶段都要回答"这个 key 指向哪个 Episode / 哪个
RawAsset"。解析规则只有一份：key 结构由领域正则
（``domain.ingestion.validation.validate_raw_object_key``）与 Episode manifest
命名共同约束，本模块把它们翻成 ID，不做任何猜测。

项目隔离在这里也生效：只接受第二段等于本项目 ``project_id`` 的 key，其余返回
``None``——调用方据此把"别人的对象"当成"不是我的输入"，而不是去查它是否存在。
"""

from __future__ import annotations

from uuid import UUID

from app.domain.episodes.validation import MANIFEST_KEY_PATTERN
from app.domain.ingestion.validation import validate_raw_object_key


def manifest_episode_id(ref: str, project_id: str) -> UUID | None:
    """从本项目 Canonical manifest Key 解析 Episode ID；非该形态返回 ``None``。"""
    if MANIFEST_KEY_PATTERN.fullmatch(ref) is None:
        return None
    parts = ref.split("/")
    if parts[1] != project_id:
        return None
    return UUID(parts[2])


def raw_asset_id_from_ref(ref: str, project_id: str) -> UUID | None:
    """从本项目 RawAsset Key 解析资产 ID；非该形态返回 ``None``。

    Key 结构由 ``domain.ingestion.validation.validate_raw_object_key`` 的权威
    正则约束：``raw/<project>/YYYY/MM/DD/<asset_id>/content.<ext>``。
    """
    if not validate_raw_object_key(ref):
        return None
    parts = ref.split("/")
    if parts[1] != project_id:
        return None
    return UUID(parts[5])


__all__ = ["manifest_episode_id", "raw_asset_id_from_ref"]
