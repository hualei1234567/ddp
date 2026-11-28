# 可观测性与告警生产闭环演练证据（2026-11-28）

> 覆盖目标 4：Alertmanager → `alert-receiver` 告警生产闭环验证 + OpenTelemetry (`DDP_OTEL_ENABLED=true`) 生产联调配置与非 Mock 追踪演练。

## 一、本批交付清单

| 资产 | 路径 | 说明 |
|---|---|---|
| 告警接收端增强（向后兼容） | `services/alert-receiver/app/main.py` | 新增可选 Bearer 令牌鉴权（`ALERT_RECEIVER_BEARER_TOKEN`）、可选 HMAC-SHA256 验签（`ALERT_RECEIVER_HMAC_SECRET` + `X-Alertmanager-Signature`）、可选滑动窗口去重（`ALERT_RECEIVER_DEDUP_WINDOW_SECONDS`，默认 0 关闭以保持既有全量留证语义，且 `firing → resolved` 恢复事件永不被误去重） |
| 告警接收端单元测试（16 项） | `services/alert-receiver/tests/test_receiver.py`、`services/alert-receiver/tests/conftest.py` | 在既有 13 项基础上补齐 Bearer 鉴权、HMAC 验签、去重与恢复事件 3 项回归用例 |
| Alertmanager 闭环演练脚本 | `scripts/ops/alerting_e2e_drill.py` | 顶层仅标准库（MEM-INC-030）；支持自拉起本地 `alert-receiver` 或对接 `--target-url`；验证健康探针、401 鉴权/签名拦截、Alertmanager v4 `firing` 落盘、窗口内去重、`resolved` 恢复落盘与 `/alerts` + `/stats` + `alerts.ndjson` 三方计数核对 |
| OTel Collector 生产联调配置 | `infra/monitoring/otel-collector-config.yaml` | 标准 `otel/opentelemetry-collector-contrib` 配置：OTLP gRPC (`:4317`) / HTTP (`:4318`)、`memory_limiter` + `batch` 处理器、`debug` + `prometheus` (`:8889/metrics`) 导出器、`health_check` (`:13133`) |
| OTel 非 Mock 追踪演练脚本 | `scripts/ops/otel_trace_drill.py` | 顶层仅标准库（MEM-INC-030）；起本地轻量 HTTP OTLP 接收桩（`127.0.0.1:<随机端口>/v1/traces`），设 `DDP_OTEL_ENABLED=true` 触发真实 FastAPI `GET /health` 请求 + 出站 `inject_headers()`，反序列化真实 OTLP protobuf `ExportTraceServiceRequest` 断言 `service.name`、`traceparent`、`X-Trace-Id` 与 span 属性 |
| 演练单元测试（6 项） | `scripts/tests/test_observability_drills.py` | 严格遵守 MEM-INC-030（顶层仅导入标准库），含顶层导入 AST 守卫、Collector YAML 结构校验、告警闭环演练、OTLP 接收桩与完整 FastAPI+OTel 端到端用例 |

---

## 二、E1 实跑证据 1：Alertmanager → `alert-receiver` 告警闭环演练

### 执行命令

```bash
/Users/hualei/vscodeworkspace/DataPlatform/backend/.venv/bin/python \
  scripts/ops/alerting_e2e_drill.py \
  --out docs/testing/evidence/2026-11-28-observability-closure/alerting-drill-result.json
```

### 真实结果（摘自 `alerting-drill-result.json`）

- `passed`: `true`（`mode: "embedded"`，运行时间 `2026-11-28T10:08:37Z`）
- 健康检查：`GET /health` → `200`（`service: "ddp-alert-receiver"`）
- 鉴权/签名守卫：
  - 缺 Bearer 令牌 → `401`
  - 篡改 `X-Alertmanager-Signature` → `401`
- `firing` 投递与落盘：`200 {"status": "accepted", "stored": true}`
- 重复 `firing` 去重（60s 窗口）：`200 {"status": "deduplicated", "stored": false}`
- `resolved` 恢复投递：`200 {"status": "accepted", "stored": true}`
- 三方计数核对：
  - `GET /stats`：`total=2, firing=1, resolved=1, by_alertname={"DdpApiDown": 2}`
  - `GET /alerts`：`count=2`（最新一条为 `resolved`）
  - 落盘文件 `alerts.ndjson`：`2` 行，逐行合法 JSON

---

## 三、E1 实跑证据 2：OpenTelemetry (`DDP_OTEL_ENABLED=true`) 非 Mock 追踪演练

### 执行命令

```bash
/Users/hualei/vscodeworkspace/DataPlatform/backend/.venv/bin/python \
  scripts/ops/otel_trace_drill.py \
  --out docs/testing/evidence/2026-11-28-observability-closure/otel-drill-result.json
```

### 真实结果（摘自 `otel-drill-result.json`）

- `passed`: `true`（运行时间 `2026-11-28T10:09:19Z`）
- 追踪上下文传播（W3C `traceparent`）：
  - 入站 `traceparent`：`00-edce086d3a1f51fd0d4ddd040d07eb81-a6c69f9661fa0eac-01`
  - FastAPI 响应头 `X-Trace-Id`：`edce086d3a1f51fd0d4ddd040d07eb81`（与入站完全一致）
  - 出站 `inject_headers()["traceparent"]`：`00-edce086d3a1f51fd0d4ddd040d07eb81-02c8aa1878fb4ffd-01`（同一 `trace_id` 延续）
- 本地 OTLP/HTTP 接收桩实收载荷：
  - `POST /v1/traces`，`Content-Type: application/x-protobuf`，`raw_bytes_len: 662`，反序列化得到 `3` 条真实 span
- 校验通过的服务端 span：
  - `name`: `"GET /health"`（路由模板，无高基数）
  - `scope`: `"ddp"`
  - `trace_id`: `"edce086d3a1f51fd0d4ddd040d07eb81"`
  - `parent_span_id`: `"a6c69f9661fa0eac"`（与入站 `traceparent` 父 span ID 一致）
  - `attributes`: `{"http.request.method": "GET", "url.path": "/health", "http.route": "/health", "http.response.status_code": 200}`

---

## 四、测试、代码规范与密钥扫描验证

| 检查项 | 命令 | 真实结果 |
|---|---|---|
| 全量后端依赖环境测试（`alert-receiver` + 演练单测） | `backend/.venv/bin/python -m pytest services/alert-receiver/tests scripts/tests/test_observability_drills.py -q` | **22 passed** |
| 最小 CI 环境模拟（无 `fastapi`/`opentelemetry`，验证 MEM-INC-030） | `/Users/hualei/.venvs/robotics/bin/python3 -m pytest scripts/tests/test_observability_drills.py -q` | **5 passed, 1 skipped**（0 收集错误） |
| Ruff 静态检查 + 格式检查 | `backend/.venv/bin/ruff check ... && backend/.venv/bin/ruff format --check ...` | **All checks passed / 8 files already formatted** |
| Secret 扫描 | `python3 scripts/check_secrets.py` | **Secret 扫描通过** |

---

## 五、未验证范围与边界

- `infra/monitoring/otel-collector-config.yaml` 已通过结构与端口/管线单测校验，但本批未修改 `docker-compose.yml`（按既有规范不擅自改动 compose 编排），未在真实 `otel/opentelemetry-collector-contrib` 容器内启动验证。
- `scripts/ops/alerting_e2e_drill.py` 本次以自拉起真实 `alert-receiver` 实例模式完成全闭环演练；若需对接真实运行中的 Alertmanager/compose 容器，可使用 `--target-url http://127.0.0.1:9194`。
- 本目录附带原始实跑 JSON 证据：
  - `alerting-drill-result.json`
  - `otel-drill-result.json`
