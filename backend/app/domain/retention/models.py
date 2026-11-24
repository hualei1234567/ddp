"""引用影响快照与保留裁决的数据形状（纯值对象）。

## 为什么需要这些对象

"这个 RawAsset 现在还能不能销毁"不是一个布尔事实，而是**带证据的裁决**：

- 谁在引用它（哪些数据集版本、第几版、当时是否已冻结）；
- 影响多少 Episode；
- 本次想做的动作会不会真的丢内容（归档不丢、墓碑丢）。

只返回 ``True/False`` 会让调用方无从解释拒绝原因，也无法在 UI 上给出"删除
会毁掉哪 3 个冻结版本"的预览。因此这里把证据建模成一等结构，API 直接序列化
它，用例层据它决定放行或抛错。

## 冻结判定为什么看 ``frozen_at`` 而不是 ``status``

``DatasetVersion`` 冻结后仍会迁移到 ``SUPERSEDED``（被新版本取代）或
``ARCHIVED``（归档）。若按 ``status == 'FROZEN'`` 判定，这两个状态下保护会
**静默消失**——而它们引用的原始内容同样是"已冻结版本的可复现证据"，必须继续
受保护。``frozen_at`` 是冻结那一刻写入且被数据库触发器列为不可变的列，是
"曾经冻结"的耐久标记，因此以它为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ReferenceTargetKind(StrEnum):
    """受保护对象的类型。

    只登记**真的会丢内容**的对象。``DatasetVersion`` 本身有独立的不可变与
    禁删触发器，不需要走本策略。
    """

    RAW_ASSET = "RAW_ASSET"
    PROCESSING_ARTIFACT = "PROCESSING_ARTIFACT"


@dataclass(frozen=True, slots=True)
class FrozenDatasetReference:
    """一个「引用了该对象且曾经冻结」的数据集版本。

    ``frozen`` 恒为 ``True``（构造来源已按 ``frozen_at IS NOT NULL`` 过滤），
    显式保留该字段是为了让 API 响应能自解释，而不是让客户端从
    ``status`` 反推"冻结过没有"。
    """

    dataset_version_id: UUID
    dataset_id: UUID
    dataset_version: int
    dataset_code: str | None
    status: str
    frozen: bool
    frozen_at: datetime
    #: 该版本中经由本对象引用到的 Episode 数量（去重）。
    episode_count: int


@dataclass(frozen=True, slots=True)
class ReferenceImpact:
    """一次"删除/销毁前"的引用影响快照。

    ``references`` 为空表示没有任何冻结版本引用它，此时销毁是安全的。
    """

    target_kind: ReferenceTargetKind
    target_id: UUID
    references: tuple[FrozenDatasetReference, ...]

    @property
    def dataset_version_count(self) -> int:
        return len(self.references)

    @property
    def episode_count(self) -> int:
        """受影响的 Episode 总数（跨版本去重由仓储按分别计数后相加）。

        注意：跨**数据集版本**去重没有意义——同一个 Episode 出现在两个冻结
        版本里就是影响两条可复现证据。因此这里取和而不是取最大。
        """
        return sum(item.episode_count for item in self.references)

    @property
    def protected(self) -> bool:
        return bool(self.references)


@dataclass(frozen=True, slots=True)
class RetentionVerdict:
    """纯函数裁决结果。

    刻意**不在这里抛异常**：领域策略返回结论，由用例层决定抛哪个域的异常
    （RawAsset 的写入口与 DatasetVersion 的冻结入口分属两个域，错误码不同）。
    这样策略本身可以被无依赖地穷举单测。
    """

    #: 是否允许执行本次动作。
    allowed: bool
    #: 稳定错误码；``allowed=True`` 时为 ``None``。
    code: str | None
    #: 面向人的拒绝原因（API 的 message 来源之一，不含内部标识）。
    reason: str | None
    impact: ReferenceImpact
