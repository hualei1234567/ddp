"""Adapter 解析接线：按注册表选解析器，失配显式失败。"""

from __future__ import annotations

import json

import pytest
from app.application.datasources.adapter_resolver import resolve_and_parse
from app.domain.datasources.errors import DomainValidationError
from ddp_data.adapters import AdapterRegistry
from ddp_data.adapters.builtin import register_builtin_adapters

GENERIC = json.dumps(
    {
        "episode_key": "e1",
        "task_name": "t",
        "instruction": "i",
        "media_assets": [{"path": "a.mp4"}],
    }
).encode()

HUMAN1M = json.dumps(
    {
        "file_name": "videos/demo_001_main.mp4",
        "example_id": "demo_001",
        "task_name": "Sew lace fabric",
        "instruction": "x",
        "view": "Egocentric main",
    }
).encode()


def test_detects_adapter_from_content() -> None:
    resolved = resolve_and_parse(GENERIC)
    assert resolved.spec.adapter_id == "generic.episode_jsonl"
    assert len(resolved.episodes) == 1


def test_detects_human1m_metadata() -> None:
    resolved = resolve_and_parse(HUMAN1M)
    assert resolved.spec.adapter_id == "lerobot.human1m_preview"


def test_explicit_adapter_id_wins_over_detection() -> None:
    resolved = resolve_and_parse(GENERIC, adapter_id="generic.episode_jsonl")
    assert resolved.spec.adapter_id == "generic.episode_jsonl"


def test_unknown_content_fails_loudly() -> None:
    """探测不出格式时必须显式失败，不能返回空让导入"成功"。"""
    with pytest.raises(DomainValidationError) as excinfo:
        resolve_and_parse(b"not json")
    assert "无法从内容判断" in str(excinfo.value)


def test_unknown_adapter_id_fails_loudly() -> None:
    with pytest.raises(DomainValidationError):
        resolve_and_parse(GENERIC, adapter_id="nope.missing")


def test_parse_error_is_wrapped_as_domain_error() -> None:
    bad = b'{"episode_key": "e1", "task_name": "t", "instruction": "i", "media_assets": []}'
    with pytest.raises(DomainValidationError):
        resolve_and_parse(bad)


def test_empty_registry_fails_with_guidance() -> None:
    with pytest.raises(DomainValidationError):
        resolve_and_parse(GENERIC, registry=AdapterRegistry())


def test_resolved_adapter_exposes_declaration() -> None:
    """解析结果要能回答「用了哪个 Adapter、哪个版本、缺什么」。"""
    resolved = resolve_and_parse(HUMAN1M)
    assert resolved.spec.version
    gaps = resolved.spec.metadata.get("semantic_gaps")
    assert isinstance(gaps, tuple) and gaps


def test_custom_registry_is_honoured() -> None:
    registry = register_builtin_adapters(AdapterRegistry())
    resolved = resolve_and_parse(GENERIC, registry=registry)
    assert resolved.spec.adapter_id == "generic.episode_jsonl"
