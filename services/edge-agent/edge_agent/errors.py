"""Edge Agent 统一异常层级。

原则：状态非法 / 完整性破坏 / 不受支持的操作一律显式抛出，不返回“默认成功”。
"""

from __future__ import annotations


class EdgeAgentError(Exception):
    """所有 Edge Agent 异常的基类。"""


class ConfigError(EdgeAgentError):
    """配置缺失或非法。"""


class IllegalTransitionError(EdgeAgentError):
    """Spool 状态机收到非法转移。"""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"Spool 状态转移非法：{from_state} -> {to_state}")
        self.from_state = from_state
        self.to_state = to_state


class StateConflictError(EdgeAgentError):
    """并发修改导致期望状态与库内状态不一致。"""


class SpoolEntryNotFoundError(EdgeAgentError):
    """Spool 条目不存在。"""


class SpoolIngestError(EdgeAgentError):
    """入 Spool 失败（源文件缺失、写入期间被修改、空文件等）。"""


class SpoolIntegrityError(EdgeAgentError):
    """Spool 落盘数据与记录的 Hash/大小不一致，或目录布局被破坏。"""


class PurgeNotAllowedError(EdgeAgentError):
    """未满足显式配置与服务端回执，禁止删除 Spool 内的原始副本。"""


class ManifestError(EdgeAgentError):
    """Manifest 字段非法、含禁止字段（凭证 / 自报 verified）或校验失败。"""


class UnsupportedOperationError(EdgeAgentError):
    """Adapter 明确不支持的能力（例如文件导出 Adapter 的 start/stop）。"""


class PreflightBlockedError(EdgeAgentError):
    """Preflight 存在 BLOCKED 项，任何“继续”都不能绕过。"""

    def __init__(self, blocked_check_ids: tuple[str, ...]) -> None:
        super().__init__(f"Preflight 被阻断，不可继续：{', '.join(blocked_check_ids)}")
        self.blocked_check_ids = blocked_check_ids


class PreflightAckError(EdgeAgentError):
    """存在 WARN 但确认不完整，或任务策略不允许带 WARN 继续。"""


class GatewayError(EdgeAgentError):
    """与服务端交互的错误基类（由 ServerGateway / PartUploader 实现抛出）。"""


class GatewayTransientError(GatewayError):
    """可重试：断网、超时、5xx。"""


class SignedUrlExpiredError(GatewayError):
    """签名 URL 已过期：必须重新签名，不得重建 UploadSession。"""


class UploadSessionGoneError(GatewayError):
    """服务端 UploadSession 已过期/中止/不存在：只能重建 Session。"""


class AuthRejectedError(GatewayError):
    """Agent 身份被服务端拒绝（token 失效/被撤销/配对过期）：不可重试，需重新配对。"""


class GatewayPermanentError(GatewayError):
    """不可重试的服务端拒绝（校验失败、权限、状态冲突等）。"""


class ChecksumMismatchError(GatewayPermanentError):
    """Hash / ETag 与期望不一致（服务端 409 或本地回执校验失败）：必须隔离。"""
