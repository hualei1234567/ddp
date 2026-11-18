"""训练配置摘要的脱敏与有界性单测（N7-002）。

脱敏规则本身复用 ``observability_redaction``（日志与配置同一口径），这里验证
组合行为：键名命中凭据、字符串值里的 URL/绝对路径/``token=...`` 被替换，
普通超参与版本串原样保留，超限与非法结构显式拒绝。
"""

from __future__ import annotations

import json

import pytest
from app.application.datasets.training_config_summary import (
    CONFIG_SUMMARY_MAX_DEPTH,
    REDACTED_VALUE,
    summarize_training_config,
)
from app.application.observability_redaction import is_secret_name
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.training_references import CONFIG_SUMMARY_MAX_BYTES


def test_secret_key_names_are_detected_without_literal_boundary_trick() -> None:
    for name in ("api_key", "hf_token", "access-token", "password", "private_key"):
        assert is_secret_name(name), name
    # 普通词里包含 token/secret 不应误伤。
    for name in ("tokenizer", "monkey", "learning_rate", "code_version"):
        assert not is_secret_name(name), name


def test_summary_redacts_secret_keys_and_sensitive_values() -> None:
    summary = summarize_training_config(
        {
            "learning_rate": 0.001,
            "batch_size": 32,
            "use_amp": True,
            "notes": None,
            "api_key": "sk-live-should-not-leak",
            "hf_token": "hf_should-not-leak",
            "nested": {"password": "p@ss", "max_steps": 1000},
            "checkpoint_dir": "/home/trainer/checkpoints",
            "endpoint": "https://internal.example/api/v1",
            "callback": "token=abc123&x=1",
            "tag": "ddp.lerobot/1.0.0",
            "empty": "",
        }
    )

    assert summary["learning_rate"] == 0.001
    assert summary["batch_size"] == 32
    assert summary["use_amp"] is True
    assert summary["notes"] is None
    # 键名命中凭据：值整体替换，不保留任何前缀/长度线索。
    assert summary["api_key"] == REDACTED_VALUE
    assert summary["hf_token"] == REDACTED_VALUE
    assert summary["nested"]["password"] == REDACTED_VALUE
    assert summary["nested"]["max_steps"] == 1000
    # 值里的绝对路径、URL、token=... 由既有日志脱敏规则处理。
    assert "/home" not in json.dumps(summary)
    assert "internal.example" not in json.dumps(summary)
    assert "abc123" not in json.dumps(summary)
    # 普通版本串、空串不应被误伤。
    assert summary["tag"] == "ddp.lerobot/1.0.0"
    assert summary["empty"] == ""

    encoded = json.dumps(summary, ensure_ascii=False)
    for leaked in ("sk-live", "hf_should-not-leak", "p@ss"):
        assert leaked not in encoded


def test_summary_keeps_empty_config_distinct_from_missing() -> None:
    assert summarize_training_config(None) == {}
    assert summarize_training_config({}) == {}


def test_summary_rejects_non_object_oversize_and_too_deep() -> None:
    with pytest.raises(DomainValidationError):
        summarize_training_config(["not", "an", "object"])  # type: ignore[arg-type]

    with pytest.raises(DomainValidationError):
        summarize_training_config({"padding": "x" * (CONFIG_SUMMARY_MAX_BYTES + 1)})

    nested: dict[str, object] = {"leaf": 1}
    for _ in range(CONFIG_SUMMARY_MAX_DEPTH):
        nested = {"level": nested}
    with pytest.raises(DomainValidationError):
        summarize_training_config(nested)

    with pytest.raises(DomainValidationError):
        summarize_training_config({"bad": object()})
