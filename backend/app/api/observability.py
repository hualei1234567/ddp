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

## 暴露面

内容只有：HTTP 路由模板与状态码计数/耗时、按状态聚合的接入任务与 Episode 数量、
进程 CPU/内存/FD。**不含**任何项目 ID、文件名、URL 参数或业务实体。路由模板本身
也已在``/openapi.json``（未鉴权）可见，故此处不新增信息暴露。生产部署仍应通过
反向代理限制来源网段，与其它运维端点同样的做法。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.metrics import Metrics

router = APIRouter(tags=["platform"])


@router.get("/metrics", include_in_schema=False)
def metrics_endpoint(request: Request) -> Response:
    metrics: Metrics = request.app.state.metrics
    return Response(
        content=generate_latest(metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
