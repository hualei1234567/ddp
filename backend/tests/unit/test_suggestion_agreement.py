"""AI 建议采纳统计的领域单测（纯函数，无 IO）。

三条被断言的语义，都是"少看一眼就会写成错的那种"：

1. 采纳率的分母是**已决定**条数，不是总数——把待处理算进分母会让
   "还没人看"读起来像"模型不行"；
2. 无已决定条目时是 ``None`` 而非 ``0``——0% 是"一条都没对上"，
   ``None`` 才是"还没人看"；
3. 未确认时不看 ``human_value``——未确认状态下它按不变式必须为空，
   万一非空算成 REVISED 会给出"人改过"的假结论。
"""

from __future__ import annotations

from app.domain.annotation.suggestion_agreement import (
    SuggestionDisposition,
    aggregate_agreement,
    classify,
)


class _Suggestion:
    """只带统计需要字段的替身。"""

    def __init__(
        self,
        *,
        model_id: str = "m",
        model_version: str = "1",
        human_confirmed: bool = False,
        suggested_value: object = "PLACE",
        human_value: object = None,
    ) -> None:
        self.model_id = model_id
        self.model_version = model_version
        self.human_confirmed = human_confirmed
        self.suggested_value = suggested_value
        self.human_value = human_value


def test_identical_value_is_accepted() -> None:
    assert (
        classify(human_confirmed=True, suggested_value="PLACE", human_value="PLACE")
        is SuggestionDisposition.ACCEPTED
    )


def test_different_value_is_revised() -> None:
    assert (
        classify(human_confirmed=True, suggested_value="PLACE", human_value="PICK")
        is SuggestionDisposition.REVISED
    )


def test_undecided_is_pending_regardless_of_value() -> None:
    """未确认就是 PENDING：即便 human_value 意外非空也不得算作 REVISED。

    领域不变式要求未确认时 human_value 为空；若数据异常导致非空，
    把它算成"人改过"会编造出并不存在的人工修订。
    """
    assert (
        classify(human_confirmed=False, suggested_value="PLACE", human_value="PICK")
        is SuggestionDisposition.PENDING
    )


def test_comparison_uses_canonical_form() -> None:
    """对象键序不同的同值视为采纳（规范化后逐字相等）。"""
    assert (
        classify(
            human_confirmed=True,
            suggested_value={"a": 1, "b": 2},
            human_value={"b": 2, "a": 1},
        )
        is SuggestionDisposition.ACCEPTED
    )


def test_int_and_float_with_same_value_are_accepted() -> None:
    """JSON 里 1 与 1.0 是同一个数：规范化后 ``1`` 与 ``1.0`` 序列化不同，
    但它们语义相同，必须判采纳而不是修订。"""
    assert (
        classify(human_confirmed=True, suggested_value=1.0, human_value=1)
        is SuggestionDisposition.ACCEPTED
    )


def test_aggregate_groups_and_sorts_by_model_version() -> None:
    stats = aggregate_agreement(
        [
            _Suggestion(model_id="b", model_version="1", human_confirmed=True),
            _Suggestion(model_id="a", model_version="2"),
            _Suggestion(model_id="a", model_version="1", human_confirmed=True),
        ]
    )
    assert [(s.model_id, s.model_version) for s in stats] == [("a", "1"), ("a", "2"), ("b", "1")]


def test_acceptance_rate_denominator_is_decided_not_total() -> None:
    """1 采纳 + 0 修订 + 3 待处理 → 采纳率 1.0，不是 0.25。"""
    stats = aggregate_agreement(
        [
            _Suggestion(human_confirmed=True, human_value="PLACE"),
            *[_Suggestion() for _ in range(3)],
        ]
    )[0]
    assert (stats.accepted, stats.revised, stats.pending) == (1, 0, 3)
    assert stats.decided == 1
    assert stats.acceptance_rate == 1.0


def test_acceptance_rate_is_none_when_nothing_decided() -> None:
    stats = aggregate_agreement([_Suggestion(), _Suggestion()])[0]
    assert stats.decided == 0
    assert stats.acceptance_rate is None


def test_empty_input_yields_empty_tuple() -> None:
    """没有建议 → 空结果，不是一行 0。调用方据此区分"没有"与"都待处理"。"""
    assert aggregate_agreement([]) == ()


def test_payload_is_json_safe() -> None:
    stats = aggregate_agreement([_Suggestion(human_confirmed=True, human_value="PLACE")])[0]
    payload = stats.to_payload()
    assert payload["acceptance_rate"] == 1.0
    assert payload["decided"] == 1
    assert set(payload) == {
        "model_id",
        "model_version",
        "total",
        "accepted",
        "revised",
        "pending",
        "decided",
        "acceptance_rate",
    }


def test_bool_is_not_treated_as_number() -> None:
    """JSON 里 `true` 与 `1` 是不同的值，不得因为 Python 的 True == 1 判等。

    Python 中 `True == 1` 为真，若数值比较分支不排除 bool，
    "人工把建议值 true 改成 1"会被误报成"采纳"。
    """
    assert (
        classify(human_confirmed=True, suggested_value=True, human_value=1)
        is SuggestionDisposition.REVISED
    )
    assert (
        classify(human_confirmed=True, suggested_value=True, human_value=True)
        is SuggestionDisposition.ACCEPTED
    )
