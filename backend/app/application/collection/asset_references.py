"""任务对素材的引用校验（COL-002）。

为什么单独成文件而不是塞进 tasks.py 或 assets.py
------------------------------------------------
- 规则本身是"任务引用素材"这一条跨聚合前置条件。放在素材用例里会让素材
  用例反过来依赖任务的命令类型；放在任务用例里又要求它知道四张表的形状。
- 这是一个**可被两个调用点复用**的纯函数：任务创建（当前唯一调用点）与
  将来任务 PATCH 若要放开素材字段。抽出来才能保证只有一份实现。

为什么不只靠数据库外键
----------------------
外键 ``(project_id, xxx_id) → 素材表(project_id, id)`` 能拒绝跨项目与幽灵引用，
但有两点它做不到：

1. **不校验版本号**。``sop_id`` 指向 v1、``sop_version`` 写 ``v9`` 时外键照样通过，
   而任务会带着一个不存在的"版本快照"进入后续执行。
2. **错误不可读**。外键失败抛 ``IntegrityError``，消息是数据库方言原文，
   客户端拿到的是 500 或裸 SQL 文本，无法按 code 分支。

因此这里做"存在性 + 版本一致性"的显式校验，返回域错误（404 / 422），
数据库外键作为最后一道兜底，两者不重复也不冲突。
"""

from __future__ import annotations

from uuid import UUID

from app.application.collection.ports import CollectionAssetDirectory
from app.domain.collection.errors import DomainValidationError, ResourceNotFoundError
from app.domain.identity.models import ProjectScope

# 素材类型 -> 面向用户的资源名；错误消息里只出现资源名，不回显 ID。
_PLAN_LABEL = "采集方案"
_SOP_LABEL = "SOP"
_SCENARIO_LABEL = "采集场景"
_ACTION_SPEC_LABEL = "动作库"


def assert_task_asset_references(
    directory: CollectionAssetDirectory,
    scope: ProjectScope,
    *,
    plan_id: UUID | None,
    sop_id: UUID | None,
    sop_version: str | None,
    scenario_id: UUID | None,
    action_spec_id: UUID | None,
    action_spec_version: str | None,
) -> None:
    """校验任务上的四个素材引用必须**同项目存在**，且版本号与实际行一致。

    ``sop_version`` / ``action_spec_version`` 的语义是"任务冻结的当时版本"：
    - 给了 ID 就必须给版本（否则任务说不清按哪一版执行）；
    - 给版本不给 ID 也拒绝（无法定位是哪份素材的哪个版本）；
    - 版本必须与该素材的**实际行**一致——素材升版是新增行，
      旧版本号若已不存在于该项目，说明引用已失效，应当报错而不是静默接受。
    """
    if plan_id is not None and not directory.plan_exists(scope, plan_id):
        raise ResourceNotFoundError(_PLAN_LABEL, plan_id)
    if scenario_id is not None and not directory.scenario_exists(scope, scenario_id):
        raise ResourceNotFoundError(_SCENARIO_LABEL, scenario_id)

    _assert_versioned_reference(
        provided_id=sop_id,
        provided_version=sop_version,
        field="sop",
        label=_SOP_LABEL,
        exists=directory.sop_exists(scope, sop_id) if sop_id is not None else False,
        version_exists=(
            directory.sop_version_exists(scope, sop_id=sop_id, sop_version=sop_version)
            if sop_id is not None and sop_version is not None
            else False
        ),
    )
    _assert_versioned_reference(
        provided_id=action_spec_id,
        provided_version=action_spec_version,
        field="action_spec",
        label=_ACTION_SPEC_LABEL,
        exists=(
            directory.action_spec_exists(scope, action_spec_id)
            if action_spec_id is not None
            else False
        ),
        version_exists=(
            directory.action_spec_version_exists(
                scope,
                spec_id=action_spec_id,
                spec_version=action_spec_version,
            )
            if action_spec_id is not None and action_spec_version is not None
            else False
        ),
    )


def _assert_versioned_reference(
    *,
    provided_id: UUID | None,
    provided_version: str | None,
    field: str,
    label: str,
    exists: bool,
    version_exists: bool,
) -> None:
    if provided_id is None:
        if provided_version is not None:
            raise DomainValidationError(f"{field}_version 必须与 {field}_id 成对出现")
        return
    if not exists:
        raise ResourceNotFoundError(label, provided_id)
    if provided_version is None:
        raise DomainValidationError(f"引用 {field}_id 时必须同时提供 {field}_version")
    if not version_exists:
        raise DomainValidationError(f"{field}_version 与所引用的{label}不一致")


__all__: list[str] = ["assert_task_asset_references"]
