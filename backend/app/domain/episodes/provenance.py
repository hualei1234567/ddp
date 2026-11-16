"""RawAsset 外部来源状态的判定规则。

为什么单独成文件：同一个判定要同时被应用层（血缘遍历）与 HTTP 出参使用，
放到任一侧都会让另一侧复制一份规则。这里只放**规则**，不放查询。

语义边界（务必区分开，三者含义不同）：

- ``RESOLVED``：``data_source_media_assets`` 里确实有指向该资产的行，
  数据源/导入任务可以从权威表读出来；
- ``NO_EXTERNAL_SOURCE``：资产本身没有任何外部数据源（手工上传 ``UPLOAD``、
  设备采集 ``DEVICE``），这是**正常状态**，不是缺失；
- ``SOURCE_LINK_MISSING``：资产声明 ``source_type=IMPORT``，但找不到对应的
  媒体资产行——这是数据不一致，必须显式暴露，不能让调用方误以为"没有来源"；
- ``UNKNOWN_ASSET``：血缘图里存在该节点，但在本项目 ``raw_assets`` 里查不到
  （例如跨项目残留或已被物理清理），此时**不能**猜成"无外部数据源"。
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.episodes.errors import DomainValidationError
from app.domain.ingestion.enums import RawAssetSourceType


class RawAssetSourceStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NO_EXTERNAL_SOURCE = "NO_EXTERNAL_SOURCE"
    SOURCE_LINK_MISSING = "SOURCE_LINK_MISSING"
    UNKNOWN_ASSET = "UNKNOWN_ASSET"


def classify_raw_asset_source(
    *,
    source_type: str | None,
    linked: bool,
) -> RawAssetSourceStatus:
    """按「是否已关联媒体资产行」与「资产声明的来源类型」判定来源状态。

    ``source_type`` 为 ``None`` 表示本项目查不到该资产；未知取值一律报错，
    不退回任何"看起来正常"的状态。
    """
    if linked:
        return RawAssetSourceStatus.RESOLVED
    if source_type is None:
        return RawAssetSourceStatus.UNKNOWN_ASSET
    try:
        normalized = RawAssetSourceType(source_type)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"source_type 取值非法：{source_type!r}") from exc
    if normalized is RawAssetSourceType.IMPORT:
        return RawAssetSourceStatus.SOURCE_LINK_MISSING
    return RawAssetSourceStatus.NO_EXTERNAL_SOURCE
