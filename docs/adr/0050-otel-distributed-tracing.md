# ADR-0050：接入 OpenTelemetry 分布式追踪

- 状态：已接受
- 日期：2026-11-27
- 决策者：后端执行者（v52-053）
- 相关任务：V52-053「OTel 分布式 trace 未接入」
- 相关 ADR：[0014 复用优先与代码质量标准](0014-reuse-first-engineering-standard.md)、
  [0031 分布式限流](0031-distributed-rate-limit-via-redis.md)、
  [0006 测试与 CI](0006-testing-and-ci.md)

---

## 1. 背景

DDP 审计把「OpenTelemetry 分布式 trace 未接入」列为剩余项。接入前仓库的观测
能力只有两样：

| 已有能力 | 位置 | 能回答什么 | 不能回答什么 |
|---|---|---|---|
| Prometheus 指标 | `backend/app/core/metrics.py` + `app/api/middleware/metrics.py` | 「这个接口被调了多少次、快不快」 | 「这次慢是因为调了下游，还是因为查库」 |
| RequestId 中间件 | `backend/app/api/middleware/request_id.py` | 「这批日志属于哪次请求」 | 「这次请求和那次请求是不是同一条业务链」 |

缺的是**跨进程调用链**：一次 API 请求会触发 Worker，Worker 会调用外部训练
Adapter / 模型平台，但三段之间没有任何共同的关联标识，排障时只能靠时间戳猜。

### 1.1 特别说明：本次接入与既有 `trace_id` 的关系

本仓库已有一个叫 `trace_id` 的东西（`app/core/logging.py` 的 `trace_id_var`），
其值等于 `request_id`，被写进审计记录与 outbox 事件的 `request_id` 字段。
`app/api/middleware/request_id.py` 的模块注释明确写着「本项目不引入
OpenTelemetry……将来真接入分布式追踪时改为从 `traceparent` 提取、与
`request_id` 分离」。

本次决策就是那句「将来」的兑现，**但取一个更保守的口径**：不改既有
`trace_id` 的取值与语义（它已被审计与 outbox 依赖），而是**新增**一个独立
命名的 `otel_trace_id`。理由见 §5.3。

---

## 2. 方案对比

### 2.1 候选方案

| 方案 | 做法 | 优点 | 否决/采纳理由 |
|---|---|---|---|
| **A. 官方 OpenTelemetry** | 引入 `opentelemetry-api/sdk/exporter-otlp`，自写 HTTP 中间件与出站插桩 | 事实标准、生态兼容（Jaeger/Tempo/OTel Collector 直接可收）、W3C 传播 | **采纳** |
| B. 自研轻量 trace | 自己实现 16 字节 trace id、采样、W3C `traceparent` 编解码、批量上报、重试 | 依赖为零、体积最小 | 否决：W3C traceparent 的编解码规则（version/flags/tracestate 长度限制、非法输入处理）有大量边界，自研极易与生态不一致；上报协议、采样、背压也都要自己写，等于重造一个事实标准。CLAUDE.md §二「官方 SDK 优先」不满足 |
| C. 只做指标关联（不动 trace） | 给 Prometheus 指标加 `trace_id` 标签 | 改动极小 | 否决：**标签值是 32 位高基数**，`prometheus-client` 每个标签组合一条时间序列，会把指标注册表打爆。CLAUDE.md 与 `app/core/metrics.py` 模块注释都把「高基数防护」列为该模块最重要的约束 |
| D. 引入 `opentelemetry-exporter-prometheus` | 把 span 指标化后进 Prometheus | 与已有指标体系统一 | 否决：它只能表达「span 的统计量」，表达不了「跨服务调用链」；且与已有 `prometheus-client` 职责重叠，属于同一职责两套技术栈 |
| E. 引入 `opentelemetry-instrumentation-*` 全家桶 | 用官方 auto-instrumentation 包（FastAPI/httpx/SQLAlchemy 全自动埋点） | 埋点最省事 | 否决（部分）：官方 instrumentor 会**接管**框架行为（如自动注入 SQL 注释、拦截所有 httpx 调用），与本仓库「规则只允许存在一处权威实现」冲突（例如 span 名与指标里的路由模板桶会各算各的）。本方案只借用官方的 **API/SDK/exporter**，插桩由本仓库自己写 |

### 2.2 为什么插桩自己写而不是用官方 instrumentor

官方 `opentelemetry-instrumentation-fastapi` 会在路由匹配后设置 `http.route`，
看起来正好。但它同时会：

- 接管 ASGI 调用栈（多一层 `OpenTelemetryMiddleware`），本仓库的中间件顺序
  （RequestId → SecurityHeaders → CORS → Metrics → RateLimit → 路由）会被打乱；
- 为 SQLAlchemy 的每条语句自动加 span —— 本仓库 200+ 端点的 SQL 量下，
  span 数会从「请求级」膨胀到「语句级」，链路后端的存储成本量级不同；
- 自动埋点 httpx 会把**所有**出站请求都纳入，包括 Worker 里的大文件下载
  （Hugging Face 流式下载），这类请求产生 span 没有排障价值。

因此：**插桩用本仓库自己的中间件，但语义标准（span 名、属性、W3C 传播）
严格按 OTel 官方语义约定实现**，并复用本仓库已有的路由归一逻辑（§5.1）。

---

## 3. 依赖评估

### 3.1 新增依赖

| 包 | 版本 | 用途 |
|---|---|---|
| `opentelemetry-api` | 1.45.2 | W3C 上下文传播、`Span`/`Tracer` 接口 |
| `opentelemetry-sdk` | 1.45.2 | `TracerProvider`、`Resource`、采样器、span processor |
| `opentelemetry-exporter-otlp` | 1.45.2 | OTLP 上报（gRPC 4317 / HTTP 4318） |

传递依赖（由上表引入）：`opentelemetry-exporter-otlp-proto-grpc`、
`opentelemetry-exporter-otlp-proto-http`、`opentelemetry-exporter-otlp-common`、
`opentelemetry-exporter-otlp-proto-common`、`opentelemetry-exporter-http-transport`、
`opentelemetry-proto`、`opentelemetry-semantic-conventions`、`protobuf`、
`grpcio`、`requests`、`charset-normalizer`。

### 3.2 替代方案

见 §2.1。要点：自研（B）要自己实现 W3C 规范的一堆边界条件；C/D 与既有
Prometheus 体系冲突且表达不了调用链；E 的官方 instrumentor 与本仓库既有
中间件/防基数规则冲突。

### 3.3 版本固定策略

全部钉到**具体版本**，禁止 `latest`（CLAUDE.md §三）。`requirements.lock` 用
`pip-compile --generate-hashes` 重新生成，每个包带全部 wheel 的 sha256，
安装走 `--require-hashes`。锁定命令（唯一来源）：

```bash
cd backend && .venv/bin/pip-compile --generate-hashes --no-emit-index-url \
  --no-emit-trusted-host --output-file=requirements.lock requirements.txt
```

### 3.4 体积与性能影响

| 项 | 实测/结论 |
|---|---|
| 安装体积 | `opentelemetry*` 约 4.6MB；`grpcio` 约 40MB（含编译扩展）；`protobuf` 约 0.6MB；`requests` 约 0.6MB。合计约 46MB |
| **运行期开销（关闭态）** | **0**——见 §5.1，关闭时连 OTel 模块都不 import |
| 运行期开销（开启态） | 每个请求 1 个 server span + 每个出站 1 个 client span；采样率可配（`DDP_OTEL_SAMPLE_RATIO`） |
| import 开销 | 实测 `opentelemetry.api.trace` ≈ 35ms、`opentelemetry.sdk.trace` ≈ 30ms。**顶层 import 会让每次启动都付这笔钱**，故全部 lazy import |

### 3.5 许可证

全部 **Apache-2.0**（OTel 官方系列）。与仓库既有依赖（FastAPI=MIT、
SQLAlchemy=MIT、prometheus-client=Apache-2.0 AND BSD-2-Clause）同族，
无 copyleft 传染，无 GPL 兼容性风险。

### 3.6 安全公告复核

- `pip-audit -r backend/requirements.lock` → **No known vulnerabilities found**（退出码 0）；
- 新增的 15 个包逐一核对，无 advisory；
- `googleapis-common-protos` 仅为 OTLP proto 编解码所需，不含可执行代码；
- `requests` 是 exporter 的官方 transport 依赖，**不被任何业务代码使用**
  （业务 HTTP 客户端仍只有 `httpx` 一套）。

### 3.7 关于「第二套 HTTP 客户端」

`opentelemetry-exporter-otlp-proto-http` 官方实现硬依赖 `requests`。曾评估改用
官方 `urllib3` transport 规避它，但该选择只能走 exporter 的**私有参数**
（`_transport=`），违反 CLAUDE.md「不依赖私有 API，升级会静默失效」，故不采用。

结论：本仓库的**业务** HTTP 客户端仍然只有 `httpx` 一套（出站 Adapter 调用、
Worker 下载全部走 httpx）；`requests` 仅作为 exporter 上传 span 的传递依赖存在，
不参与任何业务请求路径。

---

## 4. 开关语义

### 4.1 配置项

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `DDP_OTEL_ENABLED` | bool | `false` | 总开关。false = 完全不追踪 |
| `DDP_OTEL_SERVICE_NAME` | str | `""`（回落 `ddp`） | OTel `service.name` |
| `DDP_OTEL_EXPORTER_OTLP_ENDPOINT` | str | `""` | OTLP collector 地址。空 = 未接线 |
| `DDP_OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc`\|`http` | `grpc` | 传输协议（4317 / 4318） |
| `DDP_OTEL_SAMPLE_RATIO` | float | `1.0` | 采样比例（0~1） |

### 4.2 关闭态（`DDP_OTEL_ENABLED=false`）——硬保证

- 不 import 任何 `opentelemetry*` 模块（含 SDK 与 exporter）；
- 不构造 `TracerProvider`，不设置全局 provider；
- 不产生任何 span；
- 响应头**不带** `X-Trace-Id`；
- 出站请求**不带** `traceparent`；
- 结构化日志字段集合与接入前**完全一致**（不出现 `otel_trace_id`）。

保证方式：`app/core/telemetry.py` 的所有 OTel import 都在函数体内；
`app/api/middleware/telemetry.py` 第一件事就是 `is_enabled()` 判断，为假则
原样透传；`app/core/logging.py` 只在 `otel_trace_id` 非 None 时才加该键。

### 4.3 「开了但没配 endpoint」→ 降级 + WARNING（不拒绝启动）

**决策：降级为不追踪，并记一条启动 WARNING，不拒绝启动。**

理由：

1. **追踪是可观测性依赖，不是业务正确性依赖。** 它挂了，业务照常服务。把它
   升级成「启动失败」，等于让一次监控配置错误变成一次全站可用性故障。
2. **仓库已有同源口径。** `DDP_METRICS_ACCESS_TOKEN` 未配置时不让 API 启动，
   只让 `/metrics` 返回 503（API 照常启动），见 `app/core/config.py` 中
   `_metrics_access_config_problems` 的注释。可观测性的配置问题应降级为
   「该项不可用」。
3. **但降级必须显式。** 记 WARNING 说明「追踪未接线，日志里的 `trace_id` 仍是
   `request_id`」。静默降级会让人以为「已经在追踪了」，比直接报错更难排查。

对照：**endpoint 配了但格式非法**（不是 http(s) 绝对地址）属于「配错了」，
由 `Settings.validate_runtime_security()` 在启动自检里 **fail-closed** 拒绝
启动——写法错误会让 exporter 在后台线程里静默失败，属于「配了但没生效」的
假象，必须拦下。

### 4.4 幂等

`configure_telemetry()` 幂等：已启用时重复调用直接返回既有状态，**不重复安装
全局 provider**（OTel 的全局 provider 只能设置一次，重复安装只会得到一条
无意义 warning，且两个 exporter 会争抢 span）。

---

## 5. 实现要点

### 5.1 高基数防护：复用既有路由归一逻辑

span 名是 OTel 里最高危的高基数维度（原始 URL 含 UUID/查询串）。规则**只有
一处权威实现**：`app/core/metrics.py` 的 `route_label_from_scope` /
`method_label`。本方案的中间件直接复用 `Metrics` 实例的方法，
**不另写一套防基数逻辑**（CLAUDE.md「规则只允许存在一处权威实现」）。

| 情形 | span 名 | `http.route` 属性 |
|---|---|---|
| 命中路由 | `GET /api/v1/projects/{project_id}/devices` | 路由模板 |
| 未命中路由（404 / 限流短路 / 尾斜杠重定向） | `HTTP GET` | `__unmatched__` |

未命中路由时用有界兜底名，**绝不进原始路径**（该路径完全由客户端控制，可无限
随机）。

**时机难点**：Starlette 在路由匹配成功后才把命中的 Route 写回 `scope["route"]`
（`scope.update(child_scope)`），因此请求**开始时**拿不到路由模板。做法与
`app/api/middleware/metrics.py` 一致：先用有界的临时名开 span，响应返回后用
`update_name` 改成模板名。

### 5.2 上下文传播

- **入**：请求头 W3C `traceparent` / `tracestate` → 提取为父上下文；
- **出**：出站请求注入 `traceparent`（在 **client span 内部**注入，使下游成为
  本 span 的子节点——在 span 外注入的话下游会变成兄弟节点）；
- **propagator 固定为官方 W3C tracecontext**，**不传播 baggage**：客户端可任意
  构造 baggage，传播它等于把不可信数据带进下游服务与日志。

### 5.3 日志里的两个 ID 不合并

| 字段 | 来源 | 语义 | 稳定性 |
|---|---|---|---|
| `trace_id` | `trace_id_var`（既有） | 请求关联 ID（= `request_id`） | **不变**：被写进审计与 outbox 的 `request_id` 字段 |
| `otel_trace_id` | OTel span（新增） | 分布式链路 ID | 仅追踪开启时存在 |

若把两者合成一个字段，同一个键会在不同配置下表示不同的东西（口径漂移），
也让审计里的 `request_id` 在开启追踪后不再等于请求的 `request_id`。
因此：追踪关闭时日志字段集合与接入前**完全一致**，开启时多一个显式命名的
字段。

### 5.4 出站插桩

`app/infrastructure/adapter_http.py` 是训练网关与模型平台网关**共用**的传输层
（`app/infrastructure/training_adapter_http.py` 与
`model_platform_http.py` 都经它发请求），因此**一处插桩即覆盖两个出站面**。

**SSRF 守卫不受影响**：`ensure_safe_adapter_url`（`app/infrastructure/
adapter_url_guard.py`）仍在两个网关里、出站前调用，本方案不改动它，也不改动
`follow_redirects=False` 的既有语义。

---

## 6. 影响面

### 6.1 代码

| 文件 | 变更 |
|---|---|
| `backend/app/core/telemetry.py` | **新增**：OTel 装配层 |
| `backend/app/api/middleware/telemetry.py` | **新增**：ASGI 服务端 span 中间件 |
| `backend/app/core/config.py` | 新增 5 个 `DDP_OTEL_*` 配置 + 启动自检 |
| `backend/app/core/logging.py` | 新增 `otel_trace_id` 日志字段（关闭态不出现） |
| `backend/app/main.py` | 装配 `configure_telemetry` + 注册中间件 |
| `backend/app/infrastructure/adapter_http.py` | 出站 client span + `traceparent` 注入 |
| `backend/requirements.txt` / `.lock` | 新增 3 个直接依赖（+12 个传递依赖） |
| `.env.example` | 新增 5 个键（含注释） |
| `backend/tests/unit/test_telemetry.py` | **新增**：装配层测试 |
| `backend/tests/api/test_telemetry_api.py` | **新增**：HTTP 层测试 |

### 6.2 契约 / 数据库 / API

- **契约**：无变更。`packages/contracts/` 未触碰，契约 version 未 bump。
- **数据库**：无变更，无迁移。
- **API**：**响应体无变更**；仅在追踪开启时**新增**响应头 `X-Trace-Id`
  （关闭时不存在，对既有客户端完全无感知）。既有 `X-Request-Id` 不变。

### 6.3 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `X-Trace-Id` 头未在 CORS `expose_headers` 中列出 | 浏览器端 JS 读不到该头 | 已在 §7 列为生产接线步骤（需改 `config.py` 的 `cors_expose_headers`）。**本任务不改**：改它会影响既有跨域配置语义，且该头只在追踪开启时存在 |
| gRPC 传输带 40MB `grpcio` | 镜像变大 | 可改用 HTTP 协议（4318），或由主控在镜像层精简 |
| OTLP collector 不可达 | span 静默丢弃（不影响业务） | exporter 自带重试与批量；需运维侧保证 collector 可达 |
| 采样率配错（=1.0 且流量大） | 链路后端存储成本高 | `DDP_OTEL_SAMPLE_RATIO` 可配；ADR §7 给出建议值 |

---

## 7. 后续接线步骤（生产）

1. 部署 OTLP Collector（由主控统一加，**本任务不改 `docker-compose.yml`**）；
2. 设 `DDP_OTEL_ENABLED=true` 与 `DDP_OTEL_EXPORTER_OTLP_ENDPOINT`；
3. 视流量把 `DDP_OTEL_SAMPLE_RATIO` 调到 0.1~0.2（生产建议先低采样观察）；
4. 若前端需要读 `X-Trace-Id`，把该头加进 `cors_expose_headers`；
5. 关联 `X-Trace-Id` 与既有 `X-Request-Id`：两者都出现在响应头，日志里
   `otel_trace_id` 与 `trace_id` 并存，可用 `X-Request-Id` 查日志、用
   `X-Trace-Id` 查链路。

---

## 8. 回滚方式

### 8.1 紧急回滚（秒级）

```bash
DDP_OTEL_ENABLED=false
```

行为回到接入前：零 span、无 `X-Trace-Id`、日志字段集合不变。**不需要重启之外的
任何操作**（配置改动需重启进程生效）。

### 8.2 代码回滚

`git revert` 本次提交即可：新增的两个模块不被任何既有模块依赖
（`app/core/metrics.py`、`app/core/logging.py` 的既有行为未被改变），
回滚后应用照常启动。

### 8.3 依赖回滚

从 `backend/requirements.txt` 移除 3 行后重新 `pip-compile` 生成 lock。
`opentelemetry` 系列与既有依赖无版本冲突（pip-compile 已验证：新旧 lock
并存时既有包版本无一变化，唯一的差异是 `greenlet` 这个 SQLAlchemy 的平台
可选依赖在解析结果中被移除）。

---

## 9. 验收证据

| 检查 | 命令 | 结果 |
|---|---|---|
| 格式与静态检查 | `cd backend && .venv/bin/ruff check app tests migrations && .venv/bin/ruff format --check app tests migrations` | 通过 |
| 全量测试 | `cd backend && .venv/bin/python -m pytest -q` | 见 PR 描述的真实数字 |
| 密钥扫描 | `python3 scripts/check_secrets.py` | 通过 |
| SAST | `bash scripts/check_sast.sh` | 通过 |
| 依赖漏洞 | `cd backend && .venv/bin/pip-audit -r requirements.lock` | No known vulnerabilities found |
| 契约一致性 | `cd backend && .venv/bin/python scripts/check_contract_parity.py --strict` | 通过 |

追踪专项测试（关闭态零开销、开启态 span/属性/响应头、跨请求传播、
`current_trace_id()` 两种返回、配置解析降级语义）见
`backend/tests/unit/test_telemetry.py` 与
`backend/tests/api/test_telemetry_api.py`。
