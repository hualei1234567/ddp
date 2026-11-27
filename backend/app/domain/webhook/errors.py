"""Webhook 领域错误。"""

from __future__ import annotations

from app.domain.annotation.errors import DomainValidationError


class WebhookNotConfiguredError(DomainValidationError):
    """平台未配置 Webhook 出站所需的部署参数（加密密钥）。

    这是**部署配置问题**而非业务校验失败——调用方无法通过改请求参数解决，
    因此单独成类，由 API 层映射为 503（服务暂不可用）而不是 422/500：
    报 500 会把"运维要配环境变量"伪装成"平台内部故障"。
    """

    code = "service_unavailable"
