# 分布式追踪（OpenTelemetry）运维手册

> 版本：v1.0 · 2026-11-27
> 分支基线：`feat/v52-taskc-otel`
> 定位：本文回答「追踪怎么开、开起来是什么样、出问题怎么查、怎么关」。
> 设计取舍与依赖评估见 `docs/adr/0050-otel-distributed-tracing.md`（不在本文重复）。

## 一、能力现状

| 能力 | 位置 | 状态 |
|---|---|---|
| OTel 装配层 | `backend/app/core/telemetry.py` | ✅ 本批新增 |
| HTTP 服务端 span | `backend/app/api/middleware/telemetry.py` | ✅ 本批新增 |
| 出站 client span + `traceparent` 注入 | `backend/app/infrastructure/adapter_http.py` | ✅ 本批新增 |
| `X-Trace-Id` 响应头 | 同上（中间件） | ✅ 本批新增（**仅追踪开启时存在**） |
| 日志 `otel_trace_id` 字段 | `backend/app/core/logging.py` | ✅ 本批新增（**仅追踪开启时出现**） |
| OTLP Collector 部署 | — | ❌ **不在本批范围**（由主控统一加，勿自行改 `docker-compose.yml`） |
| 前端读取 `X-Trace-Id` | — | ❌ 未接线（需把该头加进 `cors_expose_headers`） |

**默认状态：关闭。** 不配置 `DDP_OTEL_ENABLED=true` 时，追踪能力**完全不存在**：
不 import 任何 OTel 模块、不产生 span、响应无 `X-Trace-Id`、日志字段与接入前一致。

---

## 二、开关语义（运维必须知道的三条）

### 2.1 开关表

| 环境变量 | 默认 | 含义 | 生效级别 |
|---|---|---|---|
| `DDP_OTEL_ENABLED` | `false` | 总开关 | 进程启动 |
| `DDP_OTEL_SERVICE_NAME` | `""`（回落 `ddp`） | OTel `service.name` | 进程启动 |
| `DDP_OTEL_EXPORTER_OTLP_ENDPOINT` | `""` | OTLP collector 地址 | 进程启动 |
| `DDP_OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc`(4317) / `http`(4318) | 进程启动 |
| `DDP_OTEL_SAMPLE_RATIO` | `1.0` | 采样比例 0~1 | 进程启动 |

全部走 `DDP_` 前缀的环境变量（见 `backend/app/core/config.py`）。

### 2.2 「开了但没配 endpoint」= 降级 + WARNING（**不是**启动失败）

这是最容易被误判的一条：

```bash
# 设了 true 但 endpoint 为空 → 应用照常启动，追踪不生效
DDP_OTEL_ENABLED=true
DDP_OTEL_EXPORTER_OTLP_ENDPOINT=
```

启动日志会出现一条 WARNING：

```json
{"level": "WARNING", "logger": "ddp.telemetry", "message": "OpenTelemetry 追踪未启用：已设置 DDP_OTEL_ENABLED=true 但未配置 DDP_OTEL_EXPORTER_OTLP_ENDPOINT，本次降级为不追踪（不产生 span、响应不带 X-Trace-Id）"}
```

**为什么这样设计**：追踪是可观测性依赖，不是业务正确性依赖。把它升级成
「启动失败」，等于让一次监控配置错误变成一次全站可用性故障。仓库里已有同源
口径：`DDP_METRICS_ACCESS_TOKEN` 未配置时不让 API 启动，只让 `/metrics` 返回
503。

**但降级必须看得见**：那条 WARNING 就是「你以为在追踪、其实没有」的唯一信号。
排查「链路里查不到这个服务」时，**先 grep 这条 WARNING**。

### 2.3 「配了但地址非法」= 启动失败（fail-closed）

```bash
DDP_OTEL_ENABLED=true
DDP_OTEL_EXPORTER_OTLP_ENDPOINT=not-a-url   # ← 启动自检直接拒绝
```

地址不是 http(s) 绝对地址时，`Settings.validate_runtime_security()` 在启动
自检里拒绝启动并打印具体问题。理由：写法错误会让 exporter 在后台线程里静默
失败，属于「配了但没生效」的假象，必须拦下。

---

## 三、开启步骤（生产）

### 3.1 前置

1. **OTLP Collector 已部署**（不在本批范围，由主控统一加）；
2. 确认 collector 的地址与端口（gRPC 默认 4317，HTTP 默认 4318）；
3. 确认采样率。生产**建议先低采样**观察成本：

   ```bash
   DDP_OTEL_SAMPLE_RATIO=0.1   # 10% 采样
   ```

### 3.2 配置与重启

```bash
DDP_OTEL_ENABLED=true
DDP_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.internal:4317
DDP_OTEL_EXPORTER_OTLP_PROTOCOL=grpc
DDP_OTEL_SERVICE_NAME=ddp-api
```

改完**重启进程**（配置在启动期读取）。启动日志应出现：

```json
{"level": "INFO", "logger": "ddp.telemetry", "message": "OpenTelemetry 追踪已启用：service=ddp-api endpoint=http://otel-collector.internal:4317 protocol=grpc sample_ratio=0.1"}
```

### 3.3 验证「真的在追踪」

```bash
# 1) 任意业务请求，响应头应出现 X-Trace-Id（32 位小写 hex）
curl -sD- -o /dev/null http://localhost:8000/health | grep -i x-trace-id
# 期望：x-trace-id: 3d7ed3a0c99f78206cde7795b95793dd

# 2) 请求处理期间打的日志，应带 otel_trace_id，且与响应头同值
curl -s http://localhost:8000/health >/dev/null
docker compose logs api | grep otel_trace_id | tail -3
```

**若第 1 步没有该头**：按 §2.2 先 grep 启动 WARNING，再确认
`DDP_OTEL_ENABLED` 真的被进程读到（容器里环境变量没传进去是最常见原因）。

---

## 四、链路里能看到什么

### 4.1 span 层级

```
（上游服务）
  └─ GET /api/v1/datasets/{dataset_version_id}/training-runs   [server, DDP]
       └─ POST /training-runs                                   [client, DDP → 训练 Adapter]
            └─ （Adapter 内部）
```

- **server span**：由 `backend/app/api/middleware/telemetry.py` 产生；
- **client span**：由 `backend/app/infrastructure/adapter_http.py` 产生，一处
  插桩同时覆盖训练网关与模型平台网关（两者共用该传输层）。

### 4.2 span 名规则（**不是原始 URL**）

| 情形 | span 名 | `http.route` 属性 |
|---|---|---|
| 命中路由 | `GET /api/v1/projects/{project_id}/devices` | 路由模板 |
| 未命中路由（404 / 限流短路 / 尾斜杠重定向） | `HTTP GET` | `__unmatched__` |

span 名是链路后端里最高危的高基数维度：原始 URL 含 UUID/查询串，用它当
span 名会让索引迅速膨胀。归一规则与 Prometheus 指标**共用同一处权威实现**
（`backend/app/core/metrics.py`），因此**指标里的 path 桶与链路里的 span 名
永远一致**——这一点在排障时很省事：指标里看到的 `path` 就是链路里该查的
span 名。

### 4.3 span 属性

| 属性 | 含义 |
|---|---|
| `http.route` | 路由模板（与指标里的 `path` 同值） |
| `http.request.method` | HTTP 方法 |
| `http.response.status_code` | 响应状态码 |
| `url.path` | 请求路径 |
| `service.name` / `service.version` / `deployment.environment` | Resource 身份 |

---

## 五、两个 ID 的关系（排障时最常混淆的一点）

排障时手上有两个 ID，**职责不同**：

| ID | 出现位置 | 回答什么 | 怎么用 |
|---|---|---|---|
| `X-Request-Id` | 响应头、错误体 `error.request_id`、日志 `trace_id` | 「这是哪次请求」 | 查应用日志、审计记录、outbox 事件 |
| `X-Trace-Id` | 响应头（追踪开启时）、日志 `otel_trace_id` | 「这是哪条链路」 | 查链路后端（Jaeger/Tempo）的 span 树 |

两者**不合并**：`trace_id` 的值写进了审计与 outbox 事件的 `request_id` 字段，
合并会让审计里的 `request_id` 在开启追踪后不再等于请求的 `request_id`。
追踪关闭时日志里**没有** `otel_trace_id` 这个键（字段集合与接入前完全一致）。

**串联方法**：拿到 `X-Request-Id` 后，在日志里 grep 它，拿到同一条日志的
`otel_trace_id`，再去链路后端查——一次跳转定位整条调用链。

---

## 六、常见故障与处置

| 现象 | 排查步骤 | 处置 |
|---|---|---|
| 响应头没有 `X-Trace-Id` | ① 启动日志有没有 WARNING「降级为不追踪」；② 容器里 `DDP_OTEL_ENABLED` 是不是真的传进去了 | ① 有 WARNING → 补 `DDP_OTEL_EXPORTER_OTLP_ENDPOINT`；② 没传进去 → 补部署配置 |
| 应用起不来，报「安全配置不完整」 | 启动信息里的具体问题 | endpoint 不是 http(s) 绝对地址 → 改成 `http://host:port` |
| 链路里有本服务，但 span 名是 `HTTP GET` | 该请求没命中任何路由 | 正常现象：404 / 限流短路 / 尾斜杠重定向都归此类；`http.route` 为 `__unmatched__` |
| 链路后端查不到任何 span | collector 是否可达；`DDP_OTEL_SAMPLE_RATIO` 是否被采样成 0；span 是否在请求结束前就结束 | 查 collector 日志；确认采样率 > 0 |
| 日志里没有 `otel_trace_id` | 该日志是否在请求处理**期间**打的 | 请求结束后再打的日志没有活跃 span（`otel_trace_id` 只在有活跃 span 时出现）——这是设计如此，不是故障 |
| span 数量暴涨、链路后端吃紧 | `DDP_OTEL_SAMPLE_RATIO` 是 1.0 | 降到 0.1~0.2 并重启 |

---

## 七、关闭与回滚

### 7.1 紧急关闭（推荐）

```bash
DDP_OTEL_ENABLED=false
```

重启进程即回到接入前状态：零 span、无 `X-Trace-Id`、日志字段集合不变、
出站请求不带 `traceparent`。**业务功能不受任何影响**——追踪只是旁路。

### 7.2 代码回滚

`git revert` 本批提交即可。新增模块不被既有模块依赖，回滚后应用照常启动。

---

## 八、与既有监控的关系

追踪与 Prometheus **互补，不替代**：

| | Prometheus 指标 | 分布式追踪 |
|---|---|---|
| 回答 | 「多少、快不快、趋势」 | 「这次慢在哪一环」 |
| 成本 | 低（常驻序列） | 高（每个请求一条 span） |
| 何时用 | 日常巡检、告警 | 故障排查、单请求定位 |
| 基数防护 | 路由模板归一（`app/core/metrics.py`） | **同一套规则**（复用 `route_label_from_scope`） |

排查顺序建议：**指标看趋势 → 定位到具体接口 → 追踪看这一次的链路细节**。

---

## 九、术语

| 术语 | 含义 |
|---|---|
| span | 一次调用（服务端或客户端）。链路的基本单位 |
| trace | 一条调用链，由多个 span 组成 |
| trace_id | 链路的 ID（32 位 hex） |
| span_id | 单个 span 的 ID（16 位 hex） |
| `traceparent` | W3C Trace Context 传播头，跨服务串联用 |
| OTLP | OpenTelemetry Protocol，上报协议（gRPC 4317 / HTTP 4318） |
| span 名 | span 的标题，**必须用路由模板**（高基数防护） |
| `http.route` | span 属性：命中的路由模板 |
| `__unmatched__` | 未命中任何路由时的归一标签（与指标口径一致） |
