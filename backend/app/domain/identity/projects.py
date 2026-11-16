"""项目与项目成员的领域规则（项目管理 / 人员与团队）。

## 为什么放在 ``identity`` 域

项目是平台隔离的基石：``project_id`` 与项目角色（``viewer`` / ``editor`` /
``owner``）已经由 ``app.domain.identity.models`` 定义，``ProjectAccessService``
按 ``project_members`` 解析权限。项目与成员管理只是这套既有模型的**写入面**
（此前后端只能靠 seed/脚本建项目），因此规则必须留在同一处，不能另起一套
"项目管理域"再定义一遍角色。

## 不做什么

- **不做硬删除**。``projects.id`` 被全部业务表以 ``ON DELETE CASCADE`` 引用，
  删除一个项目等于一次性销毁其全部采集、Episode、标注、数据集与审计记录，
  且不可恢复。本阶段只提供 ``ARCHIVED`` 状态位（见 ``ProjectStatus``），
  删除入口留空；需要清理数据时走单独的、逐条确认的运维流程。
- **不做租户/计费/账单**。平台角色只有 ``member`` / ``admin``，没有租户概念。
- **``ARCHIVED`` 不是权限开关**。它只用于"从默认列表里收起一个不再活跃的项目"，
  服务端授权仍只看项目角色（ADR-0004）。跨模块统一阻断归档项目的写入需要
  在 ``ProjectContext`` 上引入状态判定并回归全部端点，属于独立任务卡，
  不在本次范围内——把它做成"半个开关"比不做更危险。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.identity.models import ROLE_PERMISSIONS, ProjectAction, ProjectRole

PROJECT_CODE_MAX_LENGTH = 64
PROJECT_NAME_MAX_LENGTH = 200
PROJECT_DESCRIPTION_MAX_LENGTH = 1000

# 允许通过 PATCH 修改的字段。``code`` 刻意不可改：它是项目的业务键，
# 被外部脚本、备份与运维流程当作稳定标识；改名用 ``name``。
PATCHABLE_PROJECT_FIELDS = frozenset({"name", "description", "status"})

# 对外 ``allowed_actions`` 的顺序固定，避免前端渲染顺序随 dict 顺序漂移。
_ACTION_ORDER: tuple[ProjectAction, ...] = (
    ProjectAction.READ,
    ProjectAction.WRITE,
    ProjectAction.DELETE,
    ProjectAction.VALIDATE,
    ProjectAction.MANAGE_MEMBERS,
)


class ProjectStatus(StrEnum):
    """项目的生命周期状态。

    只有两个取值：新建即 ``ACTIVE``；``ARCHIVED`` 表示"不再活跃但仍需保留
    历史数据"。没有 ``DELETED``——删除不在本阶段的能力范围内。
    """

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


def normalize_project_code(value: object) -> str:
    """项目编码：去首尾空白后 1..64 字符，不允许纯空白。"""
    if not isinstance(value, str):
        raise ValueError("项目 code 必须是字符串")
    code = value.strip()
    if not code:
        raise ValueError("项目 code 不能为空")
    if len(code) > PROJECT_CODE_MAX_LENGTH:
        raise ValueError(f"项目 code 不能超过 {PROJECT_CODE_MAX_LENGTH} 个字符")
    return code


def normalize_project_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("项目名称必须是字符串")
    name = value.strip()
    if not name:
        raise ValueError("项目名称不能为空")
    if len(name) > PROJECT_NAME_MAX_LENGTH:
        raise ValueError(f"项目名称不能超过 {PROJECT_NAME_MAX_LENGTH} 个字符")
    return name


def normalize_project_description(value: object) -> str | None:
    """描述可空；空串与纯空白统一收敛为 ``None``，避免库里出现两种"空"。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("项目描述必须是字符串")
    description = value.strip()
    if not description:
        return None
    if len(description) > PROJECT_DESCRIPTION_MAX_LENGTH:
        raise ValueError(f"项目描述不能超过 {PROJECT_DESCRIPTION_MAX_LENGTH} 个字符")
    return description


def parse_project_status(value: object) -> ProjectStatus:
    if isinstance(value, ProjectStatus):
        return value
    if not isinstance(value, str):
        raise ValueError("项目状态必须是字符串")
    try:
        return ProjectStatus(value)
    except ValueError as exc:
        raise ValueError("项目状态取值非法") from exc


def parse_project_role(value: object) -> ProjectRole:
    if isinstance(value, ProjectRole):
        return value
    if not isinstance(value, str):
        raise ValueError("项目角色必须是字符串")
    try:
        return ProjectRole(value)
    except ValueError as exc:
        raise ValueError("项目角色取值非法") from exc


@dataclass(frozen=True, slots=True)
class Project:
    """一个项目。

    ``version`` 是乐观锁版本号：``PATCH`` 必须带 ``If-Match``，并发改名时
    后写者收到 409 而不是静默覆盖（与场地/设备/数据集的既有口径一致）。
    """

    project_id: UUID
    code: str
    name: str
    description: str | None
    status: ProjectStatus
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("项目 version 必须大于等于 1")
        if not isinstance(self.status, ProjectStatus):
            object.__setattr__(self, "status", parse_project_status(self.status))

    def apply_patch(self, changes: dict[str, object], *, now: datetime) -> Project:
        """返回应用补丁后的新实体；不修改自身（frozen）。"""
        if not changes:
            raise ValueError("PATCH 至少需要一个字段")
        unknown = set(changes) - PATCHABLE_PROJECT_FIELDS
        if unknown:
            raise ValueError("请求包含不允许修改的字段")

        name = self.name
        if "name" in changes:
            if changes["name"] is None:
                raise ValueError("项目名称不能置空")
            name = normalize_project_name(changes["name"])

        description = self.description
        if "description" in changes:
            description = normalize_project_description(changes["description"])

        status = self.status
        if "status" in changes:
            if changes["status"] is None:
                raise ValueError("项目状态不能置空")
            status = parse_project_status(changes["status"])

        return replace(
            self,
            name=name,
            description=description,
            status=status,
            version=self.version + 1,
            updated_at=now,
        )


def new_project(
    *,
    project_id: UUID,
    code: object,
    name: object,
    description: object,
    now: datetime,
) -> Project:
    """构造待落库的新项目；所有用户输入在此一次性规范化并校验。"""
    return Project(
        project_id=project_id,
        code=normalize_project_code(code),
        name=normalize_project_name(name),
        description=normalize_project_description(description),
        status=ProjectStatus.ACTIVE,
        version=1,
        created_at=now,
        updated_at=now,
    )


@dataclass(frozen=True, slots=True)
class ProjectMember:
    """项目成员视图：成员关系 + 用户展示名，供"人员与团队"页面直接渲染。"""

    project_id: UUID
    user_id: UUID
    display_name: str
    role: ProjectRole
    created_at: datetime | None = None
    updated_at: datetime | None = None


def leaves_project_without_owner(
    *,
    current_role: ProjectRole,
    new_role: ProjectRole | None,
    owner_count: int,
) -> bool:
    """判断一次成员变更是否会让项目失去最后一名 owner。

    ``new_role=None`` 表示移除成员。规则只有一条：**项目必须始终至少有一名
    owner**，否则成员管理权限（``manage_members``）将永久无人持有，项目变成
    只能由平台管理员接手的"孤儿项目"。

    调用方必须在锁住项目行之后取 ``owner_count`` 并调用本函数，否则两个并发
    请求各自降级不同的 owner 时会同时通过检查（见用例层的行锁说明）。
    """
    if owner_count < 1:
        raise ValueError("owner_count 至少为 1")
    was_owner = current_role is ProjectRole.OWNER
    becomes_owner = new_role is ProjectRole.OWNER
    return was_owner and not becomes_owner and owner_count <= 1


def allowed_actions(
    role: ProjectRole | None,
    *,
    is_platform_admin: bool,
) -> tuple[str, ...]:
    """对外返回的 ``allowed_actions``：仅用于前端按钮渲染的服务端提示。

    真正的授权判定仍在 ``ProjectContext.can()`` / 路由依赖里；本函数不参与
    服务端放行决策，避免出现"两个权威判定"。
    """
    if is_platform_admin:
        return tuple(action.value for action in _ACTION_ORDER)
    permissions = ROLE_PERMISSIONS.get(role) if role is not None else None
    if not permissions:
        return ()
    return tuple(action.value for action in _ACTION_ORDER if action in permissions)


__all__ = [
    "PATCHABLE_PROJECT_FIELDS",
    "PROJECT_CODE_MAX_LENGTH",
    "PROJECT_DESCRIPTION_MAX_LENGTH",
    "PROJECT_NAME_MAX_LENGTH",
    "Project",
    "ProjectMember",
    "ProjectStatus",
    "allowed_actions",
    "leaves_project_without_owner",
    "new_project",
    "normalize_project_code",
    "normalize_project_description",
    "normalize_project_name",
    "parse_project_role",
    "parse_project_status",
]
