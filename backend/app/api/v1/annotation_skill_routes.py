"""标注员技能标签写入 HTTP 路由（V52-033，补齐 V52-009 遗留）。

V52-009 交付了 ``annotator_skill_tags`` 表（迁移 0080）与派单读取端口，但
**没有写入端点**——技能标签只能靠直接改库，技能路由在运营侧不可配置。本文件
补三条写路由：

- ``PUT   .../annotation-annotators/{annotator_id}/skill-tags``：整体替换标签集；
- ``POST  .../annotation-annotators/{annotator_id}/skill-tags``：追加单条标签；
- ``DELETE .../annotation-annotators/{annotator_id}/skill-tags/{tag_name}``：移除单条。

**为什么是独立文件**：技能标签是人员档案治理（谁能派到哪类任务），与标注任务
/提交/复核的业务状态机无关；混进 ``annotation_work.py`` 会让"谁维护技能档案"
变模糊（先例：``annotation_honeypot_routes.py`` 的熔断治理独立成文件）。

权限、成员资格、规范化与幂等全部在应用用例
:class:`~app.application.annotation.skill_profiles.SkillProfileUseCases` 中实现，
本层只做 HTTP 形状适配：请求体校验、把领域 ``SkillTag`` 映射成响应、错误码交由
全局异常处理器映射（403 ``forbidden`` / 422 ``validation_failed`` / 404
``resource_not_found``）。

**weight 说明**：请求体接受 ``weight`` 并复用领域 ``SkillTag`` 的 ``(0, 1]``
校验（与任务侧 ``required_skills`` 同源规则）；但事实表（迁移 0080）没有
weight 列，本端点**不持久化** weight，也不在响应里伪造——人员档案是"会什么"
的事实集合，权重只属于任务侧配置。

契约：本轮只改 fragments 并追加 ``$ref``，**不 bump openapi.yaml version**
（按 MEM-INC-015 的版本延迟策略）。路径参数按 MEM-INC-017 三步执行：路径级
``parameters`` 声明 → 参数组件入 ``components.yaml`` 的 ``parameters:`` 段 →
fragment 内相对 ``$ref``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.annotation_common import WriteScope
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.domain.annotation.skill_routing import (
    TAG_CATEGORY_MAX_LENGTH,
    TAG_NAME_MAX_LENGTH,
    SkillTag,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-skill-tags"])

#: 标签名在 URL 路径里的长度上限（与领域 / 迁移 0080 的 ``String(64)`` 对齐）。
TagNamePath = Annotated[str, Path(min_length=1, max_length=TAG_NAME_MAX_LENGTH)]


class SkillTagWriteIn(BaseModel):
    """单条技能标签写请求。

    ``weight`` 与任务侧 ``SkillTag`` 同规则校验（``(0, 1]``），但仅用于输入
    合法性；事实表无 weight 列，不落库（见模块 docstring）。
    """

    model_config = ConfigDict(extra="forbid")

    tag_name: str = Field(
        min_length=1,
        max_length=TAG_NAME_MAX_LENGTH,
        description="标签名；服务端规范化为小写并去首尾空白后落库",
    )
    category: str | None = Field(
        default=None,
        max_length=TAG_CATEGORY_MAX_LENGTH,
        description="可选分类（如 vision / language / tool-use）",
    )
    weight: float = Field(
        default=1.0,
        gt=0,
        le=1,
        description="与任务侧技能配置同规则校验（0 < weight <= 1）；人员档案不持久化该值",
    )


class SkillTagReplaceIn(BaseModel):
    """整体替换请求：``{"tags": [...]}``，空数组表示清空该成员标签集。"""

    model_config = ConfigDict(extra="forbid")

    tags: list[SkillTagWriteIn] = Field(
        default_factory=list,
        description="完整目标标签集；重复（规范化后）返回 422，不静默去重",
    )


class SkillTagOut(BaseModel):
    """标签对外形态（不含未持久化的 weight）。"""

    tag_name: str
    category: str | None = None


class AnnotatorSkillTagsOut(BaseModel):
    """某标注员的完整技能标签集（三条写路由统一返回，便于客户端一次对齐）。"""

    annotator_id: UUID
    tags: list[SkillTagOut]

    @classmethod
    def from_domain(cls, annotator_id: UUID, tags: list[SkillTag]) -> AnnotatorSkillTagsOut:
        return cls(
            annotator_id=annotator_id,
            tags=[SkillTagOut(tag_name=tag.tag_name, category=tag.category) for tag in tags],
        )


def _payload(tag: SkillTagWriteIn) -> dict[str, object]:
    """请求模型 → 领域 ``normalize_skill_tags`` 接受的 Mapping 形态。"""
    return tag.model_dump()


@router.put(
    "/annotation-annotators/{annotator_id}/skill-tags",
    response_model=AnnotatorSkillTagsOut,
    summary="整体替换标注员的技能标签集（幂等）",
)
def replace_skill_tags(
    annotator_id: UUID,
    payload: SkillTagReplaceIn,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotatorSkillTagsOut:
    """整体替换标签集：重复提交同一集合结果一致（按集合差落库）。

    权限：项目 owner 可改任何人；本人可改自己；其余 403。目标必须是项目成员，
    否则 422（数据库复合外键兜底，应用层先给更友好的错误）。
    """
    tags = use_cases.skill_profiles.replace_tags(
        scope, annotator_id, [_payload(item) for item in payload.tags]
    )
    return AnnotatorSkillTagsOut.from_domain(annotator_id, tags)


@router.post(
    "/annotation-annotators/{annotator_id}/skill-tags",
    response_model=AnnotatorSkillTagsOut,
    summary="追加单条技能标签（幂等）",
)
def add_skill_tag(
    annotator_id: UUID,
    payload: SkillTagWriteIn,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotatorSkillTagsOut:
    """追加单条标签：已存在时不重复插入（category 变化时对齐），返回完整集合。"""
    tags = use_cases.skill_profiles.add_tag(scope, annotator_id, _payload(payload))
    return AnnotatorSkillTagsOut.from_domain(annotator_id, tags)


@router.delete(
    "/annotation-annotators/{annotator_id}/skill-tags/{tag_name}",
    response_model=AnnotatorSkillTagsOut,
    summary="移除单条技能标签",
)
def remove_skill_tag(
    annotator_id: UUID,
    tag_name: TagNamePath,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotatorSkillTagsOut:
    """移除单条标签并返回剩余集合；标签不存在返回 404（不静默成功）。"""
    tags = use_cases.skill_profiles.remove_tag(scope, annotator_id, tag_name)
    return AnnotatorSkillTagsOut.from_domain(annotator_id, tags)


__all__ = ["router"]
