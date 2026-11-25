"""导出产物回读校验（round-trip）。

writer 产出的每一行都必须能被 reader 还原并通过 schema 校验——
导出库自己保证自洽，不把"格式对不对"留给下游发现。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from . import LlmExportError
from .writers import ExportBundle

_ALLOWED_ROLES = ("system", "user", "assistant", "tool")


def _validate_messages(raw: Any, where: str) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise LlmExportError(f"{where}: messages 必须是非空数组")
    for msg in raw:
        if not isinstance(msg, dict) or set(msg) != {"role", "content"}:
            raise LlmExportError(f"{where}: 消息必须恰含 role/content")
        if msg["role"] not in _ALLOWED_ROLES:
            raise LlmExportError(f"{where}: 非法角色 {msg['role']!r}")
        if not isinstance(msg["content"], str) or not msg["content"].strip():
            raise LlmExportError(f"{where}: 消息内容不能为空")
    return raw


def parse_sft_bundle(bundle: ExportBundle) -> list[dict[str, Any]]:
    """回读 SFT 导出并逐行校验。返回每行 payload（含 metadata 若存在）。"""
    docs: list[dict[str, Any]] = []
    raw = bundle.files["sft_messages.jsonl"].decode("utf-8")
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise LlmExportError(f"sft_messages.jsonl 第 {lineno} 行为空")
        doc = json.loads(line)
        if "messages" not in doc:
            raise LlmExportError(f"第 {lineno} 行缺少 messages")
        _validate_messages(doc["messages"], f"第 {lineno} 行")
        extra = set(doc) - {"messages", "metadata"}
        if extra:
            raise LlmExportError(f"第 {lineno} 行存在未声明键 {sorted(extra)}")
        docs.append(doc)
    return docs


def parse_dpo_bundle(
    bundle: ExportBundle, *, conversational: bool | None = None
) -> list[dict[str, Any]]:
    """回读 DPO 导出并逐行校验；``conversational`` 缺省时从 manifest 推断。"""
    if conversational is None:
        conversational = bool(bundle.manifest.get("conversational"))
    docs: list[dict[str, Any]] = []
    raw = bundle.files["preference_dpo.jsonl"].decode("utf-8")
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise LlmExportError(f"preference_dpo.jsonl 第 {lineno} 行为空")
        doc = json.loads(line)
        if set(doc) != {"prompt", "chosen", "rejected"}:
            raise LlmExportError(
                f"第 {lineno} 行必须恰含 prompt/chosen/rejected，收到 {sorted(doc)}"
            )
        if conversational:
            _validate_messages(doc["prompt"], f"第 {lineno} 行 prompt")
            _validate_messages(doc["chosen"], f"第 {lineno} 行 chosen")
            _validate_messages(doc["rejected"], f"第 {lineno} 行 rejected")
        else:
            for key in ("prompt", "chosen", "rejected"):
                value = doc[key]
                if isinstance(value, list):
                    _validate_messages(value, f"第 {lineno} 行 {key}")
                elif not isinstance(value, str) or not value.strip():
                    raise LlmExportError(f"第 {lineno} 行 {key} 不能为空")
        docs.append(doc)
    return docs


def verify_manifest(bundle: ExportBundle) -> None:
    """manifest 中每个文件哈希必须与实际字节一致。"""
    import hashlib

    for name, digest in bundle.manifest["files"].items():
        actual = "sha256:" + hashlib.sha256(bundle.files[name]).hexdigest()
        if actual != digest:
            raise LlmExportError(f"manifest 哈希不符: {name}")


def check_provenance_alignment(bundle: ExportBundle, rows: int) -> None:
    """sidecar 行数必须与主文件行数一致（按行号对齐）。"""
    raw = bundle.files["provenance.jsonl"].decode("utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) != rows:
        raise LlmExportError(f"provenance 行数 {len(lines)} 与主文件 {rows} 行不对齐")
    for idx, line in enumerate(lines):
        doc = json.loads(line)
        if doc.get("row") != idx:
            raise LlmExportError(f"provenance 第 {idx} 行 row 字段错位")


def assert_no_pii(bundle: ExportBundle, *, forbidden: Sequence[str] = ()) -> None:
    """provenance sidecar 不得包含调用方声明的禁用原始标识。"""
    raw = bundle.files["provenance.jsonl"].decode("utf-8")
    for token in forbidden:
        if token and token in raw:
            raise LlmExportError(f"provenance 泄漏禁用标识 {token!r}")


__all__ = [
    "assert_no_pii",
    "check_provenance_alignment",
    "parse_dpo_bundle",
    "parse_sft_bundle",
    "verify_manifest",
]
