"""AI 建议与人工决定的采纳统计（N7 反馈闭环的输入）。

## 为什么单独建模

AI 建议与人工结果分表存储后（`annotation_ai_suggestions`），两者**同时存在**：
`suggested_value` 是模型输出，`human_value` 是人工最终值。有了这一对值，
才能回答一个此前完全无法回答的问题——**模型给的标签，人接受了多少、
改了多少、还有多少没看**。

这是 N7「模型反馈闭环」缺的那块输入：缺口分析知道"缺什么数据"，
但"模型在这个维度上靠不靠得住"必须先有采纳率。

## 三态，不是二态

每条建议的人工处置只有三种，且必须互斥：

- `ACCEPTED`：`human_value` 与 `suggested_value` 语义相等（见下方比较规则）
  ——人看过后原样采纳；
- `REVISED`：两者不等——人改了；
- `PENDING`：`human_confirmed=False`，人还没看。

**刻意不统计"拒绝"**：当前数据模型里没有"拒绝这条建议"的独立动作，
人工决定只有"确认/修订"一种语义。把"没确认"当"拒绝"会把待办算成否定，
与"没测到当 0"是同一类错误。

## 比较规则

`suggested_value` 与 `human_value` 都是任意 JSON（标量/数组/对象），
判等要同时处理两种"写法不同但语义相同"：

- **两边都是数值**：按数值比较。``1`` 与 ``1.0`` 在 JSON 里是同一个数，
  但规范化序列化分别是 ``"1"`` 与 ``"1.0"``——纯字符串比较会把"确认了同一个数"
  误判成"改过"；
- **其余情况**：按规范化 JSON 比较。对象键序不同必须判等
  （``{"a":1,"b":2}`` 与 ``{"b":2,"a":1}``），纯 ``==`` 对嵌套结构不稳。

`bool` 不算数值：JSON 里 `true` 与 `1` 是两个不同的值，
而 Python 的 `True == 1` 会掩盖这一点。实现见 :func:`_same_value`。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.annotation.validation import canonical_json


class SuggestionDisposition(StrEnum):
    """单条建议的人工处置。"""

    ACCEPTED = "ACCEPTED"
    REVISED = "REVISED"
    PENDING = "PENDING"


_NUMERIC = (int, float)


def _same_value(left: object, right: object) -> bool:
    """两个 JSON 值是否语义相同。

    分两种情况，因为 JSON 的数值与序列化形态不是一一对应：

    - **两边都是数值**（且非 bool）：按数值比较。``1`` 与 ``1.0`` 在 JSON 里
      是同一个数，但规范化序列化分别是 ``"1"`` 与 ``"1.0"``——直接比字符串
      会把"人确认了同一个数"误判成"人改过"。这是实测踩到的。
    - **其余情况**：按规范化 JSON 比较。对象键序不同（``{"a":1,"b":2}`` vs
      ``{"b":2,"a":1}``）必须判等，否则同一份标注换个键序就变成"修订"。

    ``bool`` 排除在数值之外：Python 里 ``True == 1``，但 JSON 里 ``true``
    与 ``1`` 是两个不同的值。
    """
    if (
        isinstance(left, _NUMERIC)
        and isinstance(right, _NUMERIC)
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
        return left == right
    return canonical_json(left) == canonical_json(right)


def classify(
    *,
    human_confirmed: bool,
    suggested_value: object,
    human_value: object,
) -> SuggestionDisposition:
    """判定单条建议的处置。未确认就是 PENDING，不看 ``human_value``。

    为什么未确认时不看值：未确认状态下 ``human_value`` 按领域不变式必须为空，
    若万一非空那是数据异常，把它算成 REVISED 会给出"人改过"的假结论。
    """
    if not human_confirmed:
        return SuggestionDisposition.PENDING
    if _same_value(suggested_value, human_value):
        return SuggestionDisposition.ACCEPTED
    return SuggestionDisposition.REVISED


@dataclass(frozen=True, slots=True)
class AgreementStats:
    """按模型版本聚合的采纳统计。

    ``acceptance_rate`` 的分母是**已决定**条数（ACCEPTED + REVISED），
    不是总条数。把 PENDING 算进分母会让"还没人看"拉低采纳率，
    读起来像"模型不行"，实际只是"还没轮到"。
    ``None`` 表示没有任何已决定条目——**不是 0**。
    """

    model_id: str
    model_version: str
    total: int
    accepted: int
    revised: int
    pending: int

    @property
    def decided(self) -> int:
        return self.accepted + self.revised

    @property
    def acceptance_rate(self) -> float | None:
        if self.decided == 0:
            return None
        return self.accepted / self.decided

    def to_payload(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "total": self.total,
            "accepted": self.accepted,
            "revised": self.revised,
            "pending": self.pending,
            "decided": self.decided,
            "acceptance_rate": self.acceptance_rate,
        }


@dataclass(frozen=True, slots=True)
class SuggestionLike:
    """统计只需要这几个字段，不绑定具体实体。"""

    model_id: str
    model_version: str
    human_confirmed: bool
    suggested_value: object
    human_value: object


def aggregate_agreement(
    suggestions: object,
) -> tuple[AgreementStats, ...]:
    """按 ``(model_id, model_version)`` 聚合，结果按该键排序（确定性）。

    空输入返回空元组——调用方据此区分"没有建议"与"有建议但都待处理"，
    不要把两者都渲染成 0%。
    """
    buckets: dict[tuple[str, str], list[SuggestionDisposition]] = {}
    for item in suggestions:  # type: ignore[attr-defined]
        key = (item.model_id, item.model_version)
        buckets.setdefault(key, []).append(
            classify(
                human_confirmed=item.human_confirmed,
                suggested_value=item.suggested_value,
                human_value=item.human_value,
            )
        )
    return tuple(
        AgreementStats(
            model_id=model_id,
            model_version=model_version,
            total=len(items),
            accepted=sum(1 for d in items if d is SuggestionDisposition.ACCEPTED),
            revised=sum(1 for d in items if d is SuggestionDisposition.REVISED),
            pending=sum(1 for d in items if d is SuggestionDisposition.PENDING),
        )
        for (model_id, model_version), items in sorted(buckets.items())
    )


__all__ = [
    "AgreementStats",
    "SuggestionDisposition",
    "SuggestionLike",
    "aggregate_agreement",
    "classify",
]
