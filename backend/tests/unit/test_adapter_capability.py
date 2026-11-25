"""Adapter 六级能力分级矩阵与证据存在性校验测试（ADR-0048 / V51-005）。

覆盖：
- 六级枚举 ``FormatCapabilityLevel`` 的顺序、``rank``、``at_least`` 与 ``implied_levels`` 连续推导；
- ``FormatCapabilityProfile`` 的连续性防跳级校验（跳级、越界、重复、缺 DETECT 均显式抛错）；
- ``max_level > DETECT`` 时空 ``evidence`` 抛错、空字符串条目抛错；
- 诚实边界硬规则：空 ``limitations`` 抛错、空 ``parser_version`` / ``format_name`` 抛错；
- ``AdapterSpec`` 向后兼容（未传 ``capability_profile`` 时回退 ``DETECT``）与显式挂载；
- ``AdapterCapability`` 五值接口枚举保持不变，与六级解析深度正交并存；
- 内置 ``BUILTIN_FORMAT_CAPABILITY_MATRIX`` 与 ``build_default_registry()`` 的真实等级与诚实边界；
- ``scripts/check_adapter_capability.py`` 在真实仓库上 exit 0，注入坏文件/坏测试函数名时 exit 1。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from ddp_data import (
    BUILTIN_FORMAT_CAPABILITY_MATRIX,
    CapabilityValidationError,
    FormatCapabilityLevel,
    FormatCapabilityProfile,
)
from ddp_data.adapters import (
    CANONICAL_EPISODE_FIELDS,
    AdapterCapability,
    AdapterRegistrationError,
    AdapterRegistry,
    AdapterSpec,
    build_default_registry,
    parse_generic_jsonl,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_adapter_capability.py"
ALL_FIELDS = frozenset(item.name for item in CANONICAL_EPISODE_FIELDS)


def _load_check_module():
    spec = importlib.util.spec_from_file_location("check_adapter_capability", CHECK_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_adapter_spec(**overrides: object) -> AdapterSpec:
    base: dict[str, object] = {
        "adapter_id": "demo.cap_test",
        "version": "1.0.0",
        "format_name": "demo",
        "parser": parse_generic_jsonl,
        "supported_fields": ALL_FIELDS,
        "missing_fields": frozenset(),
    }
    base.update(overrides)
    return AdapterSpec(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. 六级枚举顺序与连续推导
# ---------------------------------------------------------------------------


def test_six_capability_levels_order_and_ranks() -> None:
    levels = list(FormatCapabilityLevel)
    assert [lvl.value for lvl in levels] == [
        "detect",
        "inspect",
        "payload_parse",
        "stream_extract",
        "episode_build",
        "round_trip",
    ]
    assert [lvl.rank for lvl in levels] == [1, 2, 3, 4, 5, 6]

    for idx, current in enumerate(levels):
        assert current.implied_levels() == tuple(levels[: idx + 1])
        for target in levels[: idx + 1]:
            assert current.at_least(target) is True
        for higher in levels[idx + 1 :]:
            assert current.at_least(higher) is False


def test_at_least_rejects_non_enum_argument() -> None:
    with pytest.raises(TypeError, match="FormatCapabilityLevel"):
        FormatCapabilityLevel.INSPECT.at_least("inspect")  # type: ignore[arg-type]


def test_five_value_adapter_capability_remains_unchanged() -> None:
    """ADR-0048 铁律：既有五值接口能力枚举绝不修改。"""
    assert {cap.value for cap in AdapterCapability} == {
        "parse",
        "detect",
        "fetch",
        "verify",
        "write",
    }


# ---------------------------------------------------------------------------
# 2. FormatCapabilityProfile 防虚报约束（跳级、缺证据、空限制）
# ---------------------------------------------------------------------------


def test_profile_derives_contiguous_satisfied_levels_by_default() -> None:
    profile = FormatCapabilityProfile(
        format_name="demo_fmt",
        max_level=FormatCapabilityLevel.STREAM_EXTRACT,
        evidence=("backend/tests/unit/test_adapter_registry.py",),
        limitations=("不含视频流解码",),
        parser_version="1.2.0",
    )
    assert profile.verified_levels == (
        FormatCapabilityLevel.DETECT,
        FormatCapabilityLevel.INSPECT,
        FormatCapabilityLevel.PAYLOAD_PARSE,
        FormatCapabilityLevel.STREAM_EXTRACT,
    )
    assert profile.satisfied_levels == frozenset(profile.verified_levels)
    assert profile.supports_level(FormatCapabilityLevel.PAYLOAD_PARSE) is True
    assert profile.supports_level(FormatCapabilityLevel.EPISODE_BUILD) is False

    serialized = profile.as_dict()
    assert serialized["max_level"] == "stream_extract"
    assert serialized["max_level_rank"] == 4
    assert serialized["satisfied_levels"] == [
        "detect",
        "inspect",
        "payload_parse",
        "stream_extract",
    ]


def test_detect_only_profile_allows_empty_evidence_with_explicit_limitations() -> None:
    profile = FormatCapabilityProfile(
        format_name="raw_magic_only",
        max_level=FormatCapabilityLevel.DETECT,
        evidence=(),
        limitations=("仅识别文件魔数，未实现元数据与 payload 解析",),
        parser_version="0.1.0",
    )
    assert profile.satisfied_levels == frozenset({FormatCapabilityLevel.DETECT})


@pytest.mark.parametrize(
    "level",
    [
        FormatCapabilityLevel.INSPECT,
        FormatCapabilityLevel.PAYLOAD_PARSE,
        FormatCapabilityLevel.STREAM_EXTRACT,
        FormatCapabilityLevel.EPISODE_BUILD,
        FormatCapabilityLevel.ROUND_TRIP,
    ],
)
def test_higher_than_detect_without_evidence_raises(
    level: FormatCapabilityLevel,
) -> None:
    with pytest.raises(CapabilityValidationError, match="evidence 严禁为空"):
        FormatCapabilityProfile(
            format_name="dishonest_fmt",
            max_level=level,
            evidence=(),
            limitations=("已知限制",),
            parser_version="1.0.0",
        )


@pytest.mark.parametrize(
    "bad_verified_levels",
    [
        # 跳过 STREAM_EXTRACT 直接宣称 EPISODE_BUILD
        (
            FormatCapabilityLevel.DETECT,
            FormatCapabilityLevel.INSPECT,
            FormatCapabilityLevel.PAYLOAD_PARSE,
            FormatCapabilityLevel.EPISODE_BUILD,
        ),
        # 缺少起点 DETECT
        (
            FormatCapabilityLevel.INSPECT,
            FormatCapabilityLevel.PAYLOAD_PARSE,
        ),
        # 含有超过 max_level 的级别
        (
            FormatCapabilityLevel.DETECT,
            FormatCapabilityLevel.INSPECT,
            FormatCapabilityLevel.PAYLOAD_PARSE,
            FormatCapabilityLevel.ROUND_TRIP,
        ),
        # 重复级别
        (
            FormatCapabilityLevel.DETECT,
            FormatCapabilityLevel.DETECT,
            FormatCapabilityLevel.INSPECT,
        ),
        # 空元组
        (),
    ],
)
def test_non_contiguous_or_invalid_verified_levels_raise(
    bad_verified_levels: tuple[FormatCapabilityLevel, ...],
) -> None:
    max_lvl = (
        FormatCapabilityLevel.EPISODE_BUILD
        if FormatCapabilityLevel.EPISODE_BUILD in bad_verified_levels
        else FormatCapabilityLevel.PAYLOAD_PARSE
    )
    with pytest.raises(CapabilityValidationError):
        FormatCapabilityProfile(
            format_name="skipped_fmt",
            max_level=max_lvl,
            verified_levels=bad_verified_levels,
            evidence=("backend/tests/unit/test_adapter_registry.py",),
            limitations=("测试跳级拦截",),
            parser_version="1.0.0",
        )


def test_empty_limitations_raises_validation_error() -> None:
    with pytest.raises(CapabilityValidationError, match="limitations 严禁为空"):
        FormatCapabilityProfile(
            format_name="no_limit_fmt",
            max_level=FormatCapabilityLevel.DETECT,
            evidence=(),
            limitations=(),
            parser_version="1.0.0",
        )


@pytest.mark.parametrize(
    ("kwargs", "expected_msg"),
    [
        ({"format_name": "   "}, "format_name"),
        ({"parser_version": ""}, "parser_version"),
        ({"evidence": ("  ",)}, "evidence\\[0\\]"),
        ({"limitations": ("valid", "")}, "limitations\\[1\\]"),
    ],
)
def test_blank_strings_in_profile_are_rejected(
    kwargs: dict[str, object], expected_msg: str
) -> None:
    params: dict[str, object] = {
        "format_name": "fmt",
        "max_level": FormatCapabilityLevel.INSPECT,
        "evidence": ("backend/tests/unit/test_adapter_registry.py",),
        "limitations": ("仅元数据",),
        "parser_version": "1.0.0",
    }
    params.update(kwargs)
    with pytest.raises(CapabilityValidationError, match=expected_msg):
        FormatCapabilityProfile(**params)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. AdapterSpec 向后兼容与内置注册表核验
# ---------------------------------------------------------------------------


def test_adapter_spec_without_profile_defaults_to_detect() -> None:
    spec = _make_adapter_spec()
    assert spec.capability_profile is None
    assert spec.effective_capability_level is FormatCapabilityLevel.DETECT
    assert spec.satisfies_format_level(FormatCapabilityLevel.DETECT) is True
    assert spec.satisfies_format_level(FormatCapabilityLevel.INSPECT) is False


def test_adapter_spec_rejects_invalid_capability_profile_type() -> None:
    with pytest.raises(AdapterRegistrationError, match="FormatCapabilityProfile"):
        _make_adapter_spec(capability_profile="invalid-profile")


def test_registry_filters_by_format_level() -> None:
    registry = AdapterRegistry()
    detect_only = registry.register(_make_adapter_spec(adapter_id="demo.detect_only"))
    inspect_spec = registry.register(
        _make_adapter_spec(
            adapter_id="demo.inspect_ready",
            capability_profile=FormatCapabilityProfile(
                format_name="demo",
                max_level=FormatCapabilityLevel.INSPECT,
                evidence=("backend/tests/unit/test_adapter_registry.py",),
                limitations=("仅读头部元数据",),
                parser_version="1.0.0",
            ),
        )
    )

    assert registry.list_by_format_level(FormatCapabilityLevel.DETECT) == (
        detect_only,
        inspect_spec,
    )
    assert registry.list_by_format_level(FormatCapabilityLevel.INSPECT) == (inspect_spec,)
    assert registry.list_by_format_level(FormatCapabilityLevel.PAYLOAD_PARSE) == ()


def test_builtin_adapters_carry_honest_inspect_profiles() -> None:
    registry = build_default_registry()
    human1m = registry.get("lerobot.human1m_preview")
    generic = registry.get("generic.episode_jsonl")

    for spec in (human1m, generic):
        assert spec.capability_profile is not None
        assert spec.effective_capability_level is FormatCapabilityLevel.INSPECT
        assert spec.capability_profile.evidence
        assert spec.capability_profile.limitations


def test_builtin_format_capability_matrix_reflects_verified_reality() -> None:
    expected_levels = {
        "hdf5": FormatCapabilityLevel.ROUND_TRIP,
        "rosbag": FormatCapabilityLevel.INSPECT,
        "mp4": FormatCapabilityLevel.INSPECT,
        "parquet": FormatCapabilityLevel.INSPECT,
        "mcap": FormatCapabilityLevel.INSPECT,
        "lerobot": FormatCapabilityLevel.ROUND_TRIP,
        "generic_jsonl": FormatCapabilityLevel.INSPECT,
    }
    assert set(BUILTIN_FORMAT_CAPABILITY_MATRIX) == set(expected_levels)
    for fmt, expected_level in expected_levels.items():
        profile = BUILTIN_FORMAT_CAPABILITY_MATRIX[fmt]
        assert profile.format_name == fmt
        assert profile.max_level is expected_level

    # HDF5 诚实边界必须明确区分 robomimic 布局与通用任意 HDF5，并指出无图像流
    hdf5_limits = " ".join(BUILTIN_FORMAT_CAPABILITY_MATRIX["hdf5"].limitations)
    assert "robomimic_hdf5_v1.5" in hdf5_limits
    assert "INSPECT" in hdf5_limits
    assert "不含图像" in hdf5_limits


# ---------------------------------------------------------------------------
# 4. CI 校验脚本 check_adapter_capability.py 正向与反向验证
# ---------------------------------------------------------------------------


def test_check_adapter_capability_script_exits_zero() -> None:
    completed = subprocess.run(  # noqa: S603 - 固定仓库内受控脚本调用
        [sys.executable, str(CHECK_SCRIPT)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "校验通过" in completed.stdout


def test_check_script_fails_on_missing_file_or_missing_test_symbol() -> None:
    check_mod = _load_check_module()

    bad_file_profile = FormatCapabilityProfile(
        format_name="ghost_file",
        max_level=FormatCapabilityLevel.INSPECT,
        evidence=("backend/tests/unit/test_non_existent_file_xyz.py",),
        limitations=("测试坏文件路径",),
        parser_version="1.0.0",
    )
    bad_symbol_profile = FormatCapabilityProfile(
        format_name="ghost_symbol",
        max_level=FormatCapabilityLevel.INSPECT,
        evidence=("backend/tests/unit/test_adapter_registry.py::test_definitely_missing_symbol",),
        limitations=("测试坏测试函数名",),
        parser_version="1.0.0",
    )

    problems = check_mod.validate_capability_profiles(
        matrix={
            "ghost_file": bad_file_profile,
            "ghost_symbol": bad_symbol_profile,
        },
        adapter_specs=(),
        repo_root=REPO_ROOT,
    )
    assert len(problems) == 2
    assert any("文件不存在" in item for item in problems)
    assert any("符号不存在" in item for item in problems)


def test_check_script_rejects_path_traversal_and_missing_adapter_profile() -> None:
    check_mod = _load_check_module()

    traversal_profile = FormatCapabilityProfile(
        format_name="traversal",
        max_level=FormatCapabilityLevel.INSPECT,
        evidence=("../outside_repo.py",),
        limitations=("路径穿越拦截",),
        parser_version="1.0.0",
    )
    bare_adapter = _make_adapter_spec(adapter_id="demo.unprofiled")

    problems = check_mod.validate_capability_profiles(
        matrix={"traversal": traversal_profile},
        adapter_specs=(bare_adapter,),
        repo_root=REPO_ROOT,
        require_adapter_profiles=True,
    )
    assert any("相对路径" in item for item in problems)
    assert any("未声明 capability_profile" in item for item in problems)
