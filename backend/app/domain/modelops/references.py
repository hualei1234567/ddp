"""推理结果引用校验。

契约 `inference_task_result_invalid`(422)：「推理结果引用非法或 Hash 不匹配」，
验收标准 AC-IN-02：「结果未知不得记为成功」。

契约把 `result_ref` 声明为可空开放对象（`additionalProperties: true`），因此这里
**不新增必填字段**：只做「已给出的键必须可用」的校验，避免实现比契约更严。
`sha256` 与对象存储实际内容的一致性校验不在 DDP 侧执行（DDP 不下载推理结果对象）。
"""

from __future__ import annotations

import re

from app.domain.modelops.errors import InferenceTaskResultInvalidError

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_OBJECT_KEY = 1024


def validate_result_reference(value: object) -> dict[str, object]:
    """成功回调的结果引用必须能定位到具体对象。

    - 缺失、`null` 或空对象：结果不可定位 → 拒绝（不得记 SUCCEEDED）；
    - `object_key` 存在但为空或超长 → 拒绝；
    - `sha256` 存在但不符合 `sha256:<64 位小写十六进制>` → 拒绝。
    """
    if value is None or value == {}:
        raise InferenceTaskResultInvalidError("推理成功回调必须提供结果引用 result_ref")
    if not isinstance(value, dict):
        raise InferenceTaskResultInvalidError("result_ref 必须是对象")
    object_key = value.get("object_key")
    if object_key is not None:
        if not isinstance(object_key, str) or object_key.strip() == "":
            raise InferenceTaskResultInvalidError("result_ref.object_key 必须是非空字符串")
        if len(object_key) > _MAX_OBJECT_KEY:
            raise InferenceTaskResultInvalidError(
                f"result_ref.object_key 超过最大长度 {_MAX_OBJECT_KEY}"
            )
    sha256 = value.get("sha256")
    if sha256 is not None:
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise InferenceTaskResultInvalidError(
                "result_ref.sha256 必须是 sha256:<64 位小写十六进制>"
            )
    return dict(value)


__all__ = ["validate_result_reference"]
