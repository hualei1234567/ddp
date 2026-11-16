"""Canonical Adapter 注册表与内置 Adapter（N2）。

覆盖 N2 完成定义里可自动化验证的部分：

- Adapter 注册、发现、版本机制可用；
- **两种不同来源映射到同一 Canonical Schema**；
- 字段映射与缺失字段可追溯；
- 原始字段不丢失。
"""

from __future__ import annotations

import json

import pytest
from ddp_data.adapters import (
    CANONICAL_EPISODE_FIELDS,
    AdapterNotFoundError,
    AdapterRegistrationError,
    AdapterRegistry,
    AdapterSpec,
    GenericJsonlParseError,
    build_default_registry,
    parse_generic_jsonl,
)

ALL_FIELDS = frozenset(item.name for item in CANONICAL_EPISODE_FIELDS)


def _spec(**overrides: object) -> AdapterSpec:
    base: dict[str, object] = {
        "adapter_id": "demo.basic",
        "version": "1.0.0",
        "format_name": "demo",
        "parser": lambda data: (),
        "supported_fields": ALL_FIELDS,
        "missing_fields": frozenset(),
    }
    base.update(overrides)
    return AdapterSpec(**base)  # type: ignore[arg-type]


def test_builtin_registry_has_two_adapters() -> None:
    registry = build_default_registry()
    ids = [spec.adapter_id for spec in registry.list_specs()]
    assert ids == ["generic.episode_jsonl", "lerobot.human1m_preview"]


def test_every_adapter_must_declare_all_fields() -> None:
    """字段必须穷尽声明：否则"数据没有"与"Adapter 漏了"无法区分。"""
    with pytest.raises(AdapterRegistrationError) as excinfo:
        _spec(supported_fields=frozenset({"episode_key"}), missing_fields=frozenset())
    assert "未表态" in str(excinfo.value)


def test_undeclared_field_name_is_rejected() -> None:
    with pytest.raises(AdapterRegistrationError) as excinfo:
        _spec(
            supported_fields=ALL_FIELDS | {"not_a_canonical_field"},
            missing_fields=frozenset(),
        )
    assert "未定义" in str(excinfo.value)


def test_field_cannot_be_both_supported_and_missing() -> None:
    with pytest.raises(AdapterRegistrationError) as excinfo:
        _spec(
            supported_fields=ALL_FIELDS,
            missing_fields=frozenset({"episode_key"}),
        )
    assert "既声明支持又声明缺失" in str(excinfo.value)


def test_adapter_id_requires_variant_suffix() -> None:
    with pytest.raises(AdapterRegistrationError):
        _spec(adapter_id="lerobot")


def test_version_is_required() -> None:
    """映射规则变化必须升版本，否则历史导入无法复现当时的映射规则。"""
    with pytest.raises(AdapterRegistrationError):
        _spec(version="")


def test_duplicate_registration_is_rejected() -> None:
    registry = AdapterRegistry()
    registry.register(_spec())
    with pytest.raises(AdapterRegistrationError):
        registry.register(_spec(version="2.0.0"))


def test_unknown_adapter_fails_loudly() -> None:
    registry = build_default_registry()
    with pytest.raises(AdapterNotFoundError) as excinfo:
        registry.get("nope.missing")
    assert "已注册" in str(excinfo.value)


# --------------------------------------------------------------------------
# 两种来源 → 同一 Canonical Schema（N2 完成定义的核心一条）
# --------------------------------------------------------------------------

HUMAN1M_LINE = {
    "file_name": "videos/demo_001_0b4a67b0_main.mp4",
    "example_id": "demo_001",
    "task_name": "Sew lace fabric",
    "instruction": "The worker sews white lace fabric.",
    "view": "Egocentric main",
    "setting": "itw",
    "modality": "stereo",
    "has_wrist": False,
    "environment": "Industrial workspace",
    "episode_seconds": 237.77,
    "source_fps": 30.0,
    "excerpt_start_seconds": 69.0,
    "excerpt_seconds": 12.0,
}

GENERIC_LINE = {
    "episode_key": "demo_001",
    "task_name": "Sew lace fabric",
    "instruction": "The worker sews white lace fabric.",
    "media_assets": [{"path": "videos/demo_001_main.mp4", "camera": "main"}],
    "episode_seconds": 12.0,
    "environment": "Industrial workspace",
    "setting": "itw",
}


def test_two_formats_map_to_same_canonical_shape() -> None:
    """Human-1M 的平铺元数据与通用 JSONL 产出**同一 Canonical 类型**。

    这是 N2「两种不同数据来源映射到同一 Canonical Schema」的可执行证明：
    两个解析器返回的都是 ``CanonicalEpisode``，字段可直接横向比较。
    """
    registry = build_default_registry()

    human1m = registry.get("lerobot.human1m_preview").parser(json.dumps(HUMAN1M_LINE).encode())
    generic = registry.get("generic.episode_jsonl").parser(json.dumps(GENERIC_LINE).encode())

    assert len(human1m) == len(generic) == 1
    left, right = human1m[0], generic[0]
    # 同一类型、同一字段集合：下游不需要为来源分叉。
    assert type(left) is type(right)
    assert {f.name for f in CANONICAL_EPISODE_FIELDS} == set(left.__slots__)
    assert left.episode_key == right.episode_key == "demo_001"
    assert left.task_name == right.task_name
    assert left.instruction == right.instruction
    assert left.media_assets[0].camera == right.media_assets[0].camera == "main"


def test_raw_fields_are_preserved() -> None:
    """原始字段必须保留，不得因映射而丢失（蓝图 §5.4）。"""
    registry = build_default_registry()
    episode = registry.get("lerobot.human1m_preview").parser(json.dumps(HUMAN1M_LINE).encode())[0]
    assert episode.raw == HUMAN1M_LINE

    generic = parse_generic_jsonl(json.dumps(GENERIC_LINE).encode())[0]
    assert generic.raw == GENERIC_LINE


def test_missing_optional_fields_stay_none_not_zero() -> None:
    """缺失字段不得伪装成 0 或空值（蓝图 §5.4 明令禁止）。"""
    minimal = {
        "episode_key": "e1",
        "task_name": "t",
        "instruction": "i",
        "media_assets": [{"path": "a.mp4"}],
    }
    episode = parse_generic_jsonl(json.dumps(minimal).encode())[0]
    assert episode.environment is None
    assert episode.setting is None
    assert episode.episode_seconds is None
    assert episode.source_fps is None


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------


def test_detect_routes_human1m_metadata() -> None:
    registry = build_default_registry()
    spec = registry.detect(json.dumps(HUMAN1M_LINE).encode())
    assert spec is not None
    assert spec.adapter_id == "lerobot.human1m_preview"


def test_detect_routes_generic_jsonl() -> None:
    registry = build_default_registry()
    spec = registry.detect(json.dumps(GENERIC_LINE).encode())
    assert spec is not None
    assert spec.adapter_id == "generic.episode_jsonl"


def test_detect_returns_none_for_unknown_content() -> None:
    registry = build_default_registry()
    assert registry.detect(b"\x00\x01\x02 not json at all") is None


def test_human1m_declares_semantic_gaps_honestly() -> None:
    """Human-1M 缺动作/位姿/时间同步/标定——必须在 metadata 里如实登记。

    把它说成"支持"会让下游以为拿到了动作序列，而实际是空的。
    """
    spec = build_default_registry().get("lerobot.human1m_preview")
    gaps = spec.metadata.get("semantic_gaps")
    assert isinstance(gaps, tuple)
    assert {"action_sequence", "robot_pose", "time_sync", "calibration"} <= set(gaps)


# --------------------------------------------------------------------------
# 解析器的负向路径：坏数据必须显式失败
# --------------------------------------------------------------------------


def test_invalid_json_line_fails_with_line_number() -> None:
    with pytest.raises(GenericJsonlParseError) as excinfo:
        parse_generic_jsonl(b'{"episode_key": "e1",}\n')
    assert "第 1 行" in str(excinfo.value)


def test_duplicate_episode_key_is_rejected() -> None:
    line = json.dumps(GENERIC_LINE)
    with pytest.raises(GenericJsonlParseError) as excinfo:
        parse_generic_jsonl(f"{line}\n{line}\n".encode())
    assert "重复" in str(excinfo.value)


def test_episode_without_assets_is_rejected() -> None:
    bad = {"episode_key": "e1", "task_name": "t", "instruction": "i", "media_assets": []}
    with pytest.raises(GenericJsonlParseError) as excinfo:
        parse_generic_jsonl(json.dumps(bad).encode())
    assert "media_assets" in str(excinfo.value)


def test_empty_input_is_rejected() -> None:
    with pytest.raises(GenericJsonlParseError):
        parse_generic_jsonl(b"\n\n")
