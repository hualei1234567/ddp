"""标注员技能标签路由（V52-009，蓝图 N2 Skill-based Routing）。

**蓝图要求**（N2「Skill-based Routing」）：按标注员技能标签把任务路由给
具备对应技能的人；评分结合历史质量与可选技能覆盖度。

**本模块是纯领域层**：不触 DB、不触 API、不建迁移——只提供确定性判定
函数与值对象，由上层（分配用例）在既有流程上**可选接入**。

**向后兼容不变量（验收硬要求）**：任务未配置必配技能（``required_skills``
为空）时，:func:`route_candidates` 是 no-op——返回全部候选、不排除、
不排序（保持调用方传入顺序）。未接入技能路由的项目行为 100% 不变。

评分公式（蓝图数值）：``score = quality_score * 0.7 + optional_hit_ratio * 0.3``。
质量分占 0.7、可选技能加权命中率占 0.3；``min_quality = 0``（默认）表示
不按质量过滤——只有技能门槛，新标注员（无历史质量数据）不会被质量项误伤。

**为什么 tags 用规范化字符串集合**：匹配语义 = 标签名相等（大小写不敏感、
去首尾空白）。技能档案是事实集合（人员会什么），任务侧才是配置（带
category / weight）；两侧通过同一规范化规则对齐，避免「SQLAlchemy 里存
大小写不同的同一个标签」这类匹配漏判。

设计对齐仓库既有约束：
- 阈值与权重全部显式常量，默认值来自蓝图，不硬编码散落在调用方；
- 失败必须显式（排除原因码），不得用静默跳过或空数组掩盖。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

#: 质量分在综合评分中的权重（蓝图 N2 数值）
QUALITY_WEIGHT = 0.7
#: 可选技能命中率在综合评分中的权重（蓝图 N2 数值）
SKILL_WEIGHT = 0.3

#: tag_name 最大长度（与迁移 0080 的 String(64) 对齐）
TAG_NAME_MAX_LENGTH = 64
#: category 最大长度（与迁移 0080 的 String(64) 对齐）
TAG_CATEGORY_MAX_LENGTH = 64


class SkillRoutingError(ValueError):
    """技能路由的领域错误（输入非法属于编程错误，不映射 HTTP 码）。"""


class SkillExclusionReason(StrEnum):
    """候选被排除的原因码（审计要求每条排除可解释）。"""

    #: 缺少任一必配技能
    MISSING_REQUIRED_SKILL = "MISSING_REQUIRED_SKILL"
    #: 历史质量分低于任务要求的 min_quality
    LOW_QUALITY = "LOW_QUALITY"


def normalize_tag_name(value: object) -> str:
    """标签名规范化：去首尾空白 + 小写。

    规范化是匹配语义的一部分：``"Kitchen"`` 与 ``"kitchen "`` 必须是同一个
    技能，否则档案与任务两侧的书写差异会造成静默漏配。
    """
    if not isinstance(value, str):
        raise SkillRoutingError("tag_name 必须是字符串")
    cleaned = value.strip().lower()
    if not cleaned:
        raise SkillRoutingError("tag_name 去空白后不能为空")
    if len(cleaned) > TAG_NAME_MAX_LENGTH:
        raise SkillRoutingError(f"tag_name 长度不得超过 {TAG_NAME_MAX_LENGTH}")
    return cleaned


@dataclass(frozen=True, slots=True)
class SkillTag:
    """任务侧技能标签配置。

    ``weight`` 只对**可选技能**生效（加权命中率）；必配技能要么全部命中、
    要么整体排除，权重无意义。范围 ``(0, 1]``——0 权重等于没配，禁止。
    """

    tag_name: str
    category: str | None = None
    weight: float = 1.0
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "tag_name", normalize_tag_name(self.tag_name))
        if self.category is not None:
            if not isinstance(self.category, str) or not self.category.strip():
                raise SkillRoutingError("category 为空时请传 None，不得传空白字符串")
            cleaned = self.category.strip()
            if len(cleaned) > TAG_CATEGORY_MAX_LENGTH:
                raise SkillRoutingError(f"category 长度不得超过 {TAG_CATEGORY_MAX_LENGTH}")
            object.__setattr__(self, "category", cleaned)
        if not isinstance(self.weight, int | float) or isinstance(self.weight, bool):
            raise SkillRoutingError("weight 必须是数值")
        if not 0.0 < float(self.weight) <= 1.0:
            raise SkillRoutingError("weight 必须在 (0, 1]")

    @property
    def is_required(self) -> bool:
        return self.required


@dataclass(frozen=True, slots=True)
class AnnotatorSkillProfile:
    """标注员技能档案（只读快照，由端口层从库中聚合）。

    ``quality_score``：历史质量分，∈ [0, 1]；无历史数据时端口层给 0.0
    （fail-closed：配置了 ``min_quality > 0`` 的项目不会把无证据者排进
    高质量任务——想放开就把 ``min_quality`` 配成 0）。
    ``completed_count``：历史完成分配数，≥ 0。
    """

    annotator_id: UUID
    tags: frozenset[str]
    quality_score: float
    completed_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.annotator_id, UUID):
            raise SkillRoutingError("annotator_id 必须是 UUID")
        tags: set[str] = set()
        for tag in self.tags:
            tags.add(normalize_tag_name(tag))
        object.__setattr__(self, "tags", frozenset(tags))
        if not isinstance(self.quality_score, int | float) or isinstance(self.quality_score, bool):
            raise SkillRoutingError("quality_score 必须是数值")
        if not 0.0 <= float(self.quality_score) <= 1.0:
            raise SkillRoutingError("quality_score 必须在 [0, 1]")
        object.__setattr__(self, "quality_score", float(self.quality_score))
        if not isinstance(self.completed_count, int) and not isinstance(self.completed_count, bool):
            raise SkillRoutingError("completed_count 必须是整数")
        if self.completed_count < 0:
            raise SkillRoutingError("completed_count 不能为负")

    def has_all(self, tag_names: Sequence[str]) -> bool:
        """是否持有全部给定技能（规范化后比对）。"""
        return all(normalize_tag_name(name) in self.tags for name in tag_names)


@dataclass(frozen=True, slots=True)
class SkillExclusion:
    """被排除的候选 + 原因码。"""

    annotator_id: UUID
    reason: SkillExclusionReason


@dataclass(frozen=True, slots=True)
class SkillRoutingOutcome:
    """路由结果：合格候选（已排序）+ 排除明细（带原因码）。

    ``excluded`` 只含被技能路由排除的人；**不路由（零配置）时为空元组且
    ``routed`` 为 False**——调用方据此判断"走了路由"还是"透传既有逻辑"。
    """

    eligible: tuple[AnnotatorSkillProfile, ...]
    excluded: tuple[SkillExclusion, ...]
    routed: bool

    def __post_init__(self) -> None:
        if self.routed and self.eligible and self.excluded:
            ids_eligible = {p.annotator_id for p in self.eligible}
            ids_excluded = {e.annotator_id for e in self.excluded}
            if ids_eligible & ids_excluded:
                raise SkillRoutingError("同一候选不得同时出现在 eligible 与 excluded")


def _required_and_optional(
    task_skills: Sequence[SkillTag],
) -> tuple[tuple[str, ...], tuple[SkillTag, ...]]:
    """拆分任务的必配技能名与可选技能配置。"""
    required: list[str] = []
    optional: list[SkillTag] = []
    for skill in task_skills:
        if not isinstance(skill, SkillTag):
            raise SkillRoutingError("task_skills 必须是 SkillTag 序列")
        if skill.required:
            required.append(skill.tag_name)
        else:
            optional.append(skill)
    return tuple(required), tuple(optional)


def _optional_hit_ratio(profile: AnnotatorSkillProfile, optional: tuple[SkillTag, ...]) -> float:
    """可选技能加权命中率：命中的权重和 / 总权重和。

    无可选技能时返回 0.0（评分退化为纯质量分，可选项不虚构命中）。
    """
    total = sum(skill.weight for skill in optional)
    if total <= 0.0:
        return 0.0
    matched = sum(skill.weight for skill in optional if skill.tag_name in profile.tags)
    return matched / total


def sort_candidates(
    candidates: Sequence[AnnotatorSkillProfile],
    task_skills: Sequence[SkillTag],
) -> tuple[AnnotatorSkillProfile, ...]:
    """按综合评分降序排序候选（独立导出，供只重排不过滤的调用方）。

    排序键 ``(score 降序, annotator_id 升序)``：同分按 id 决出稳定次序，
    保证同输入跨运行结果一致（验收要求：排序稳定性）。
    """
    _required, optional = _required_and_optional(task_skills)
    scored = [
        (
            -(
                profile.quality_score * QUALITY_WEIGHT
                + _optional_hit_ratio(profile, optional) * SKILL_WEIGHT
            ),
            profile.annotator_id,
            profile,
        )
        for profile in candidates
    ]
    scored.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in scored)


def route_candidates(
    task_required_skills: Sequence[SkillTag],
    min_quality: float,
    candidates: Sequence[AnnotatorSkillProfile],
) -> SkillRoutingOutcome:
    """技能路由纯函数（蓝图 N2）。

    规则：
    1. **零配置兼容**：任务没有任何必配技能 → 返回全部候选（原顺序），
       ``routed=False``——调用方继续走既有分配逻辑，行为不变；
    2. 缺任一必配技能 → 排除，原因 ``MISSING_REQUIRED_SKILL``；
    3. ``quality_score < min_quality`` → 排除，原因 ``LOW_QUALITY``
       （恰好等于阈值通过；``min_quality=0`` 表示不做质量过滤）；
    4. 其余按 :func:`sort_candidates` 排序（唯一评分权威实现）。

    一个候选同时缺技能且质量低时，主因记 ``MISSING_REQUIRED_SKILL``：
    结构性不匹配优先于指标不达标，排查时先看技能配置再看质量数据。
    """
    if not isinstance(min_quality, int | float) or isinstance(min_quality, bool):
        raise SkillRoutingError("min_quality 必须是数值")
    if not 0.0 <= float(min_quality) <= 1.0:
        raise SkillRoutingError("min_quality 必须在 [0, 1]")

    required, _optional = _required_and_optional(task_required_skills)
    if not required:
        # 零命中配置：不路由（向后兼容不变量，见模块 docstring）。
        return SkillRoutingOutcome(
            eligible=tuple(candidates),
            excluded=(),
            routed=False,
        )

    for profile in candidates:
        if not isinstance(profile, AnnotatorSkillProfile):
            raise SkillRoutingError("candidates 必须是 AnnotatorSkillProfile 序列")

    eligible: list[AnnotatorSkillProfile] = []
    excluded: list[SkillExclusion] = []
    for profile in candidates:
        if not profile.has_all(required):
            excluded.append(
                SkillExclusion(profile.annotator_id, SkillExclusionReason.MISSING_REQUIRED_SKILL)
            )
            continue
        if profile.quality_score < min_quality:
            excluded.append(SkillExclusion(profile.annotator_id, SkillExclusionReason.LOW_QUALITY))
            continue
        eligible.append(profile)

    return SkillRoutingOutcome(
        eligible=sort_candidates(eligible, task_required_skills),
        excluded=tuple(excluded),
        routed=True,
    )


def normalize_skill_tags(value: object) -> tuple[SkillTag, ...]:
    """把 JSON / Mapping 序列归一成 ``SkillTag`` 元组（任务聚合根用）。

    接受 ``None`` / 空序列（→ 空元组）、``SkillTag`` 序列或
    ``Mapping`` 序列（``tag_name`` 必填，``category`` / ``weight`` /
    ``required`` 可选）。非法元素立即报错，不静默丢弃。
    """
    if value is None:
        return ()
    if isinstance(value, SkillTag):
        return (value,)
    if isinstance(value, Mapping):
        items: Sequence[object] = (value,)
    elif isinstance(value, list | tuple):
        items = value
    else:
        raise SkillRoutingError("required_skills 必须是标签序列或 None")
    tags: list[SkillTag] = []
    for item in items:
        if isinstance(item, SkillTag):
            tags.append(item)
        elif isinstance(item, Mapping):
            if "tag_name" not in item:
                raise SkillRoutingError("required_skills 元素缺少 tag_name")
            weight = item.get("weight", 1.0)
            category = item.get("category")
            tags.append(
                SkillTag(
                    tag_name=str(item["tag_name"]),
                    category=None if category is None else str(category),
                    weight=float(weight),  # type: ignore[arg-type] - SkillTag 再校验
                    required=bool(item.get("required", True)),
                )
            )
        else:
            raise SkillRoutingError("required_skills 元素必须是 Mapping 或 SkillTag")
    return tuple(tags)


def skill_tags_to_json(tags: Sequence[SkillTag]) -> list[dict[str, object]]:
    """``SkillTag`` 元组 → JSON 可序列化列表（仓储写库用）。"""
    return [
        {
            "tag_name": tag.tag_name,
            "category": tag.category,
            "weight": tag.weight,
            "required": tag.required,
        }
        for tag in tags
    ]


def parse_min_quality(quality_policy: Mapping[str, object]) -> float:
    """从任务 ``quality_policy.skill_routing.min_quality`` 读取质量门槛。

    缺省 0.0（不按质量过滤）；配置非法值立即报错——配置错误不该被静默
    当成 0 放行。
    """
    routing = quality_policy.get("skill_routing")
    if routing is None:
        return 0.0
    if not isinstance(routing, Mapping):
        raise SkillRoutingError("quality_policy.skill_routing 必须是对象")
    raw = routing.get("min_quality", 0.0)
    if raw is None:
        return 0.0
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        raise SkillRoutingError("quality_policy.skill_routing.min_quality 必须是数值")
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise SkillRoutingError("quality_policy.skill_routing.min_quality 必须在 [0, 1]")
    return value


__all__ = [
    "QUALITY_WEIGHT",
    "SKILL_WEIGHT",
    "TAG_CATEGORY_MAX_LENGTH",
    "TAG_NAME_MAX_LENGTH",
    "AnnotatorSkillProfile",
    "SkillExclusion",
    "SkillExclusionReason",
    "SkillRoutingError",
    "SkillRoutingOutcome",
    "SkillTag",
    "normalize_skill_tags",
    "normalize_tag_name",
    "parse_min_quality",
    "route_candidates",
    "skill_tags_to_json",
    "sort_candidates",
]
