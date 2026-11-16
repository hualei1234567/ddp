"""Canonical Adapter：注册、发现与字段映射声明（N2）。"""

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
    AdapterNotFoundError,
    AdapterRegistrationError,
    AdapterRegistry,
    AdapterSpec,
    CanonicalField,
    Parser,
)

__all__ = [
    "CANONICAL_EPISODE_FIELDS",
    "AdapterNotFoundError",
    "AdapterRegistrationError",
    "AdapterRegistry",
    "AdapterSpec",
    "CanonicalField",
    "GenericJsonlParseError",
    "Parser",
    "build_default_registry",
    "parse_generic_jsonl",
    "parse_human1m_preview",
    "parse_jsonl_rows",
    "register_builtin_adapters",
]
