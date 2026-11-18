"""跨域共享的日志脱敏唯一实现。

步骤日志/失败摘要既会落库，也会通过只读端点回给前端，因此**不得携带**
密钥、预签名 URL、绝对路径或对象存储地址。异常文本（``str(exc)``）不受
我们控制，可能带上游 URL 或本机路径，所以统一在写入前与返回前各脱敏一次
（纵深防御）。

为什么从 :mod:`app.application.datasources.observability` 上移到这里：
N4-002 的处理运行阶段日志需要同一套规则，但处理运行域**不应 import
数据源域**（跨域 import 内部实现）。规则只保留这一份，数据源域改为薄适配。

这里只做"把已知敏感形态替换成占位符"，不尝试识别所有秘密；真正的秘密管理
仍是"不写进日志"——本模块不接收任何密钥值，只处理文本。
"""

from __future__ import annotations

import re

DEFAULT_LOG_MESSAGE_MAX_LENGTH = 1000

_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
# 预签名/凭证查询参数；URL 整体被替换后通常已不存在，单独处理防漏网。
_CREDENTIAL_QUERY_PATTERN = re.compile(
    r"(?i)\b(X-Amz-[A-Za-z]+|Signature|Credential|access_token|sig)=[^&\s]+"
)
# 绝对路径：POSIX（以 / 开头）与 Windows（C:\ 开头）。前置字符不能是字母
# 数字或点，避免把 "3/10"、"and/or" 这类普通文本误伤。
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w.~])(?:/(?=[^\s\"'<>])[^\s\"'<>]*|[A-Za-z]:\\[^\s\"'<>]*)"
)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:authorization|api[_-]?key|secret|token)\b\s*[:=]\s*"
    r"(?:bearer\s+)?[^\s,;]+"
    r"|\bbearer\s+[^\s,;]+"
)
# 键名判定：``\b`` 在 ``_`` 两侧不成立（下划线是词字符），所以 ``hf_token``
# 用 ``\btoken\b`` 匹配不到。这里显式把 ``_``/``-`` 当作分隔符，同时用
# 字母数字邻接排除 ``tokenizer`` 这类普通单词。与 ``_SECRET_PATTERN`` 互补：
# 后者脱敏 ``key=value`` 文本，本规则判断"这个键名是否表示凭据"。
_SECRET_NAME_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|credential|private[_-]?key)"
    r"(?:$|[^a-z0-9])"
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def is_secret_name(name: object) -> bool:
    """键名/字段名是否表示凭据类信息（token、secret、password、api_key 等）。

    ``hf_token`` 命中，``tokenizer`` 不命中。只做名字判断，不读取也不推断值。
    """
    return bool(_SECRET_NAME_PATTERN.search(str(name or "")))


def sanitize_log_message(
    message: object,
    *,
    limit: int = DEFAULT_LOG_MESSAGE_MAX_LENGTH,
) -> str:
    """把任意异常/日志文本压成可安全外发的一行。

    - 协议地址（http/https/s3/hf 等）→ ``[已脱敏地址]``；
    - 绝对路径（``/...``、``C:\\...``）→ ``[已脱敏路径]``；
    - 凭据形态（``token=...``、``Bearer ...``）→ ``[已脱敏凭据]``；
    - 所有空白折叠为单个空格，并按列宽截断。

    永不返回空串：脱敏后为空时返回 ``"(无更多信息)"``，避免前端出现空白行。
    """
    text = _WHITESPACE_PATTERN.sub(" ", str(message or "")).strip()
    text = _URL_PATTERN.sub("[已脱敏地址]", text)
    text = _CREDENTIAL_QUERY_PATTERN.sub("[已脱敏凭据]", text)
    text = _ABSOLUTE_PATH_PATTERN.sub("[已脱敏路径]", text)
    text = _SECRET_PATTERN.sub("[已脱敏凭据]", text)
    if not text:
        return "(无更多信息)"
    return text[:limit]


__all__ = [
    "DEFAULT_LOG_MESSAGE_MAX_LENGTH",
    "is_secret_name",
    "sanitize_log_message",
]
