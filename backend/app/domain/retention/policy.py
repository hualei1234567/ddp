"""保留策略：动作分类与「是否允许销毁内容」的纯函数裁决。

## 核心区分：归档 ≠ 物理删除 ≠ 丢内容

蓝图 §20.4 要求"默认不做不可逆物理删除"，并保留归档/软删除路径。落到本仓库
的现实是**三个层次**，必须分开说清楚：

1. **物理删除行**（``DELETE FROM raw_assets``）：已被迁移 0008 安装的
   ``trg_raw_assets_no_delete`` 触发器**无条件禁止**——不是"被引用时禁止"，
   而是任何情况下都不可能。本策略不重复这道闸，也不声称是自己做的。
2. **丢弃内容但保留行**（``tombstone``）：状态转 ``TOMBSTONED``，下载被拒、
   存储计量不再计入，行与血缘仍在。这才是**真正会毁掉可复现性**的动作。
3. **归档**（``archive``）：状态转 ``ARCHIVED``，内容仍可下载、仍计入存储。
   不丢任何东西，因此**永不被引用保护阻断**。

因此 ``CONTENT_DISCARDING_ACTIONS`` 是 ``tombstone``（以及把资产判死的
``reject-quarantined``），而 ``archive`` 属于 ``CONTENT_PRESERVING_ACTIONS``。
把归档也一并阻断会让唯一的合规退场路径失效，与蓝图冲突。

## 为什么 ``reject-quarantined`` 也算销毁

``REJECTED`` 与 ``TOMBSTONED`` 在存储计量里同属"内容已丢弃"
（``_STORAGE_OCCUPYING_STATUSES`` 排除两者），下载同样被拒
（``downloads.py`` 只允许 ``VALID`` / ``ARCHIVED``）。既然客观效果相同，
保留策略的判定口径必须相同，否则"先隔离再驳回"就成了绕过保护的后门。
"""

from __future__ import annotations

from app.domain.ingestion.enums import RawAssetAction
from app.domain.retention.models import (
    ReferenceImpact,
    ReferenceTargetKind,
    RetentionVerdict,
)

#: 会真正丢弃内容的公开动作：执行后资产不再是可复现证据。
CONTENT_DISCARDING_ACTIONS: frozenset[str] = frozenset(
    {
        RawAssetAction.TOMBSTONE.value,
        RawAssetAction.REJECT_QUARANTINED.value,
    }
)

#: 不丢弃内容的动作。``archive`` 是蓝图明确要求的合规退场路径，
#: 隔离类动作只是标记得更严重、内容仍在，同样不属于销毁。
CONTENT_PRESERVING_ACTIONS: frozenset[str] = frozenset(
    {
        RawAssetAction.ARCHIVE.value,
        RawAssetAction.POST_HOC_QUARANTINE.value,
        RawAssetAction.RELEASE_QUARANTINE.value,
    }
)

#: 稳定错误码：被冻结数据集版本引用，禁止销毁内容。
#:
#: 只用于 ``RAW_ASSET``：``ProcessingArtifact`` 当前**没有**任何销毁入口
#: （产物随运行级联删除，而运行本身没有删除用例），因此对它的引用查询是
#: 纯咨询性的预览，不产生阻断。等未来真的加了产物销毁动作，再为它分配
#: 独立错误码——现在预留一个永远打不出的码只会污染契约。
RAW_ASSET_REFERENCED_BY_FROZEN_DATASET = "raw_asset_referenced_by_frozen_dataset"
_REFERENCE_TARGET_KIND_LABEL = {
    ReferenceTargetKind.RAW_ASSET: "原始资产",
    ReferenceTargetKind.PROCESSING_ARTIFACT: "处理产物",
}


def is_content_discarding(action: str) -> bool:
    """该动作是否会真正丢弃内容。

    未知动作返回 ``False``：动作白名单由各领域状态机先行校验，走到这里
    说明动作已合法。对未知值"当作不销毁"是安全的——真正的拦截点仍然在
    领域状态机，本函数不是唯一防线。
    """
    return action in CONTENT_DISCARDING_ACTIONS


def retention_verdict(
    impact: ReferenceImpact,
    *,
    action: str | None = None,
) -> RetentionVerdict:
    """判定本次动作是否放行。

    ``action=None`` 表示"仅查询影响"（只读预览），此时不做任何阻断：
    预览的职责是如实报告，不是替用户决定。

    规则只有一条：**只要存在任何曾冻结的数据集版本引用，且动作会丢内容，
    就拒绝**。不做"影响小于 N 个版本就放行"之类的阈值——可复现性没有
    部分成立的说法。
    """
    label = _REFERENCE_TARGET_KIND_LABEL[impact.target_kind]
    if action is None or not is_content_discarding(action):
        return RetentionVerdict(
            allowed=True,
            code=None,
            reason=None,
            impact=impact,
        )
    if not impact.protected:
        return RetentionVerdict(
            allowed=True,
            code=None,
            reason=None,
            impact=impact,
        )
    count = impact.dataset_version_count
    episodes = impact.episode_count
    return RetentionVerdict(
        allowed=False,
        code=RAW_ASSET_REFERENCED_BY_FROZEN_DATASET,
        reason=(
            f"{label}被 {count} 个已冻结数据集版本引用（涉及 {episodes} 个 Episode），"
            "销毁内容会使这些版本不可复现；请改用归档（ARCHIVED）路径"
        ),
        impact=impact,
    )


__all__ = [
    "CONTENT_DISCARDING_ACTIONS",
    "CONTENT_PRESERVING_ACTIONS",
    "RAW_ASSET_REFERENCED_BY_FROZEN_DATASET",
    "is_content_discarding",
    "retention_verdict",
]
