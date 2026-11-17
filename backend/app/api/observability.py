"""运维指标端点。

``GET /metrics`` 输出 Prometheus 文本格式，供抓取端（Prometheus / VictoriaMetrics /
Grafana Agent 等）拉取。

## 为什么不在 ``/api/v1`` 前缀下、也不进 OpenAPI

- 这是**运维接口**而非业务契约：抓取端约定俗成的路径是根级 ``/metrics``，
  放进版本前缀会让抓取配置随 API 版本迁移而失效；
- 因此 ``include_in_schema=False``：它不出现在 ``/openapi.json``，也就不参与
  ``scripts/check_contract_parity.py`` 的契约一致性比对。**契约缺口已知**：
  ``packages/contracts`` 未声明该端点，本次不修改契约（契约变更需 System Architect
  评审），留待后续 ADR 决定是否正式纳入契约。

## 暴露面与访问控制

内容只有：HTTP 路由模板与状态码计数/耗时、按状态聚合的接入任务与 Episode 数量、
进程 CPU/内存/FD。**不含**任何项目 ID、文件名、URL 参数或业务实体。

即便如此，路由模板清单与流量规模（哪个端点被调多少次、错误率多少）对攻击者
是做侦察的有用信息，因此本端点**默认需要认证**：

- 抓取端是机器、无用户会话，不能走用户 JWT（会作废所有现有抓取配置），
  改用静态 Bearer 令牌 ``DDP_METRICS_ACCESS_TOKEN``；
- 可叠加 ``DDP_METRICS_ALLOWED_SOURCE_NETWORKS`` 来源网段白名单做纵深防御；
- 判定逻辑（失败关闭口径、恒定时间比较、不读转发头）在
  ``app/core/metrics_access.py``；本模块只做 HTTP 适配。

拒绝时抛出 :class:`AppError` 子类，由 ``app/main.py`` 的全局处理器输出统一的
``{"error": {...}}`` 结构（含 ``request_id``）。**不自定义响应体**：两套错误
格式是排查时最先咬人的那类不一致。

## 为什么 ``/health`` 保持公开

它不含路由清单、流量或任何配置，唯一负载是 ``{"status": "ok", ...}``；而容器
healthcheck（``docker-compose.yml``）、负载均衡探活与就绪门禁都依赖它能无条件
访问。加认证会让"服务不健康"与"探针没带令牌"变成同一个现象，探活本身失去意义。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import extract_bearer_token
from app.api.v1.upload_dependencies import SettingsDep
from app.core.errors import AppError, AuthenticationError
from app.core.metrics import Metrics
from app.core.metrics_access import (
    MetricsAccessOutcome,
    build_metrics_access_policy,
    decide_metrics_access,
)

logger = logging.getLogger("ddp.metrics")

router = APIRouter(tags=["platform"])


def _not_configured_error() -> AppError:
    """非本地环境未配置令牌：503 而不是 401。

    这是**服务端**配置缺失，不是客户端凭证有问题。返回 401 会让运维反复
    更换令牌；503 让抓取端以 ``up == 0`` 报错，日志里同时有明确指向。
    """
    return AppError(
        "metrics_not_configured",
        "指标抓取端点未配置访问凭证，已拒绝访问",
        503,
    )


def _unauthorized_error() -> AuthenticationError:
    """来源被拒与令牌错误对外必须**不可区分**。

    同一状态码 + 同一文案，否则远端可借差异判断"自己是否在允许网段内"
    或"令牌是否猜对"。
    """
    return AuthenticationError("未授权访问该端点")


@router.get("/metrics", include_in_schema=False)
def metrics_endpoint(request: Request, settings: SettingsDep) -> Response:
    policy = build_metrics_access_policy(
        token=settings.metrics_access_token,
        allowed_source_networks=settings.metrics_allowed_source_networks,
        is_local=settings.is_local,
    )
    client = request.client
    outcome = decide_metrics_access(
        policy,
        presented_token=extract_bearer_token(request),
        client_host=client.host if client is not None else None,
    )

    if outcome is MetricsAccessOutcome.NOT_CONFIGURED:
        # 5xx 已由全局处理器落 error 日志（含 path/request_id），此处不重复。
        raise _not_configured_error()
    if outcome is MetricsAccessOutcome.SOURCE_NOT_ALLOWED:
        # 4xx 全局处理器不落日志，而"来源越界"是值得追查的安全信号。
        logger.warning("GET /metrics 被拒：来源不在 DDP_METRICS_ALLOWED_SOURCE_NETWORKS 内")
        raise _unauthorized_error()
    if outcome is MetricsAccessOutcome.UNAUTHENTICATED:
        # 不区分"没带"与"带错"，也不回显任何令牌片段。
        logger.warning("GET /metrics 被拒：缺少或错误的 Bearer 令牌")
        raise _unauthorized_error()

    metrics: Metrics = request.app.state.metrics
    return Response(
        content=generate_latest(metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
