"""数据源许可门禁（V61-023）。

## 缺口

平台在登记数据源时已经记录了 ``license`` 与 ``access_condition`` / ``is_gated``，
但这些字段**只用于一致性校验**（GATED 与 is_gated 必须一致），从未在**导入**这一步
产生任何约束：一个需要申请访问权限（GATED）或使用非宽松许可（如 CC-BY-NC）的数据源，
可以被直接全量导入并进入下游数据集与训练链路，没有任何留痕表明"操作人知道这个限制"。

## 门禁规则（唯一权威实现）

1. **GATED 数据源必须显式确认**：``access_condition=GATED`` 或 ``is_gated=True``
   时，导入命令必须带 ``license_acknowledged=True``，否则拒绝；
2. **非宽松许可必须显式确认**：许可属于"禁止商用 / 禁止衍生 / 仅研究"类
   （白名单外）时同样要求确认；
3. **宽松许可免确认**：MIT / Apache-2.0 / BSD / CC0 / CC-BY（允许商用）等直接放行；
4. **许可缺失视为最严格**：空许可字符串按"未知许可"处理，要求确认——
   "没说许可"不等于"随便用"。

本模块纯领域、无 IO；调用方（应用用例）负责把确认事实写入审计留痕。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.datasources.errors import DomainValidationError

#: 可直接导入、无需额外确认的宽松许可（小写归一后比较）。
PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        "mit",
        "apache-2.0",
        "apache2",
        "bsd",
        "bsd-2-clause",
        "bsd-3-clause",
        "cc0",
        "cc0-1.0",
        "cc-by",
        "cc-by-4.0",
        "cc-by-sa",
        "cc-by-sa-4.0",
        "unlicense",
        "public-domain",
    }
)


class LicenseGateReason(StrEnum):
    """需要确认的原因（写进审计与错误消息，便于解释"为什么要我确认"）。"""

    GATED_ACCESS = "GATED_ACCESS"
    RESTRICTED_LICENSE = "RESTRICTED_LICENSE"
    UNKNOWN_LICENSE = "UNKNOWN_LICENSE"


@dataclass(frozen=True, slots=True)
class LicenseGateDecision:
    """门禁判定结果。

    ``requires_acknowledgement=False`` 时 ``reasons`` 必为空——放行就是放行，
    不留"看起来有风险但没拦"的模糊状态。
    """

    requires_acknowledgement: bool
    reasons: tuple[LicenseGateReason, ...]
    license: str
    message: str | None = None


def _normalize(value: object) -> str:
    return str(value or "").strip().lower()


def evaluate_license_gate(
    *,
    license_name: str | None,
    access_condition: str | None,
    is_gated: bool,
) -> LicenseGateDecision:
    """判定该数据源的导入是否需要显式许可确认（纯函数）。"""
    normalized_license = _normalize(license_name)
    normalized_access = _normalize(access_condition)
    reasons: list[LicenseGateReason] = []

    if is_gated or normalized_access == "gated":
        reasons.append(LicenseGateReason.GATED_ACCESS)

    if not normalized_license:
        reasons.append(LicenseGateReason.UNKNOWN_LICENSE)
    elif normalized_license not in PERMISSIVE_LICENSES:
        reasons.append(LicenseGateReason.RESTRICTED_LICENSE)

    if not reasons:
        return LicenseGateDecision(
            requires_acknowledgement=False,
            reasons=(),
            license=normalized_license,
        )

    parts: list[str] = []
    if LicenseGateReason.GATED_ACCESS in reasons:
        parts.append("该数据源需要申请访问权限（GATED）")
    if LicenseGateReason.UNKNOWN_LICENSE in reasons:
        parts.append("数据源未声明许可")
    elif LicenseGateReason.RESTRICTED_LICENSE in reasons:
        parts.append(f"数据源许可为 {normalized_license}，不属于宽松许可白名单")
    message = (
        "；".join(parts)
        + "。导入前请核对数据集卡片的许可与访问条件；确认后请在请求中显式传 "
        "license_acknowledged=true（该确认会写入审计留痕）。"
    )
    return LicenseGateDecision(
        requires_acknowledgement=True,
        reasons=tuple(reasons),
        license=normalized_license,
        message=message,
    )


def enforce_license_gate(
    *,
    license_name: str | None,
    access_condition: str | None,
    is_gated: bool,
    acknowledged: bool,
) -> LicenseGateDecision:
    """执行门禁：需要确认而未确认时抛 ``DomainValidationError``。

    已确认时返回判定结果，供调用方把 ``reasons`` 写进审计 details。
    """
    decision = evaluate_license_gate(
        license_name=license_name,
        access_condition=access_condition,
        is_gated=is_gated,
    )
    if decision.requires_acknowledgement and not acknowledged:
        raise DomainValidationError(decision.message or "该数据源导入需要显式许可确认")
    return decision


__all__ = [
    "PERMISSIVE_LICENSES",
    "LicenseGateDecision",
    "LicenseGateReason",
    "enforce_license_gate",
    "evaluate_license_gate",
]
