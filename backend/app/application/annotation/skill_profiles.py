"""标注员技能标签写入用例（V52-033，补齐 V52-009 只读口的写半边）。

V52-009 只交付了表（迁移 0080）与读取端口
:class:`~app.application.annotation.ports.SkillProfileDirectory`：分配用例能按
技能过滤候选，但**没有人能写标签**——技能路由在前端/运营侧等于空配置。本模块
补上写入用例：整体替换、单条追加、单条移除。

## 权限模型（本卡唯一权威实现）

- **项目 owner** 可改任何成员（含自己）；
- **本人** 可改自己的标签（``principal == annotator_id`` 且本人是项目成员）；
- 其余一律 403（``forbidden``）。平台管理员若不在该项目成员表内同样 403——
  与"owner 或本人"的显式规则一致，避免隐式放行。

写路由统一依赖 ``WriteScope``（editor 及以上）：技能标签是项目内写操作，viewer
不具备写权限，因此 viewer 自改也在依赖层被拒（文档与测试钉住该口径）。

## 校验

- 标签名规范化复用领域 :func:`~app.domain.annotation.skill_routing.normalize_tag_name`
  （去首尾空白 + 小写），与任务侧 ``required_skills`` 的匹配语义同源；
- ``weight ∈ (0, 1]`` 由 :class:`~app.domain.annotation.skill_routing.SkillTag`
  校验（与任务侧配置同一规则源）；**事实表没有 weight 列**（迁移 0080 只存
  tag_name / category），本用例不持久化 weight，也不在响应里伪造它；
- 同一请求内规范化后重复的标签显式 422，不静默去重（静默去重会掩盖调用方 bug）；
- 非项目成员不得持标签：应用层先判 422（更友好的错误），数据库复合外键
  ``(project_id, annotator_id) -> project_members`` 兜底。

## 幂等

整体替换按「集合差」落库：同一标签集重复提交不产生新行、不改 ``created_at``、
不重写 category 相同的行；追加已存在的标签不改行（category 变化时对齐）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from app.application.annotation.ports import (
    MemberDirectory,
    SkillProfileWriteRepository,
    UnitOfWork,
)
from app.domain.annotation.errors import DomainValidationError, ForbiddenOperationError
from app.domain.annotation.skill_routing import (
    SkillRoutingError,
    SkillTag,
    normalize_skill_tags,
    normalize_tag_name,
)
from app.domain.identity.models import ProjectRole, ProjectScope

#: 单个标注员标签数上限：技能标签是人可读的档案属性，不是标签云；
#: 上限防止误把任务标签整批灌进人员档案，也让响应体大小可预期。
MAX_SKILL_TAGS_PER_ANNOTATOR = 100


class SkillProfileUseCases:
    """技能标签写入用例（权限 + 规范化 + 幂等落库同一事务）。"""

    def __init__(
        self,
        profiles: SkillProfileWriteRepository,
        members: MemberDirectory,
        uow: UnitOfWork,
    ) -> None:
        self._profiles = profiles
        self._members = members
        self._uow = uow

    def replace_tags(
        self,
        scope: ProjectScope,
        annotator_id: UUID,
        tags: Sequence[object],
    ) -> list[SkillTag]:
        """整体替换某成员的标签集（``PUT``）；返回替换后的完整集合。"""
        self._authorize(scope, annotator_id)
        self._assert_target_member(scope, annotator_id)
        parsed = self._parse_tags(tags, replace=True)
        result = self._profiles.replace_tags(scope, annotator_id, parsed, now=datetime.now(UTC))
        self._uow.commit()
        return result

    def add_tag(
        self,
        scope: ProjectScope,
        annotator_id: UUID,
        tag: object,
    ) -> list[SkillTag]:
        """追加单条标签（``POST``）；已存在时幂等返回完整集合。"""
        self._authorize(scope, annotator_id)
        self._assert_target_member(scope, annotator_id)
        parsed = self._parse_tags([tag], replace=False)
        result = self._profiles.upsert_tag(scope, annotator_id, parsed[0], now=datetime.now(UTC))
        self._uow.commit()
        return result

    def remove_tag(
        self,
        scope: ProjectScope,
        annotator_id: UUID,
        tag_name: str,
    ) -> list[SkillTag]:
        """移除单条标签（``DELETE``）；标签不存在返回 404，不静默成功。"""
        self._authorize(scope, annotator_id)
        self._assert_target_member(scope, annotator_id)
        try:
            normalized = normalize_tag_name(tag_name)
        except SkillRoutingError as exc:
            raise DomainValidationError(f"技能标签名不合法：{exc}") from exc
        result = self._profiles.delete_tag(scope, annotator_id, normalized)
        self._uow.commit()
        return result

    def _authorize(self, scope: ProjectScope, annotator_id: UUID) -> None:
        """owner 可改任何人；本人可改自己；其余 403（默认拒绝）。"""
        role = self._members.user_role(scope, scope.actor_id)
        if role == ProjectRole.OWNER.value:
            return
        if role is not None and scope.actor_id == annotator_id:
            return
        raise ForbiddenOperationError("技能标签只允许项目负责人或本人修改")

    def _assert_target_member(self, scope: ProjectScope, annotator_id: UUID) -> None:
        """目标必须是项目成员：应用层先判 422，DB 复合外键兜底。"""
        if not self._members.user_is_member(scope, annotator_id):
            raise DomainValidationError("目标标注员不是项目成员，不能拥有技能标签")

    def _parse_tags(self, tags: Sequence[object], *, replace: bool) -> tuple[SkillTag, ...]:
        """把入参归一成 ``SkillTag`` 元组并做集合级校验（重复/上限）。"""
        try:
            parsed = normalize_skill_tags(list(tags))
        except SkillRoutingError as exc:
            raise DomainValidationError(f"技能标签不合法：{exc}") from exc
        if not replace:
            return parsed
        names = [tag.tag_name for tag in parsed]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise DomainValidationError(f"技能标签规范化后重复：{', '.join(duplicates)}")
        if len(parsed) > MAX_SKILL_TAGS_PER_ANNOTATOR:
            raise DomainValidationError(f"单个标注员最多 {MAX_SKILL_TAGS_PER_ANNOTATOR} 个技能标签")
        return parsed


__all__ = ["MAX_SKILL_TAGS_PER_ANNOTATOR", "SkillProfileUseCases"]
