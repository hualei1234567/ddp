"""激活前的标定门禁：Profile 要求的标定类型必须有 ACTIVE 记录。

为什么单独成模块而不是在三处用例里各写一遍
------------------------------------------
契约《阶段 1 设备域契约冻结 v1》§8.1 规定：机器人/设备/传感器
``PROVISIONED → ACTIVE`` 的前置条件包含"采集就绪另需有效标定"，
错误码为 ``calibration_not_active``。这条规则同时属于三种实体，
复制三份必然漂移；规则只允许存在一处权威实现（CLAUDE.md §五）。

为什么必须真的产生 ``calibration_not_active``（G-20）
-----------------------------------------------------
该 code 此前只存在于映射表，没有任何产生点——客户端拿到的是泛化的
``validation_failed``，"没有有效标定"与"字段填错"无法区分。
门禁是本 code 唯一正确的触发语义：请求合法，缺的是**标定数据**。

边界
----
- 只在 ``activate`` 动作上执行；其它状态动作不要求标定。
- 实体没有绑定 Profile 时不检查——"没声明要求"不等于"要求已满足"，
  也不等于要凭空拒绝（绑定 Profile 是设备域的显式建模动作）。
- 绑定 Profile 但记录已不可见（跨项目/被删）按 ``calibration_profile_invalid``
  拒绝，不静默放行：拿不到要求清单时放过，等于跳过门禁。
"""

from __future__ import annotations

from uuid import UUID

from app.application.devices.ports import (
    CalibrationProfileRepository,
    CalibrationRecordRepository,
)
from app.domain.devices.enums import CalibrationStatus, CalibrationTargetType
from app.domain.devices.errors import (
    CalibrationNotActiveError,
    CalibrationProfileInvalidError,
)
from app.domain.identity.models import ProjectScope

# 单目标 ACTIVE 记录数受唯一索引约束（每个 calibration_type 至多一条），
# 这里的上限只为防御异常数据，不是分页语义。
_ACTIVE_QUERY_LIMIT = 100


def ensure_required_calibrations_active(
    *,
    calibrations: CalibrationRecordRepository,
    profiles: CalibrationProfileRepository,
    scope: ProjectScope,
    profile_id: UUID | None,
    target_type: CalibrationTargetType,
    target_id: UUID,
) -> None:
    """校验 Profile ``required_types`` 中的每一类标定都有 ACTIVE 记录。"""
    if profile_id is None:
        return
    profile = profiles.get(scope, profile_id)
    if profile is None:
        raise CalibrationProfileInvalidError("绑定的标定配置不存在或不属于当前项目")
    required = tuple(profile.required_types)
    if not required:
        return

    active = calibrations.list(
        scope,
        target_type=target_type.value,
        target_id=target_id,
        status=CalibrationStatus.ACTIVE,
        limit=_ACTIVE_QUERY_LIMIT,
    )
    available = {_enum_value(item.calibration_type) for item in active}
    missing = sorted(_enum_value(item) for item in required if _enum_value(item) not in available)
    if missing:
        raise CalibrationNotActiveError(
            "目标缺少有效标定，不能激活",
            details={
                "target_type": target_type.value,
                # 只回显标定类型名称：客户端需要知道"还差哪一类"，
                # 不需要也不应拿到其它记录 ID。
                "missing_calibration_types": missing,
            },
        )


def _enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
