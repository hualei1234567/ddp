"""标注路由共享类型与请求辅助。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Path

from app.api.deps import get_project_scope, get_write_scope
from app.api.deps import request_id as request_id  # noqa: F401 - 兼容既有导入路径
from app.api.v1.preconditions import require_expected_version
from app.domain.identity.models import ProjectScope

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
ActionName = Annotated[str, Path(min_length=1, max_length=64)]


# 系统内部动作前缀。这些动作由应用层在状态推进时自行发起（例如提交后触发
# ``system-review``、租约到期触发 ``system-expire``），不是用户可以点的按钮；
# 它们若进入 ``allowed_actions``，前端会渲染出一个语义错误、后端也不该由用户
# 触发的动作。对外动作集合的定义只保留这一处，供响应序列化共用。
SYSTEM_ACTION_PREFIX = "system-"


def public_actions(allowed: frozenset[str]) -> list[str]:
    """把状态机允许的动作收敛成对外公开动作（排序稳定）。"""
    return sorted(action for action in allowed if not action.startswith(SYSTEM_ACTION_PREFIX))
