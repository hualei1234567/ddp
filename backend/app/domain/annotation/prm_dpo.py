"""Step-DPO 对构造（V52-020）：原轨迹 × 分叉轨迹 → canonical 偏好对。

从 :mod:`app.domain.annotation.prm_step` 拆出独立模块：标注域（标签/标注/
分叉）与偏好对构造是两个变更轴——前者随标注规则演进，后者随导出契约
（``PreferenceRecord``）演进；合在一起会把 ``prm_step.py`` 推过 500 行评审线。

## canonical 语义（与 ``llm_export.PreferenceRecord`` 对齐）

与呈现位置无关：``prompt`` 是共享前缀任务描述 + 前缀步骤；
``chosen`` = Forked 重写序列摘要、``rejected`` = Original 原序列摘要。
偏好对按 MEM-DEC-002 以 JSON 约定值回流，不新增标签类型。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.annotation.errors import DomainValidationError, ResourceNotFoundError
from app.domain.annotation.prm_step import ForkedTrajectory, PrmAnnotation
from app.domain.annotation.validation import (
    JSON_MAX_BYTES,
    canonical_json,
    optional_text,
)

#: 偏好对 JSON 约定值的字段键（MEM-DEC-002：值结构规范）。
_PREFERENCE_JSON_KEYS = frozenset(
    {"winner", "chosen", "rejected", "fork_point_index", "source_trajectory_id", "rationale"}
)


def _summary(actions: Sequence[str], start_index: int) -> str:
    """把步骤序列渲染成行式摘要：``[index] action`` 每步一行。"""
    lines = [f"[{start_index + i}] {action}" for i, action in enumerate(actions)]
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class StepDpoPair:
    """Step-DPO 对（canonical 语义）：chosen=Forked、rejected=Original。

    与呈现位置无关：``prompt`` 是共享前缀任务描述 + 前缀步骤；
    ``chosen`` / ``rejected`` 是分叉点之后的重写序列与原序列摘要。
    偏好对按 MEM-DEC-002 以 JSON 约定值回流，不新增标签类型。
    """

    source_trajectory_id: str
    fork_point_index: int
    prompt: str
    chosen: str
    rejected: str
    rationale: str | None = None
    criteria_scores: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompt.strip() or not self.chosen.strip() or not self.rejected.strip():
            raise DomainValidationError("prompt/chosen/rejected 不能为空")
        if self.chosen == self.rejected:
            raise DomainValidationError("chosen 与 rejected 不得相同：无差异不成偏好对")
        object.__setattr__(
            self,
            "rationale",
            optional_text(self.rationale, "rationale", max_length=2000),
        )
        cleaned: dict[str, float] = {}
        for key, value in self.criteria_scores.items():
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise DomainValidationError(f"criteria_scores[{key!r}] 必须是数值")
            cleaned[str(key)] = float(value)
        object.__setattr__(self, "criteria_scores", cleaned)

    def to_preference_record_kwargs(self) -> dict[str, Any]:
        """提供 ``PreferenceRecord.from_raw`` 所需的字段形态。

        ``response_a`` 固定为 Forked（chosen 语义）、``response_b`` 固定为
        Original（rejected 语义）、``winner="A"``、``presented_order=("A","B")``
        ——呈现槽与 canonical 槽一致，from_raw 归一化后 chosen=Forked。
        """
        kwargs: dict[str, Any] = {
            "prompt": self.prompt,
            "response_a": self.chosen,
            "response_b": self.rejected,
            "winner": "A",
            "presented_order": ("A", "B"),
        }
        if self.rationale is not None:
            kwargs["rationale"] = self.rationale
        if self.criteria_scores:
            kwargs["criteria_scores"] = dict(self.criteria_scores)
        return kwargs

    def to_payload(self) -> dict[str, object]:
        """MEM-DEC-002 的 JSON 约定值形态（作为 JSON 标签值落库）。"""
        payload: dict[str, object] = {
            "winner": "chosen",
            "chosen": self.chosen,
            "rejected": self.rejected,
            "fork_point_index": self.fork_point_index,
            "source_trajectory_id": self.source_trajectory_id,
        }
        if self.rationale is not None:
            payload["rationale"] = self.rationale
        return payload


def _prompt_summary(prm: PrmAnnotation, fork_point_index: int) -> str:
    task_goal = prm.task_goal or "（未提供任务描述）"
    prefix = _summary(prm.step_actions[:fork_point_index], 0) or "（前缀为空：整条重写）"
    return f"任务：{task_goal}\n已执行步骤：\n{prefix}"


def _pair_scores(prm: PrmAnnotation, forked: ForkedTrajectory) -> dict[str, float]:
    scores: dict[str, float] = {}
    original_reward = prm.total_reward()
    if original_reward is not None:
        scores["original_total_reward"] = original_reward
    prefix_reward = round(sum(mark.reward for mark in forked.marks), 6)
    scores["shared_prefix_reward"] = prefix_reward
    return scores


def _encoded_bytes(pair: StepDpoPair) -> int:
    return len(canonical_json(pair.to_payload()).encode("utf-8"))


def to_step_dpo_pair(prm: PrmAnnotation, forked: ForkedTrajectory) -> StepDpoPair:
    """把（原轨迹，分叉轨迹）构造成 Step-DPO 对。

    分叉点前两份轨迹共享前缀（prompt 只含前缀）；分叉点后
    chosen = Forked 重写序列、rejected = Original 原序列。JSON 约定值
    超过 ``JSON_MAX_BYTES`` 时显式失败（不静默截断）。
    """
    if forked.source_trajectory_id != prm.trajectory_id:
        raise DomainValidationError("分叉轨迹必须源自同一 trajectory_id")
    if forked.fork_point_index > prm.step_count:
        raise ResourceNotFoundError("fork_point_index", forked.fork_point_index)
    pair = StepDpoPair(
        source_trajectory_id=prm.trajectory_id,
        fork_point_index=forked.fork_point_index,
        prompt=_prompt_summary(prm, forked.fork_point_index),
        chosen=_summary(forked.suffix_actions(), forked.fork_point_index),
        rejected=_summary(prm.step_actions[forked.fork_point_index :], forked.fork_point_index),
        criteria_scores=_pair_scores(prm, forked),
    )
    if _encoded_bytes(pair) > JSON_MAX_BYTES:
        raise DomainValidationError(f"Step-DPO 对序列化后超过 {JSON_MAX_BYTES} 字节")
    return pair


def validate_preference_payload(value: Mapping[str, object]) -> None:
    """校验 JSON 约定值形态的偏好对（提交回流入口的兜底校验）。"""
    unknown = set(value) - _PREFERENCE_JSON_KEYS
    if unknown:
        raise DomainValidationError(f"偏好对载荷包含未知字段：{sorted(unknown)}")
    for key in ("chosen", "rejected"):
        text = value.get(key)
        if not isinstance(text, str) or not text.strip():
            raise DomainValidationError(f"偏好对 {key} 必须是非空字符串")
    if value.get("chosen") == value.get("rejected"):
        raise DomainValidationError("chosen 与 rejected 不得相同")


__all__ = [
    "StepDpoPair",
    "to_step_dpo_pair",
    "validate_preference_payload",
]
