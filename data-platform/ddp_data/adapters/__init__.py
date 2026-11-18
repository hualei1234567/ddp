"""Canonical Adapter：注册、发现、字段映射与可选能力声明（N2 / N3-002）。"""

from ddp_data.adapters.builtin import (
    GenericJsonlParseError,
    build_default_registry,
    parse_generic_jsonl,
    parse_human1m_preview,
    parse_jsonl_rows,
    register_builtin_adapters,
)
from ddp_data.adapters.registry import (
    CANONICAL_EPISODE_FIELDS,
    AdapterCapability,
    AdapterCapabilityError,
    AdapterNotFoundError,
    AdapterRegistrationError,
    AdapterRegistry,
    AdapterSpec,
    CanonicalField,
    Fetcher,
    Parser,
    Verifier,
    Writer,
)

__all__ = [
    "CANONICAL_EPISODE_FIELDS",
    "AdapterCapability",
    "AdapterCapabilityError",
    "AdapterNotFoundError",
    "AdapterRegistrationError",
    "AdapterRegistry",
    "AdapterSpec",
    "CanonicalField",
    "Fetcher",
    "GenericJsonlParseError",
    "Parser",
    "Verifier",
    "Writer",
    "build_default_registry",
    "parse_generic_jsonl",
    "parse_human1m_preview",
    "parse_jsonl_rows",
    "register_builtin_adapters",
]
