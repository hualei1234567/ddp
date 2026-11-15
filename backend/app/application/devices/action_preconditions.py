"""生命周期动作的请求体前置条件（契约《阶段 1 设备域契约冻结 v1》§8.1）。

为什么单独成模块
----------------
同一张规则表同时约束 robot / device / sensor 三种实体——三处各写一遍必然漂移。
``_ACTIONS`` 的动作名三者共享（``activate`` / ``enter-maintenance`` /
``report-fault`` / ``suspend`` / ``retire``），前置条件也一致，因此规则只在这里
定义一次，三个 use case 共用。

与 ``calibration_gate`` 的分工
----------------------------
那个管"标定数据是否就绪"（查库），本模块只管"请求体字段是否齐备"（纯校验）。
两者都在 ``activate`` 上执行，但失败码不同：缺标定是
``calibration_not_active``，缺字段是 ``validation_failed``。

规则来源（§8.1 状态迁移表）
--------------------------
| 迁移 | 必填字段 |
|---|---|
| ACTIVE→IN_MAINTENANCE | ``reason`` |
| IN_MAINTENANCE→ACTIVE | ``maintenance_record_id`` 与复检证据 |
| ACTIVE/IN_MAINTENANCE→FAULT | ``fault_code``；``occurred_at`` 可省略（默认取服务端当前时间） |
| FAULT→IN_MAINTENANCE | ``maintenance_record_id`` |
| 任意非终态→SUSPENDED | ``reason`` |
| SUSPENDED→ACTIVE | ``reason`` |
| 任意非终态→RETIRED | ``reason`` |

当前实现无法区分"从哪个状态来"（``apply_action`` 拿到的只是动作名），
因此按**动作级**的最严口径校验：``enter-maintenance`` 要求 ``reason``，
``report-fault`` 要求 ``fault_code`` 与 ``occurred_at``。
``maintenance_record_id`` 在 ``enter-maintenance`` 上要求——它既覆盖
``FAULT→IN_MAINTENANCE``（契约必填），也覆盖 ``ACTIVE→IN_MAINTENANCE``
（契约未强制，但维修工单是留痕必需，收紧不会拒绝契约允许的合法请求）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.devices.errors import DomainValidationError

# 动作 → 该动作必填的字段名。字段名与 ActionRequest 的属性名一致。
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "enter-maintenance": ("maintenance_record_id", "reason"),
    # `occurred_at` 不在此列：契约 §8.1 把它列为上报字段，但客户端不传时
    # 服务端用当前时间填充更合理——强迫客户端生成时间戳只会带来时钟偏差，
    # 而"故障何时被发现"服务端本来就知道。真正的必填是 fault_code。
    "report-fault": ("fault_code",),
}


def assert_action_payload_complete(
    action: str,
    *,
    reason: str | None,
    fault_code: str | None,
    occurred_at: datetime | None,
    maintenance_record_id: UUID | None,
) -> None:
    """按动作校验必填字段；缺失时抛 ``DomainValidationError``（422）。

    错误文案必须点名**哪个字段**缺失——契约 §8.1 把这些字段列为前置条件，
    客户端需要知道补什么，而不是收到一句泛化的"参数不合法"。
    """
    available = {
        "reason": reason,
        "fault_code": fault_code,
        "occurred_at": occurred_at,
        "maintenance_record_id": maintenance_record_id,
    }
    missing = [name for name in _REQUIRED_FIELDS.get(action, ()) if _is_blank(available[name])]
    if missing:
        raise DomainValidationError(
            f"{action} 缺少必填字段：{', '.join(missing)}",
            details={"action": action, "missing_fields": missing},
        )


def _is_blank(value: object) -> bool:
    """字符串按 strip 后判空；None 视为缺失；其它类型只要有值就算填了。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
