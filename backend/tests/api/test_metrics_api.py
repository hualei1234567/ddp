"""Prometheus 指标端点与埋点中间件测试。

覆盖五件事，其中第 3 条是这套设计的核心约束：

1. ``/metrics`` 可抓取且包含预期指标名；
2. 请求后对应路由的计数 +1；
3. **高基数防护**：不同 UUID 的同一路由必须归一到同一 path 模板，原始 URL
   不得出现在输出里；404 的任意路径折叠为 ``__unmatched__``；
4. ``/metrics`` 与 ``/health`` 自身不计入（否则抓取频率会污染数据）；
5. ``trace_id`` 贯通：响应头 ``X-Request-Id``、错误体 ``error.request_id``
   与（经真实 ``JsonFormatter`` 输出的）日志 ``trace_id`` 三者同值。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from app import main as main_module
from app.api.deps import get_current_principal
from app.core.logging import JsonFormatter
from app.core.metrics import (
    EPISODES_GAUGE,
    EXEMPT_PATHS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    MAX_TRACKED_ROUTES,
    Metrics,
    build_metrics,
)
from app.domain.identity.models import PlatformRole, Principal
from app.infrastructure.db.session import get_session
from app.main import create_app
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds

_DEVICES_TEMPLATE = "/api/v1/projects/{project_id}/devices"
_MISSING_ID = "00000000-0000-7000-8000-000000000000"


class _CollectingHandler(logging.Handler):
    """挂到具名 logger 上的采集器（``create_app()`` 会清空 root handlers）。

    **必须在 emit 时就格式化**：``trace_id`` 来自 contextvar，``RequestIdMiddleware``
    在请求结束时复位它，事后再格式化只能读到 ``None``。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.payloads: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


@pytest.fixture()
def metrics_app(
    monkeypatch: pytest.MonkeyPatch,
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> Iterator[tuple[TestClient, ApiIds]]:
    """真实应用 + 真实 SQLite：业务指标采集走测试库，不打真实 PostgreSQL。

    认证主体替换为项目 A 的编辑者：这样路由能真正走到用例层，才能验证
    「同一路由模板 + 不同 UUID → 一条时间序列」而不是被认证层挡住。
    """
    factory, ids = api_db
    # ``create_app`` 里业务采集器在调用时才解析该名字，替换即生效。
    monkeypatch.setattr(main_module, "get_session_factory", lambda: factory)
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=ids.editor_a,
        platform_role=PlatformRole.MEMBER,
    )
    yield TestClient(app), ids


@pytest.fixture()
def metrics_client(metrics_app: tuple[TestClient, ApiIds]) -> TestClient:
    return metrics_app[0]


def _metric_value(body: str, line_prefix: str) -> float:
    """取某条样本行的值；样本尚未出现时返回 0.0（计数器语义上的初始值）。"""
    for line in body.splitlines():
        if line.startswith(line_prefix):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def _counter(body: str, *, method: str, path: str, status: int) -> float:
    return _metric_value(
        body,
        f'{HTTP_REQUESTS_TOTAL}{{method="{method}",path="{path}",status="{status}"}}',
    )


def test_metrics_endpoint_exposes_expected_metrics(
    metrics_client: TestClient,
) -> None:
    response = metrics_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert f"# TYPE {HTTP_REQUESTS_TOTAL} counter" in body
    assert f"# TYPE {HTTP_REQUEST_DURATION_SECONDS} histogram" in body
    # 业务指标：两个状态分布族都在，状态标签来自领域枚举（不是手写字符串）。
    assert "# TYPE ddp_ingestion_jobs gauge" in body
    assert "# TYPE ddp_episodes gauge" in body
    assert 'ddp_episodes{status="PENDING"} 0.0' in body


def test_request_increments_route_counter(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    client, ids = metrics_app
    url = f"/api/v1/projects/{ids.project_a}/devices"
    before = _counter(client.get("/metrics").text, method="GET", path=_DEVICES_TEMPLATE, status=200)

    assert client.get(url).status_code == 200

    after = _counter(client.get("/metrics").text, method="GET", path=_DEVICES_TEMPLATE, status=200)
    assert after == before + 1


def test_different_uuids_are_normalized_to_one_route_template(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """高基数防护：N 个不同 UUID 只产生 1 条时间序列，原始 URL 不落标签。"""
    client, _ = metrics_app
    first, second = uuid4(), uuid4()
    before = _counter(client.get("/metrics").text, method="GET", path=_DEVICES_TEMPLATE, status=403)

    # 两个不存在的项目：都走同一路由，都因无成员资格被拒绝。
    assert client.get(f"/api/v1/projects/{first}/devices").status_code == 403
    assert client.get(f"/api/v1/projects/{second}/devices").status_code == 403

    body = client.get("/metrics").text
    after = _counter(body, method="GET", path=_DEVICES_TEMPLATE, status=403)
    assert after == before + 2
    # 任何形如原始 URL 的标签都不得出现。
    assert str(first) not in body
    assert str(second) not in body


def test_unmatched_paths_collapse_to_constant_label(
    metrics_client: TestClient,
) -> None:
    """404 路径完全由客户端控制，只能折叠成常量，否则可被用来撑爆内存。"""
    before = _counter(
        metrics_client.get("/metrics").text,
        method="GET",
        path="__unmatched__",
        status=404,
    )
    first = f"/api/v1/not-a-route-{uuid4()}"
    second = f"/api/v1/not-a-route-{uuid4()}"

    assert metrics_client.get(first).status_code == 404
    assert metrics_client.get(second).status_code == 404

    body = metrics_client.get("/metrics").text
    after = _counter(body, method="GET", path="__unmatched__", status=404)
    assert after == before + 2
    assert first not in body
    assert second not in body


def test_metrics_and_health_are_not_counted(
    metrics_client: TestClient,
) -> None:
    """抓取端点与健康检查不埋点：否则「抓得越勤、数字越大」。"""
    metrics_client.get("/metrics")
    metrics_client.get("/health")
    body = metrics_client.get("/metrics").text

    assert 'path="/metrics"' not in body
    assert 'path="/health"' not in body


def test_docs_routes_do_not_pollute_unmatched_bucket(
    metrics_client: TestClient,
) -> None:
    """内置文档路由是 Starlette Route（不写 ``scope["route"]``），必须显式豁免。

    否则打开一次 Swagger UI 就以 status=200 落进 ``__unmatched__``，该桶同时混着
    「客户端乱猜的 404」与「服务端固定路径的 200」，404 率再也无法用于告警。
    """
    before = _counter(
        metrics_client.get("/metrics").text,
        method="GET",
        path="__unmatched__",
        status=200,
    )
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert metrics_client.get(path).status_code == 200

    body = metrics_client.get("/metrics").text
    after = _counter(body, method="GET", path="__unmatched__", status=200)
    assert after == before
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert f'path="{path}"' not in body


def test_metric_labels_never_carry_identity(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """``/metrics`` 是未鉴权端点：标签必须只有路由模板、方法、状态码与状态枚举。

    这条用**白名单**而不是黑名单断言：漏掉某个新标签（例如有人顺手加上
    ``project_id`` 便于分项目看板）就在白名单外，测试直接失败；而黑名单只能
    防住已经想到的那几个键。同时验证真实请求产生的 project_id / user_id 不出现在
    响应体里。
    """
    client, ids = metrics_app
    client.get(f"/api/v1/projects/{ids.project_a}/devices")
    body = client.get("/metrics").text

    allowed_labels = {"method", "path", "status", "status_code", "le", "quantile", "version"}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        if "{" not in line:
            continue
        label_part = line.split("{", 1)[1].split("}", 1)[0]
        for pair in label_part.split(","):
            name = pair.split("=", 1)[0].strip()
            assert name in allowed_labels, f"指标 {line!r} 出现白名单外标签 {name!r}"

    for secret in (str(ids.project_a), str(ids.editor_a), str(ids.owner_a)):
        assert secret not in body


def test_tracked_route_budget_has_headroom() -> None:
    """路由模板上限是兜底，不能紧贴真实应用的路由数。

    一旦等于或接近真实模板数，新增一个端点就会让后续路由被静默折叠进
    ``__other__``——指标看起来正常，数据已经错了，且没有任何报错。
    这里用真实 ``create_app()`` 的注册路由反算，断言至少还有 2 倍余量。
    """
    from fastapi.routing import APIRoute

    app = create_app()

    def countable(routes: object) -> set[str]:
        templates: set[str] = set()
        for route in routes:  # type: ignore[attr-defined]
            if isinstance(route, APIRoute):
                templates.add(route.path)
            nested = getattr(route, "routes", None)
            if nested:
                templates |= countable(nested)
        return templates

    templates = countable(app.routes) - EXEMPT_PATHS
    assert templates, "未能从真实应用读到任何路由模板，护栏本身失效"
    assert (
        len(templates) * 2 < MAX_TRACKED_ROUTES
    ), f"真实可计数路由模板 {len(templates)} 个，上限 {MAX_TRACKED_ROUTES} 余量不足 2 倍"


def test_route_budget_exhaustion_folds_into_other() -> None:
    """上限本身要生效：超出的新模板折叠为 ``__other__``，已跟踪的仍保留。"""
    metrics = Metrics(max_tracked_routes=2)
    assert metrics.route_label("/a") == "/a"
    assert metrics.route_label("/b") == "/b"
    assert metrics.route_label("/c") == "__other__"
    # 已跟踪的不受影响，只有新模板被折叠。
    assert metrics.route_label("/a") == "/a"


def test_business_collector_failure_emits_no_samples() -> None:
    """查库失败时业务指标整体缺失，而不是输出 0（假零值会掩盖故障）。"""

    def explode() -> dict[str, dict[str, int]]:
        raise RuntimeError("数据库不可用")

    metrics = build_metrics(counts_provider=explode)
    body = generate_latest(metrics.registry).decode()

    assert f"# TYPE {EPISODES_GAUGE} gauge" not in body
    # HTTP 指标仍可抓取：/metrics 不能因数据库抖动整体失败。
    assert f"# TYPE {HTTP_REQUESTS_TOTAL} counter" in body


def test_business_collector_zero_fills_missing_statuses() -> None:
    """某状态计数消失时要输出 0，否则仪表盘会断线（缺样本 ≠ 值为零）。"""
    from app.domain.episodes.enums import EpisodeStatus

    metrics = build_metrics(counts_provider=lambda: {EPISODES_GAUGE: {"READY": 3}})
    body = generate_latest(metrics.registry).decode()

    assert f'{EPISODES_GAUGE}{{status="READY"}} 3.0' in body
    assert f'{EPISODES_GAUGE}{{status="PENDING"}} 0.0' in body
    for status in EpisodeStatus:
        assert f'{EPISODES_GAUGE}{{status="{status.value}"}}' in body


def test_unknown_status_rows_are_dropped_not_coerced() -> None:
    """库里出现领域枚举之外的状态：不进入样本，也不被归入任何既有状态。"""
    metrics = build_metrics(
        counts_provider=lambda: {EPISODES_GAUGE: {"READY": 2, "MADE_UP_STATUS": 9}}
    )
    body = generate_latest(metrics.registry).decode()

    assert f'{EPISODES_GAUGE}{{status="READY"}} 2.0' in body
    assert "MADE_UP_STATUS" not in body
    # 未知状态的 9 不得被摊到任何已知状态上（伪造数据）。
    assert f'{EPISODES_GAUGE}{{status="PENDING"}} 0.0' in body


def test_trace_id_flows_through_logs_and_error_body(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """一次请求的 X-Request-Id = 日志 trace_id = 错误体 request_id。"""
    client, ids = metrics_app
    handler = _CollectingHandler()
    target = logging.getLogger("app.main")
    target.addHandler(handler)
    try:
        response = client.get(
            f"/api/v1/projects/{ids.project_a}/devices/{_MISSING_ID}",
            headers={"X-Request-Id": "trace-test-1"},
        )
    finally:
        target.removeHandler(handler)

    assert response.status_code == 404
    assert response.headers["X-Request-Id"] == "trace-test-1"
    assert response.json()["error"]["request_id"] == "trace-test-1"
    assert handler.payloads, "领域异常必须留日志"
    for payload in handler.payloads:
        assert payload["trace_id"] == "trace-test-1"


def test_trace_id_does_not_leak_to_next_request(
    metrics_app: tuple[TestClient, ApiIds],
) -> None:
    """contextvar 必须按请求复位，不能串到同一连接的下一个请求。

    用 ``token`` + ``reset`` 而非 ``set(None)``：后者在嵌套/异常退出时会丢掉
    外层值；这里验证第二次请求拿到的是**它自己**的 ID，而不是第一次的残留。
    """
    client, ids = metrics_app
    handler = _CollectingHandler()
    target = logging.getLogger("app.main")
    target.addHandler(handler)
    try:
        client.get(
            f"/api/v1/projects/{ids.project_a}/devices/{_MISSING_ID}",
            headers={"X-Request-Id": "trace-first"},
        )
        client.get(
            f"/api/v1/projects/{ids.project_a}/devices/{_MISSING_ID}",
            headers={"X-Request-Id": "trace-second"},
        )
    finally:
        target.removeHandler(handler)

    ids_in_logs = [payload["trace_id"] for payload in handler.payloads]
    assert "trace-first" in ids_in_logs
    assert "trace-second" in ids_in_logs
    # 第二次请求的日志里不得出现第一次的 ID（位置对应关系）。
    first_index = ids_in_logs.index("trace-first")
    assert all(item != "trace-first" for item in ids_in_logs[first_index + 1 :])


def test_trace_id_is_absent_outside_request_context() -> None:
    """请求外的日志（启动/Worker）不编造 trace_id，显式为 null。"""
    record = logging.LogRecord(
        name="ddp.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="启动完成",
        args=(),
        exc_info=None,
    )

    assert json.loads(JsonFormatter().format(record))["trace_id"] is None
