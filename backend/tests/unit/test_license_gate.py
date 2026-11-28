"""数据源许可门禁测试（V61-023）。

钉住四条规则：

1. GATED 数据源必须显式确认；
2. 非宽松许可必须显式确认；
3. 许可缺失按最严格处理（"没说许可" ≠ "随便用"）；
4. 宽松许可直接放行，且放行时不留模糊的"有风险"记录。
"""

from __future__ import annotations

import pytest
from app.domain.datasources.errors import DomainValidationError
from app.domain.datasources.license_gate import (
    PERMISSIVE_LICENSES,
    LicenseGateReason,
    enforce_license_gate,
    evaluate_license_gate,
)


@pytest.mark.parametrize(
    "license_name",
    ["MIT", "mit", "Apache-2.0", "apache2", "BSD-3-Clause", "cc0-1.0", "CC-BY-4.0"],
)
def test_permissive_license_without_gate_is_allowed(license_name: str) -> None:
    decision = evaluate_license_gate(
        license_name=license_name, access_condition="PUBLIC", is_gated=False
    )

    assert decision.requires_acknowledgement is False
    assert decision.reasons == ()
    assert decision.message is None
    assert decision.license == license_name.lower()


def test_gated_access_requires_acknowledgement() -> None:
    decision = evaluate_license_gate(license_name="mit", access_condition="GATED", is_gated=True)

    assert decision.requires_acknowledgement is True
    assert decision.reasons == (LicenseGateReason.GATED_ACCESS,)
    assert "申请访问权限" in (decision.message or "")


def test_is_gated_flag_alone_triggers_gate() -> None:
    """即使 access_condition 缺失，is_gated=True 也必须拦。"""
    decision = evaluate_license_gate(license_name="mit", access_condition=None, is_gated=True)

    assert LicenseGateReason.GATED_ACCESS in decision.reasons


@pytest.mark.parametrize(
    "license_name", ["cc-by-nc-4.0", "cc-by-nc-sa-4.0", "gpl-3.0", "proprietary", "research-only"]
)
def test_restricted_license_requires_acknowledgement(license_name: str) -> None:
    decision = evaluate_license_gate(
        license_name=license_name, access_condition="PUBLIC", is_gated=False
    )

    assert decision.requires_acknowledgement is True
    assert decision.reasons == (LicenseGateReason.RESTRICTED_LICENSE,)
    assert license_name in (decision.message or "")


@pytest.mark.parametrize("license_name", [None, "", "   "])
def test_missing_license_is_treated_as_strictest(license_name: str | None) -> None:
    decision = evaluate_license_gate(
        license_name=license_name, access_condition="PUBLIC", is_gated=False
    )

    assert decision.requires_acknowledgement is True
    assert decision.reasons == (LicenseGateReason.UNKNOWN_LICENSE,)
    assert "未声明许可" in (decision.message or "")


def test_gated_and_restricted_reasons_accumulate() -> None:
    decision = evaluate_license_gate(
        license_name="cc-by-nc-4.0", access_condition="GATED", is_gated=True
    )

    assert set(decision.reasons) == {
        LicenseGateReason.GATED_ACCESS,
        LicenseGateReason.RESTRICTED_LICENSE,
    }


def test_enforce_raises_when_not_acknowledged() -> None:
    with pytest.raises(DomainValidationError, match="license_acknowledged=true"):
        enforce_license_gate(
            license_name="cc-by-nc-4.0",
            access_condition="PUBLIC",
            is_gated=False,
            acknowledged=False,
        )


def test_enforce_passes_when_acknowledged() -> None:
    decision = enforce_license_gate(
        license_name="cc-by-nc-4.0",
        access_condition="GATED",
        is_gated=True,
        acknowledged=True,
    )

    assert decision.requires_acknowledgement is True
    assert decision.reasons  # 供审计写进 details


def test_enforce_on_permissive_license_ignores_acknowledgement() -> None:
    decision = enforce_license_gate(
        license_name="apache-2.0",
        access_condition="PUBLIC",
        is_gated=False,
        acknowledged=False,
    )

    assert decision.requires_acknowledgement is False


def test_permissive_set_is_lowercase_and_covers_common_free_licenses() -> None:
    assert all(item == item.lower() for item in PERMISSIVE_LICENSES)
    for required in ("mit", "apache-2.0", "cc0", "bsd-3-clause"):
        assert required in PERMISSIVE_LICENSES
    # 非商用许可绝不能在白名单里
    assert "cc-by-nc-4.0" not in PERMISSIVE_LICENSES
