"""PRM 步级标注域模型（V52-020，蓝图 N1 步骤级过程奖励 + 分叉重写）。

## 与 ``data-platform/ddp_data/llm_export/prm.py`` 的双源关系

四分类标签（``CORRECT`` / ``REDUNDANT`` / ``HALLUCINATED_TOOL`` / ``FATAL``）
与 reward 映射在本文件独立声明：``backend`` 运行时不依赖 ``data-platform``
包（后者不装进 backend venv），因此两份映射必须靠**单测钉死相等**
（``tests/unit/test_prm_step_domain.py`` 断言两 ``REWARD_BY_LABEL`` 的键与值
完全一致），任一侧改动而另一侧没跟，CI 直接红——这是"防双源漂移"的机制，
不是可以省略的仪式。

## 为什么步级偏好对走 JSON 约定值而非新增标签类型

MEM-DEC-002：偏好对比标注（chosen/rejected）用现有 ``JSON`` 标签类型的
约定值规范表达，不新增 ``PREFERENCE_PAIR`` 标签类型。本模块只产出
canonical 语义对的数据结构（:class:`StepDpoPair`）与
``PreferenceRecord.from_raw`` 兼容的字段形态，回流既有提交链路时按
JSON 值落库，不触碰 ``AnnotationLabelType`` 枚举。

## 分叉重写（fork）的语义

从指定步 ``fork_point_index`` 分叉：0..at_index-1 步（含标注）原样保留，
at_index 起替换为重写序列。分叉点前两份轨迹共享前缀；分叉点后
chosen = Forked（重写）、rejected = Original（原轨迹），构成 Step-DPO 对。
分叉后新轨迹的步骤 index 沿用原轴连续编号（0..n-1，n = at_index + len(重写)），
与 ``llm_export`` 的"index 连续 0..n-1"红线一致。


Step-DPO 对构造（``StepDpoPair`` / ``to_step_dpo_pair``）在
:mod:`app.domain.annotation.prm_dpo`（本文件超 500 行评审线时拆出的
独立变更轴，此处不再重复定义）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.domain.annotation.errors import (
    DomainValidationError,
)
from app.domain.annotation.validation import (
    frozen_mapping,
    normalized_text,
    optional_aware_datetime,
    optional_text,
)

#: 与 ``data-platform/ddp_data/llm_export/prm.py`` 的 ``REWARD_BY_LABEL``
#: 数值必须完全一致（单测钉住）；此处改动必须同步那边，反之亦然。
REWARD_BY_LABEL: dict[str, float] = {
    "CORRECT": 1.0,
    "REDUNDANT": -0.5,
    "HALLUCINATED_TOOL": -1.0,
    "FATAL": -2.0,
}

#: 轨迹步骤数上限——与 ``llm_export.prm._MAX_STEPS`` 对齐（512）。
MAX_STEP_COUNT = 512

#: 单条步骤 action / 重写文本的最大长度（与提交备注同级，防超长注入）。
ACTION_MAX_LENGTH = 2000

_ANNOTATOR_MAX_LENGTH = 128
_COMMENT_MAX_LENGTH = 2000


class StepLabelKind(StrEnum):
    """蓝图 N1 四分类步级标签（与 ``llm_export.prm.StepLabel`` 同值集）。"""

    CORRECT = "CORRECT"
    REDUNDANT = "REDUNDANT"
    HALLUCINATED_TOOL = "HALLUCINATED_TOOL"
    FATAL = "FATAL"

    @property
    def reward(self) -> float:
        """该标签对应的 step reward（数值与 ``llm_export`` 完全一致）。"""
        return REWARD_BY_LABEL[self.value]


def _plain(value: object) -> object:
    """把不可变容器还原成普通 Python 值，便于落库与序列化。"""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _now_iso(value: datetime) -> str:
    return value.isoformat()


def _coerce_aware_datetime(value: object, field_name: str) -> datetime | None:
    """读库回读容忍 ISO 字符串形态；写路径要求 aware datetime。"""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 不是合法的 ISO 时间") from exc
    return optional_aware_datetime(value, field_name)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class StepMark:
    """一步的标注结果（可部分标注：未标 ≠ 正确）。"""

    step_index: int
    label: StepLabelKind | str
    comment: str | None = None
    annotator: str | None = None
    marked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.step_index, int) or isinstance(self.step_index, bool):
            raise DomainValidationError("step_index 必须是整数")
        if self.step_index < 0:
            raise DomainValidationError(f"step_index 不能为负：{self.step_index}")
        try:
            label = StepLabelKind(self.label)
        except ValueError as exc:
            raise DomainValidationError(f"非法步级标签：{self.label!r}") from exc
        object.__setattr__(self, "label", label)
        object.__setattr__(
            self,
            "comment",
            optional_text(self.comment, "step_mark.comment", max_length=_COMMENT_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "annotator",
            optional_text(self.annotator, "step_mark.annotator", max_length=_ANNOTATOR_MAX_LENGTH),
        )
        object.__setattr__(
            self, "marked_at", _coerce_aware_datetime(self.marked_at, "step_mark.marked_at")
        )

    @property
    def reward(self) -> float:
        return REWARD_BY_LABEL[self.label.value]

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "step_index": self.step_index,
            "label": self.label.value,
        }
        if self.comment is not None:
            payload["comment"] = self.comment
        if self.annotator is not None:
            payload["annotator"] = self.annotator
        if self.marked_at is not None:
            payload["marked_at"] = _now_iso(self.marked_at)
        return payload


def _clean_action(text: object, field_name: str) -> str:
    if not isinstance(text, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    try:
        return normalized_text(text, field_name, max_length=ACTION_MAX_LENGTH)
    except DomainValidationError as exc:
        raise DomainValidationError(
            f"{field_name} 不能为空且不得超过 {ACTION_MAX_LENGTH} 字符"
        ) from exc


@dataclass(frozen=True, slots=True)
class PrmAnnotation:
    """一条 Agent 轨迹的步级标注集合。

    ``step_actions`` 是轨迹的原始步骤序列（与 AGENT_TRACE 载体一致，
    index 连续 0..n-1）；``marks`` 是已标注步骤的集合——**允许部分标注**，
    未出现在 ``marks`` 里的步骤不携带任何结论（红线：未标注 ≠ 正确）。

    分叉轨迹（``fork_point_index`` 非 ``None``）通过 ``fork_lineage`` 记录
    来源：分叉点前与原轨迹共享前缀，构成 Step-DPO 对的 chosen 侧。
    """

    trajectory_id: str
    step_actions: tuple[str, ...]
    marks: tuple[StepMark, ...] = ()
    task_goal: str | None = None
    fork_lineage: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trajectory_id",
            normalized_text(self.trajectory_id, "trajectory_id", max_length=200),
        )
        if not self.step_actions:
            raise DomainValidationError("轨迹至少需要 1 个步骤")
        if len(self.step_actions) > MAX_STEP_COUNT:
            raise DomainValidationError(f"步骤数超上限 {MAX_STEP_COUNT}")
        object.__setattr__(
            self,
            "step_actions",
            tuple(
                _clean_action(action, f"step_actions[{i}]")
                for i, action in enumerate(self.step_actions)
            ),
        )
        object.__setattr__(
            self, "task_goal", optional_text(self.task_goal, "task_goal", max_length=2000)
        )
        if self.fork_lineage is not None:
            object.__setattr__(
                self, "fork_lineage", frozen_mapping(self.fork_lineage, "fork_lineage")
            )
        marks: list[StepMark] = []
        seen: set[int] = set()
        for item in self.marks:
            mark = item if isinstance(item, StepMark) else StepMark(**item)  # type: ignore[arg-type]
            if mark.step_index in seen:
                raise DomainValidationError(f"步骤 {mark.step_index} 重复标注")
            if mark.step_index >= len(self.step_actions):
                raise DomainValidationError(
                    f"步骤 {mark.step_index} 越界（轨迹共 {len(self.step_actions)} 步）"
                )
            seen.add(mark.step_index)
            marks.append(mark)
        object.__setattr__(self, "marks", tuple(sorted(marks, key=lambda m: m.step_index)))

    @property
    def step_count(self) -> int:
        return len(self.step_actions)

    @property
    def labeled_step_count(self) -> int:
        return len(self.marks)

    def mark_at(self, step_index: int) -> StepMark | None:
        """取某一步的标注；未标注返回 ``None``（≠ 正确）。"""
        for mark in self.marks:
            if mark.step_index == step_index:
                return mark
        return None

    def with_mark(self, mark: StepMark) -> PrmAnnotation:
        """追加或覆盖一步标注；覆盖允许（复核修正），index 唯一性不变。"""
        if mark.step_index >= self.step_count:
            raise DomainValidationError(
                f"步骤 {mark.step_index} 越界（轨迹共 {self.step_count} 步）"
            )
        kept = tuple(m for m in self.marks if m.step_index != mark.step_index)
        return replace(self, marks=(*kept, mark))

    def label_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for mark in self.marks:
            key = mark.label.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def total_reward(self) -> float | None:
        if not self.marks:
            return None
        return round(sum(mark.reward for mark in self.marks), 6)

    def to_payload(self) -> dict[str, object]:
        """落库 JSON 载荷（走既有 JSONB/JSON 列，不新增标签类型）。"""
        payload: dict[str, object] = {
            "trajectory_id": self.trajectory_id,
            "step_actions": list(self.step_actions),
            "marks": [mark.to_payload() for mark in self.marks],
        }
        if self.task_goal is not None:
            payload["task_goal"] = self.task_goal
        if self.fork_lineage is not None:
            payload["fork_lineage"] = _plain(self.fork_lineage)
        return payload

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> PrmAnnotation:
        """从 JSON 载荷重建（读库路径；未知字段拒绝，防拼写错误静默丢语义）。"""
        unknown = set(value) - {
            "trajectory_id",
            "step_actions",
            "marks",
            "task_goal",
            "fork_lineage",
        }
        if unknown:
            raise DomainValidationError(f"PRM 标注载荷包含未知字段：{sorted(unknown)}")
        try:
            raw_actions = value["step_actions"]
        except KeyError as exc:
            raise DomainValidationError("PRM 标注载荷缺少 step_actions") from exc
        if not isinstance(raw_actions, list | tuple):
            raise DomainValidationError("step_actions 必须是字符串列表")
        raw_marks = value.get("marks") or ()
        if not isinstance(raw_marks, list | tuple):
            raise DomainValidationError("marks 必须是列表")
        raw_lineage = value.get("fork_lineage")
        return cls(
            trajectory_id=str(value["trajectory_id"]),
            step_actions=tuple(raw_actions),
            marks=tuple(dict(mark) for mark in raw_marks),
            task_goal=str(value["task_goal"]) if value.get("task_goal") else None,
            fork_lineage=dict(raw_lineage) if raw_lineage is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ForkedTrajectory:
    """分叉重写后的新轨迹：分叉点前共享前缀，分叉点后为重写序列。"""

    source_trajectory_id: str
    fork_point_index: int
    step_actions: tuple[str, ...]
    marks: tuple[StepMark, ...] = ()
    task_goal: str | None = None

    @property
    def step_count(self) -> int:
        return len(self.step_actions)

    @property
    def prefix_step_count(self) -> int:
        return self.fork_point_index

    def mark_at(self, step_index: int) -> StepMark | None:
        """取某一步的标注；未标注返回 ``None``（≠ 正确）。"""
        for mark in self.marks:
            if mark.step_index == step_index:
                return mark
        return None

    def suffix_actions(self) -> tuple[str, ...]:
        """分叉点之后的步骤文本（chosen 摘要的原料）。"""
        return self.step_actions[self.fork_point_index :]

    def to_prm_annotation(self, trajectory_id: str) -> PrmAnnotation:
        """物化成独立 :class:`PrmAnnotation`（持久化分叉轨迹时使用）。

        ``trajectory_id`` 由调用方分配（如 ``<原 id>#fork-<n>``）；分叉点前的
        共享前缀标注随构造保留，重写步骤保持未标注。分叉血缘（来源轨迹、
        分叉点、共享前缀长度）写入 ``fork_lineage``。
        """
        return PrmAnnotation(
            trajectory_id=trajectory_id,
            step_actions=self.step_actions,
            marks=self.marks,
            task_goal=self.task_goal,
            fork_lineage={
                "source_trajectory_id": self.source_trajectory_id,
                "fork_point_index": self.fork_point_index,
                "shared_prefix_steps": self.fork_point_index,
            },
        )


def _validated_fork_point(prm: PrmAnnotation, at_index: int) -> None:
    if not isinstance(at_index, int) or isinstance(at_index, bool):
        raise DomainValidationError("at_index 必须是整数")
    if at_index < 0 or at_index >= prm.step_count:
        raise DomainValidationError(
            f"fork_point_index {at_index} 越界：允许 0..{prm.step_count - 1}"
        )


def _validate_rewrite_actions(new_actions: Sequence[object]) -> tuple[str, ...]:
    if not new_actions:
        raise DomainValidationError("重写序列不能为空：分叉必须至少引入 1 个新步骤")
    if len(new_actions) > MAX_STEP_COUNT:
        raise DomainValidationError(f"重写序列步骤数超上限 {MAX_STEP_COUNT}")
    return tuple(_clean_action(action, f"new_actions[{i}]") for i, action in enumerate(new_actions))


def fork_from_step(
    prm: PrmAnnotation,
    at_index: int,
    *new_actions: object,
) -> ForkedTrajectory:
    """从 ``at_index`` 步分叉重写。

    保留 0..at_index-1 步（含已标注），``at_index`` 起替换为 ``new_actions``
    重写序列；重写步骤全部未标注（新内容不携带任何先验结论）。at_index=0
    表示整条重写；at_index=末步表示只换最后一步；越界拒绝。分叉血缘
    （来源轨迹、分叉点、共享前缀长度）记入 ``fork_lineage``，供落库与
    Step-DPO 对构造追溯。
    """
    _validated_fork_point(prm, at_index)
    rewritten = _validate_rewrite_actions(new_actions)
    if at_index + len(rewritten) > MAX_STEP_COUNT:
        raise DomainValidationError(f"分叉后步骤数超上限 {MAX_STEP_COUNT}")
    merged = (*prm.step_actions[:at_index], *rewritten)
    prefix_marks = tuple(mark for mark in prm.marks if mark.step_index < at_index)
    return ForkedTrajectory(
        source_trajectory_id=prm.trajectory_id,
        fork_point_index=at_index,
        step_actions=merged,
        marks=prefix_marks,
        task_goal=prm.task_goal,
    )


__all__ = [
    "ACTION_MAX_LENGTH",
    "MAX_STEP_COUNT",
    "REWARD_BY_LABEL",
    "ForkedTrajectory",
    "PrmAnnotation",
    "StepLabelKind",
    "StepMark",
    "fork_from_step",
]
