"""Canonical JSON 与 manifest hash（确定性：同输入同 hash）。

规则：UTF-8、键排序、无空白、``allow_nan=False``；浮点在校验阶段已被拒绝。
Hash 覆盖「去掉 manifest_hash 字段」后的 canonical 字节。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

HASH_FIELD = "manifest_hash"


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def compute_manifest_hash(document: Mapping[str, Any]) -> str:
    body = {k: v for k, v in document.items() if k != HASH_FIELD}
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()
