"""模型闭环领域规范化与 Hash；算法委托给唯一实现。

保留本模块作为领域入口（``app.domain.modelops`` 对外导出这两个名字）。
G-15：此前这里独立实现了一份 json.dumps 规范化，与 datasets 那份并存且都不符合
RFC 8785；现在两处都指向 ``app.domain.common.canonical_json``。
"""

from __future__ import annotations

from app.domain.common.canonical_json import canonical_json, sha256_value

__all__ = ["canonical_json", "sha256_value"]
