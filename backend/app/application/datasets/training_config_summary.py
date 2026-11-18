"""训练配置摘要：落库前的有界裁剪与脱敏。

**为什么需要这一层**

请求里的 ``config`` 是训练平台参数（超参、数据配比等），其权威副本在外部训练
平台。DDP 只保留一份"给人和审计看的摘要"，因此必须满足两个硬约束：

- **有界**：不能把不受控的大对象写进引用行，否则一条配置就能拖垮列表响应与
  备份；上限与领域层 ``CONFIG_SUMMARY_MAX_BYTES`` 同源（8KiB）。
- **脱敏**：配置里可能混入 token、密钥或本机绝对路径。这些内容既不能入库，也
  不能出现在 API 响应里。

**脱敏规则不在这里另写一份**

字符串值复用 :func:`app.application.observability_redaction.sanitize_log_message`
（URL、绝对路径、``token=...``/``Bearer ...`` 形态），键名复用同一模块的
:func:`is_secret_name`（``hf_token`` 命中、``tokenizer`` 不命中）。两处共用同一
套规则，避免配置摘要与日志脱敏出现两套口径。

**落在应用层而不是领域层的原因**：脱敏规则属于"对外暴露前的处理"，领域层只
校验摘要的结构与大小（``optional_payload``），不 import 应用层模块。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.application.observability_redaction import is_secret_name, sanitize_log_message
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.training_references import CONFIG_SUMMARY_MAX_BYTES

# 嵌套深度上限：配置是扁平的参数表，超过 4 层说明调用方在传递不受控对象。
CONFIG_SUMMARY_MAX_DEPTH = 4
_KEY_MAX_LENGTH = 64
# 键名本身表示凭据时的值占位符；不读取、不推断原值。
REDACTED_VALUE = "[已脱敏]"


def summarize_training_config(config: Mapping[str, object] | None) -> dict[str, object]:
    """把请求配置压成有界、脱敏的 JSON 对象。

    ``None`` 与 ``{}`` 都返回 ``{}``：新提交一定会写入摘要，"提交时没有配置"
    是一个确定事实；``None`` 只留给迁移 0063 之前的存量记录。
    """
    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise DomainValidationError("config 必须是对象")
    summary = _clean_mapping(config, depth=1)
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > CONFIG_SUMMARY_MAX_BYTES:
        raise DomainValidationError(f"config 摘要超过 {CONFIG_SUMMARY_MAX_BYTES} 字节上限")
    return summary


def _clean_mapping(value: Mapping[object, object], *, depth: int) -> dict[str, object]:
    _ensure_depth(depth)
    cleaned: dict[str, object] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise DomainValidationError("config 的键必须是非空字符串")
        key = sanitize_log_message(raw_key.strip(), limit=_KEY_MAX_LENGTH)
        if is_secret_name(raw_key):
            cleaned[key] = REDACTED_VALUE
            continue
        cleaned[key] = _clean_value(item, depth=depth)
    return cleaned


def _clean_value(value: object, *, depth: int) -> object:
    _ensure_depth(depth)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, Mapping):
        return _clean_mapping(value, depth=depth + 1)
    if isinstance(value, list | tuple):
        _ensure_depth(depth + 1)
        return [_clean_value(item, depth=depth + 1) for item in value]
    raise DomainValidationError(f"config 摘要只支持 JSON 值，收到 {type(value).__name__}")


def _clean_text(value: str) -> str:
    if not value:
        # 空串是合法的"未设置"，不能被脱敏占位符替换。
        return ""
    # limit 传原文长度：这里只做替换，不顺便截断——大小由总字节上限控制。
    return sanitize_log_message(value, limit=max(len(value), 1))


def _ensure_depth(depth: int) -> None:
    if depth > CONFIG_SUMMARY_MAX_DEPTH:
        raise DomainValidationError(f"config 摘要嵌套超过 {CONFIG_SUMMARY_MAX_DEPTH} 层")


__all__ = [
    "CONFIG_SUMMARY_MAX_DEPTH",
    "REDACTED_VALUE",
    "summarize_training_config",
]
