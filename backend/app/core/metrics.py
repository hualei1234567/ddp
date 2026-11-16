"""Prometheus 指标：注册表、指标定义与标签归一。

本模块**不含 HTTP 适配**：中间件在 ``app/api/middleware/metrics.py``，端点在
``app/api/observability.py``。这里只做「指标是什么」与「标签怎么归一」。

## 为什么用独立的 ``CollectorRegistry`` 而不是默认全局注册表

``prometheus_client`` 默认把指标注册到进程级 ``REGISTRY``，重复注册同名指标会抛
``ValueError``。但 ``create_app()`` 在测试里会被反复调用（每个用例自建应用），
注册到全局表就会在第二次调用时炸掉。这里为每个应用实例建一份注册表，指标随
应用实例生命周期存在：单进程单应用（真实部署）语义完全相同，测试之间天然隔离。

进程级指标（``process_cpu_seconds_total`` 等）通过 ``ProcessCollector`` 显式
注册进这份私有注册表，保留「CPU/内存/文件描述符」这类排障必需信号。

## 高基数防护（这是本模块最重要的约束）

Prometheus 的代价模型是「每个标签组合一个时间序列」。标签一旦带用户输入，内存
随请求量线性增长，最终把进程打爆。本模块的三道闸：

1. **路径只用路由模板**：标签取自 ``scope["route"].path``，即
   ``/api/v1/projects/{project_id}/devices``，而不是原始 URL。Starlette 在
   路由匹配成功后把命中的 Route 写回 scope（``scope.update(child_scope)``），
   因此外层中间件在响应返回后能读到模板。UUID、编码、查询串全部不进标签。
2. **未匹配路由折叠为常量**：404 的路径完全由客户端控制（可以无限随机），
   一律记为 ``__unmatched__``，绝不记原始 path。被限流短路（未进入路由）的请求
   同样落在这里：宁可在该桶里看到若干 429，也不为它们记录原始路径。
   带尾斜杠的重定向（``/health/`` → 307）也在该桶：Starlette 的重定向分支用
   ``dict(scope)`` 的副本重新匹配，不回写原 scope，因此模板拿不到。该桶的
   语义是"没能归到某个路由模板"而非"404"，按 404 率做告警时要用
   ``status="404"`` 精确过滤。
3. **模板数量硬上限**：即便将来出现挂载子应用、动态注册路由等异常情况，模板数
   超过 :data:`MAX_TRACKED_ROUTES` 后统一折叠为 ``__other__``。

HTTP 方法同样受限：非标准方法（客户端可随意构造）折叠为 ``__other__``。

直方图只带 ``method`` + ``path``，不带 ``status``：状态码维度已由计数器承担，
两者相乘会让序列数再翻数倍，而按状态分桶的延迟曲线在实践中很少被真正使用。

## 业务指标为什么在采集时查库（pull），而不是主动更新

业务量（导入任务、Episode 的状态分布）是**数据库状态的投影**，不是应用自己
维护的计数器。若改成「每次状态迁移 ``inc()``」，就必须在所有写入点（用例、
Worker、运维脚本、直接改库的修数流程）都记得更新同一个进程内变量：漏一处即
长期静默偏差，多副本部署时每份进程还各记一份、互相矛盾。采集时 ``GROUP BY``
则始终与库一致，只有一个事实来源。

代价是每次抓取（默认 15s 一次）产生两条 ``GROUP BY`` 查询，两者都走
``(project_id, status)`` 类索引的前缀；数据量再大一个量级应先在指标侧改用预聚合
表，而不是改回主动更新。查询失败时**不输出**业务指标样本（不是输出 0），
并记 warning：缺失比假零值更诚实。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from app.domain.episodes.enums import EpisodeStatus
from app.domain.ingestion.enums import IngestionJobStatus
from prometheus_client import CollectorRegistry, Counter, Histogram, ProcessCollector
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

logger = logging.getLogger("ddp.metrics")

HTTP_REQUESTS_TOTAL = "ddp_http_requests_total"
HTTP_REQUEST_DURATION_SECONDS = "ddp_http_request_duration_seconds"
INGESTION_JOBS_GAUGE = "ddp_ingestion_jobs"
EPISODES_GAUGE = "ddp_episodes"

# 未匹配到任何路由（404/被短路）：路径由客户端完全控制，只能折叠成常量。
UNMATCHED_ROUTE = "__unmatched__"
# 模板数超过上限后的兜底标签。
OTHER_ROUTE = "__other__"
# 模板数硬上限。**这是兜底，不是容量规划**：正常情况下的模板数等于应用注册的
# 路由数，由代码静态决定，客户端无法影响。当前应用约 200 个 APIRoute 模板
# （其中 /health、/metrics 豁免），即 ~200 个可计数模板，距原值 200 只剩 0-1 个槽位
# ——任何新增端点都会让后续路由被静默折叠进 ``__other__``，指标无声失真。
# 取 1000 留出 >5 倍增长余量；真正需要防的是"挂载子应用/动态注册路由"这类
# 异常放大，那时的量级远不止几百。``tests/api/test_metrics_api.py`` 有回归护栏，
# 断言真实应用的可计数模板数明显低于本上限。
MAX_TRACKED_ROUTES = 1000

# 允许方法：标准 HTTP 方法集合之外一律折叠，防止客户端用随机方法名撑爆标签。
_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
OTHER_METHOD = "__other__"

# FastAPI 内置文档路由。它们是 **Starlette Route 而非 APIRoute**，不往
# ``scope["route"]`` 写值，因此中间件只能看到 ``__unmatched__``。若不显式豁免，
# 每次打开 API 文档页都会以 status=200 落进「未匹配」桶，让这个桶同时混着
# 「客户端乱猜的 404」和「服务端固定路径的 200」，404 率从此无法用于告警。
# 取值必须与 ``app/main.py`` 里 ``FastAPI(docs_url=..., redoc_url=...,
# openapi_url=...)`` 一致；``/docs/oauth2-redirect`` 是 Swagger UI 的固定伴生路径。
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"})

# 埋点豁免：抓取端点自身、健康检查与内置文档路由。计入抓取端点会产生
# 「抓取越勤、数字越大」的自指循环；文档路由与业务流量无关且路径固定，
# 没有观测价值（见 ``DOCS_PATHS`` 的说明）。
EXEMPT_PATHS = frozenset({"/health", "/metrics"}) | DOCS_PATHS


@dataclass(frozen=True)
class BusinessGaugeSpec:
    """一个业务状态分布指标：按 ``status`` 一个标签分族。"""

    name: str
    help_text: str
    statuses: tuple[str, ...]


def business_gauge_specs() -> tuple[BusinessGaugeSpec, ...]:
    """业务指标定义。状态取值直接来自领域枚举，不在此重复维护字符串。"""
    return (
        BusinessGaugeSpec(
            name=INGESTION_JOBS_GAUGE,
            help_text="按状态的接入任务数量（采集时查库）",
            statuses=tuple(status.value for status in IngestionJobStatus),
        ),
        BusinessGaugeSpec(
            name=EPISODES_GAUGE,
            help_text="按状态的 Episode 数量（采集时查库）",
            statuses=tuple(status.value for status in EpisodeStatus),
        ),
    )


class BusinessStateCollector(Collector):
    """采集时才查询数据库的业务状态分布。

    ``counts_provider`` 返回 ``{指标名: {状态值: 数量}}``；缺少的状态按 0 输出，
    这样仪表盘在某个状态从「有」变「无」时不会断线。
    """

    def __init__(
        self,
        counts_provider: Callable[[], Mapping[str, Mapping[str, int]]],
        specs: Iterable[BusinessGaugeSpec],
    ) -> None:
        self._counts_provider = counts_provider
        self._specs = tuple(specs)

    def collect(self) -> Iterable[GaugeMetricFamily]:
        try:
            counts = self._counts_provider()
        except Exception:
            # /metrics 不能因为数据库抖动而整体失败：HTTP 指标仍要可抓取。
            # 这里不是吞异常——原文与堆栈落日志，且业务样本整体缺失（不输出假 0）。
            logger.warning("业务指标采集失败，本次不输出业务指标样本", exc_info=True)
            return
        for spec in self._specs:
            per_status = counts.get(spec.name, {})
            family = GaugeMetricFamily(spec.name, spec.help_text, labels=("status",))
            for status in spec.statuses:
                family.add_metric([status], float(per_status.get(status, 0)))
            yield family


class Metrics:
    """一份应用实例级的指标注册表与埋点入口。"""

    def __init__(self, *, max_tracked_routes: int = MAX_TRACKED_ROUTES) -> None:
        self.registry = CollectorRegistry()
        self._max_tracked_routes = max_tracked_routes
        self._tracked_routes: set[str] = set()
        self._lock = threading.Lock()
        # 进程级指标（CPU、RSS、启动时间、文件描述符）：排障必需的基线信号。
        # 仅 Linux 有效——prometheus_client 从 ``/proc/<pid>`` 读取，非 Linux 平台
        # （含开发机 macOS）静默返回空集合。生产部署在 Linux 容器内，此处保留。
        ProcessCollector(registry=self.registry)
        self.http_requests = Counter(
            HTTP_REQUESTS_TOTAL,
            "HTTP 请求总数（path 为路由模板，非原始 URL）",
            ("method", "path", "status"),
            registry=self.registry,
        )
        self.http_request_duration = Histogram(
            HTTP_REQUEST_DURATION_SECONDS,
            "HTTP 请求耗时（秒，path 为路由模板）",
            ("method", "path"),
            registry=self.registry,
        )

    def route_label_from_scope(self, scope: dict[str, object]) -> str:
        """把请求归一成有界的路由模板标签（入参是 ASGI scope）。"""
        route = scope.get("route")
        template = getattr(route, "path", None)
        return self.route_label(template if isinstance(template, str) else None)

    def route_label(self, template: str | None) -> str:
        if not template:
            return UNMATCHED_ROUTE
        with self._lock:
            if template in self._tracked_routes:
                return template
            if len(self._tracked_routes) >= self._max_tracked_routes:
                return OTHER_ROUTE
            self._tracked_routes.add(template)
        return template

    def method_label(self, method: str) -> str:
        return method if method in _ALLOWED_METHODS else OTHER_METHOD

    def observe_request(
        self,
        *,
        method: str,
        route: str,
        status: int,
        duration_seconds: float,
    ) -> None:
        self.http_requests.labels(method=method, path=route, status=str(status)).inc()
        self.http_request_duration.labels(method=method, path=route).observe(duration_seconds)


def build_metrics(
    *,
    counts_provider: Callable[[], Mapping[str, Mapping[str, int]]] | None = None,
) -> Metrics:
    """装配指标注册表；给了 ``counts_provider`` 才注册业务采集器。"""
    metrics = Metrics()
    if counts_provider is not None:
        metrics.registry.register(BusinessStateCollector(counts_provider, business_gauge_specs()))
    return metrics
