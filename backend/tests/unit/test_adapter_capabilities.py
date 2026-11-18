"""Adapter 接口能力扩展（DDP-V21-N3-002）。

覆盖：

- 旧式 ``AdapterSpec``（只有 ``parse`` / ``detect``）不改造即可注册（向后兼容）；
- 能力集由实际实现推导，注册表可按能力检索；
- 未实现能力显式抛 ``AdapterCapabilityError``，不静默返回 ``None``；
- 所有已注册 Adapter 满足统一接口契约，两个内置 Adapter 的能力声明一致。
"""

from __future__ import annotations

import json

import pytest
from ddp_data.adapters import (
    CANONICAL_EPISODE_FIELDS,
    AdapterCapability,
    AdapterCapabilityError,
    AdapterRegistrationError,
    AdapterRegistry,
    AdapterSpec,
    build_default_registry,
    parse_generic_jsonl,
)
from ddp_data.lerobot import CanonicalEpisode

ALL_FIELDS = frozenset(item.name for item in CANONICAL_EPISODE_FIELDS)

GENERIC_LINE = {
    "episode_key": "demo_001",
    "task_name": "Sew lace fabric",
    "instruction": "The worker sews white lace fabric.",
    "media_assets": [{"path": "videos/demo_001_main.mp4", "camera": "main"}],
    "episode_seconds": 12.0,
}

HUMAN1M_LINE = {
    "file_name": "videos/demo_001_main.mp4",
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


def _spec(**overrides: object) -> AdapterSpec:
    base: dict[str, object] = {
        "adapter_id": "demo.capability",
        "version": "1.0.0",
        "format_name": "demo",
        "parser": parse_generic_jsonl,
        "supported_fields": ALL_FIELDS,
        "missing_fields": frozenset(),
    }
    base.update(overrides)
    return AdapterSpec(**base)  # type: ignore[arg-type]


def _fetch(reference: str) -> bytes:
    return f"payload:{reference}".encode()


def _verify(data: bytes) -> None:
    if not data:
        raise ValueError("空内容")


def _write(episodes: tuple[CanonicalEpisode, ...]) -> bytes:
    return json.dumps([episode.episode_key for episode in episodes]).encode()


# --------------------------------------------------------------------------
# 向后兼容：旧 Adapter 无需改动
# --------------------------------------------------------------------------


def test_legacy_spec_registers_without_capability_fields() -> None:
    """只传原有参数的构造方式必须继续可用，且能力集不虚报。"""
    registry = AdapterRegistry()
    spec = registry.register(_spec(detect=lambda _sample: False))

    assert spec.capabilities == frozenset({AdapterCapability.PARSE, AdapterCapability.DETECT})
    assert spec.fetch is None
    assert spec.verify is None
    assert spec.write is None
    episodes = spec.parser(json.dumps(GENERIC_LINE).encode())
    assert [episode.episode_key for episode in episodes] == ["demo_001"]


def test_capability_enum_is_the_interface_contract() -> None:
    assert {capability.value for capability in AdapterCapability} == {
        "parse",
        "detect",
        "fetch",
        "verify",
        "write",
    }


# --------------------------------------------------------------------------
# 能力集由实现推导，并由注册表检索
# --------------------------------------------------------------------------


def test_capabilities_are_derived_from_implementations() -> None:
    spec = _spec(fetch=_fetch, verify=_verify, write=_write)

    assert spec.capabilities == frozenset(
        {
            AdapterCapability.PARSE,
            AdapterCapability.FETCH,
            AdapterCapability.VERIFY,
            AdapterCapability.WRITE,
        }
    )
    for capability in (
        AdapterCapability.FETCH,
        AdapterCapability.VERIFY,
        AdapterCapability.WRITE,
    ):
        assert spec.supports(capability)


def test_registry_queries_capability_sets() -> None:
    registry = AdapterRegistry()
    legacy = registry.register(_spec(adapter_id="demo.legacy", detect=lambda _sample: False))
    full = registry.register(_spec(adapter_id="demo.full", fetch=_fetch, verify=_verify))

    assert registry.list_by_capability(AdapterCapability.PARSE) == (full, legacy)
    assert registry.list_by_capability(AdapterCapability.FETCH) == (full,)
    assert registry.list_by_capability(AdapterCapability.WRITE) == ()
    assert registry.require_capability("demo.full", AdapterCapability.FETCH) is full


def test_non_callable_capability_is_rejected_at_registration() -> None:
    with pytest.raises(AdapterRegistrationError, match="fetch"):
        _spec(fetch="not-callable")
    with pytest.raises(AdapterRegistrationError, match="parser"):
        _spec(parser=None)


# --------------------------------------------------------------------------
# 不支持必须显式失败，不能静默
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("capability", "accessor"),
    [
        (AdapterCapability.FETCH, "require_fetch"),
        (AdapterCapability.VERIFY, "require_verify"),
        (AdapterCapability.WRITE, "require_write"),
    ],
)
def test_unsupported_capability_fails_loudly(capability: AdapterCapability, accessor: str) -> None:
    spec = _spec()

    with pytest.raises(AdapterCapabilityError) as excinfo:
        getattr(spec, accessor)()
    message = str(excinfo.value)
    assert spec.adapter_id in message
    assert capability.value in message

    with pytest.raises(AdapterCapabilityError):
        spec.require(capability)


def test_unsupported_detect_is_explicit_too() -> None:
    spec = _spec()
    with pytest.raises(AdapterCapabilityError):
        spec.require(AdapterCapability.DETECT)


def test_registry_rejects_capability_not_implemented() -> None:
    registry = AdapterRegistry()
    registry.register(_spec(adapter_id="demo.legacy"))

    with pytest.raises(AdapterCapabilityError):
        registry.require_capability("demo.legacy", AdapterCapability.WRITE)


def test_required_capabilities_are_callable() -> None:
    spec = _spec(fetch=_fetch, verify=_verify, write=_write)

    assert spec.require_fetch()("abc") == b"payload:abc"
    spec.require_verify()(b"non-empty")

    episodes = spec.parser(json.dumps(GENERIC_LINE).encode())
    assert json.loads(spec.require_write()(episodes)) == ["demo_001"]


# --------------------------------------------------------------------------
# 一致性：所有已注册 Adapter 满足同一接口契约
# --------------------------------------------------------------------------

_BUILTIN_SPECS = build_default_registry().list_specs()


@pytest.mark.parametrize("spec", _BUILTIN_SPECS, ids=lambda item: item.adapter_id)
def test_registered_adapter_satisfies_interface_contract(spec: AdapterSpec) -> None:
    assert callable(spec.parser)

    expected: set[AdapterCapability] = {AdapterCapability.PARSE}
    for capability, implementation in (
        (AdapterCapability.DETECT, spec.detect),
        (AdapterCapability.FETCH, spec.fetch),
        (AdapterCapability.VERIFY, spec.verify),
        (AdapterCapability.WRITE, spec.write),
    ):
        assert implementation is None or callable(implementation)
        if implementation is not None:
            expected.add(capability)

    assert isinstance(spec.capabilities, frozenset)
    assert spec.capabilities == frozenset(expected)

    assert spec.supported_fields.isdisjoint(spec.missing_fields)
    assert spec.supported_fields | spec.missing_fields == ALL_FIELDS

    for capability in AdapterCapability:
        if capability in spec.capabilities:
            assert spec.require(capability) is spec
        else:
            with pytest.raises(AdapterCapabilityError):
                spec.require(capability)

    if spec.detect is not None:
        assert isinstance(spec.detect(b"\x00not-a-real-sample"), bool)


def test_builtin_adapters_have_consistent_declarations() -> None:
    """两个内置 Adapter 的基础能力一致，且都未引入网络能力。"""
    registry = build_default_registry()
    specs = registry.list_specs()

    assert {spec.adapter_id for spec in specs} == {
        "generic.episode_jsonl",
        "lerobot.human1m_preview",
    }
    assert {spec.capabilities for spec in specs} == {
        frozenset({AdapterCapability.PARSE, AdapterCapability.DETECT})
    }
    # 只读解析不得依赖网络：内置 Adapter 不声明 fetch/verify/write。
    for capability in (
        AdapterCapability.FETCH,
        AdapterCapability.VERIFY,
        AdapterCapability.WRITE,
    ):
        assert registry.list_by_capability(capability) == ()

    sample_by_adapter = {
        "lerobot.human1m_preview": json.dumps(HUMAN1M_LINE).encode(),
        "generic.episode_jsonl": json.dumps(GENERIC_LINE).encode(),
    }
    produced = {spec.adapter_id: spec.parser(sample_by_adapter[spec.adapter_id]) for spec in specs}
    assert all(len(episodes) == 1 for episodes in produced.values())
    assert type(produced["lerobot.human1m_preview"][0]) is type(
        produced["generic.episode_jsonl"][0]
    )
    assert isinstance(produced["generic.episode_jsonl"][0], CanonicalEpisode)
