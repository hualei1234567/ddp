"""审计查询的领域值对象。

``AuditResult`` 与数据库 CHECK 约束 ``ck_audit_logs_result_allowed`` 一一对应：
只有 ``SUCCESS`` / ``FAILURE`` 两种取值。客户端不得用其他字符串过滤，
避免把「未知结果」当成默认成功。

时间区间语义为闭区间 ``[occurred_from, occurred_to]``；区间先后由 API 层
显式校验（写反返回 422），不在仓储里静默返回空集——审计是排障工具，
"没有数据"与"条件写反了"必须可区分。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AuditResult(StrEnum):
    """审计结果；与 ``audit_logs`` 的 CHECK 约束同源。"""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


def normalize_filter_text(value: str | None) -> str | None:
    """过滤用文本规范化：去首尾空白，空串视为未过滤。

    不改变大小写：``resource_type`` / ``action`` 的取值由各业务用例写入，
    历史数据里大小写并不统一，改成小写匹配会悄悄漏掉记录。
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """审计日志过滤条件。

    所有字段都是可选的等值/区间条件；``None`` 表示不限制。
    项目维度不在这里——它由服务端 :class:`ProjectScope` 强制，客户端无法传入。
    """

    resource_type: str | None = None
    resource_id: UUID | None = None
    action: str | None = None
    actor_user_id: UUID | None = None
    result: AuditResult | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None

    @classmethod
    def build(
        cls,
        *,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        action: str | None = None,
        actor_user_id: UUID | None = None,
        result: AuditResult | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> AuditQuery:
        """从 HTTP 输入构造；文本条件统一走 :func:`normalize_filter_text`。"""
        return cls(
            resource_type=normalize_filter_text(resource_type),
            resource_id=resource_id,
            action=normalize_filter_text(action),
            actor_user_id=actor_user_id,
            result=result,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )


__all__ = ["AuditQuery", "AuditResult", "normalize_filter_text"]
