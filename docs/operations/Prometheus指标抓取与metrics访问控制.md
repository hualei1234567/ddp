# Prometheus 指标抓取与 /metrics 访问控制

> 适用版本：N9 可观测性补齐（`/metrics` 由未鉴权改为默认需要认证）
> 最后更新：2026-11-16

## 1. 为什么抓取端不能用用户 JWT

`/metrics` 的抓取方是 Prometheus / VictoriaMetrics / Grafana Agent 这类**机器**：
固定 IP、无用户会话、没有 DDP 的令牌签发流程，也不支持
`X-DDP-Signature` 那套「时间戳 + nonce + body」的 HMAC 回调签名（那是给训练/模型
Adapter 回调用的）。强行要求用户 JWT 会作废所有现有抓取配置。

因此采用 **静态 Bearer 令牌**（HTTP 标准认证方案，抓取端配置里一行
`authorization.credentials` 即可），并可选叠加**来源网段白名单**做纵深防御。

## 2. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DDP_METRICS_ACCESS_TOKEN` | 空 | 静态抓取令牌。配置后 `/metrics` 必须带 `Authorization: Bearer <token>`，否则 401 |
| `DDP_METRICS_ALLOWED_SOURCE_NETWORKS` | `[]` | 允许抓取的来源网段（JSON 数组，CIDR 或单地址）。空数组表示不限制来源 |

非 local 环境的令牌必须满足：去空白后至少 **32** 个字符、不能是占位符
（`replace-me-in-production` 等）、不得含首尾空白。不满足时**启动自检失败**。

生成令牌：

```bash
openssl rand -hex 32
```

## 3. 未配置令牌时的行为（失败关闭的边界）

| 环境 | 未配置令牌时 `/metrics` | 原因 |
|---|---|---|
| `local` / `dev` / `development` | **放行** | 本地开发与测试便利，与 `DDP_LOCAL_HEADER_AUTH` 同一口径 |
| 其它（`staging` / `production`） | **503 `metrics_not_configured`** | 安全的默认值 |

选择 **请求级 503** 而不是「启动失败」或「放行 + warning」的理由：

- **启动失败**会把一次安全加固变成部署事故，并连带让 API/Worker 一起起不来；
- **放行 + warning** 等于没解决问题——日志里的 warning 不会是第一个被看到的东西；
- **503** 只影响 `/metrics` 一个端点（其余接口完全正常），抓取端会以 `up == 0`
  明确报警，运维看到后补配置即可。错误码与文案明确指向「未配置凭证」，
  不会让人误以为令牌写错了。

`/health` **保持公开**：它不含路由清单、流量或任何配置，唯一负载是
`{"status": "ok", ...}`，且容器 healthcheck（`docker-compose.yml`）、负载均衡
探活与就绪门禁都依赖它能无条件访问。给它加认证会让「服务不健康」和
「探针没带令牌」变成同一个现象，探活本身失去意义。

## 4. 抓取配置示例

### 4.1 Prometheus

```yaml
scrape_configs:
  - job_name: ddp-api
    metrics_path: /metrics
    scheme: https
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/ddp-metrics-token
    static_configs:
      - targets: ["ddp-api.internal:8000"]
```

`credentials_file` 优于 `credentials`：直接写字面量会让令牌进入配置仓库。

### 4.2 VictoriaMetrics / vmagent

参数等价，令牌放在单独文件：

```bash
vmagent \
  -promscrape.config=/etc/vmagent/scrape.yml
```

```yaml
scrape_configs:
  - job_name: ddp-api
    authorization:
      type: Bearer
      credentials_file: /etc/vmagent/ddp-metrics-token
    static_configs:
      - targets: ["ddp-api.internal:8000"]
```

### 4.3 本地手工验证

```bash
# local 环境、未配置令牌时：
curl -s http://127.0.0.1:8000/metrics | head

# 配置令牌后：不带令牌被拒、带对令牌可抓
curl -si http://127.0.0.1:8000/metrics | head -1          # 401（或 503，见 §3）
curl -s -H "Authorization: Bearer $DDP_METRICS_ACCESS_TOKEN" \
  http://127.0.0.1:8000/metrics | head
```

## 5. 来源网段白名单

只比对 **ASGI 对端地址**，**不读取 `X-Forwarded-For`**：该头可被客户端伪造，
采信它等于把白名单交给攻击者决定（与限流中间件的
`DDP_RATE_LIMIT_TRUST_FORWARDED_FOR` 默认关闭是同一判断）。

因此：

- 抓取端直连 API 时，把抓取端网段写进白名单；
- API 在反向代理之后时，对端地址是**代理**的地址——要么把代理地址写进白名单，
  要么只用令牌、不启用网段白名单；
- 配置了白名单后，地址无法解析的来源（含测试客户端的 `testclient`）一律拒绝。

来源被拒与令牌错误对外**不可区分**（同为 401、同一文案），避免远端据此判断
「自己是否在允许网段内」或「令牌是否接近正确」。

## 6. 失败排查

| 现象 | 含义 | 处置 |
|---|---|---|
| `/metrics` 返回 503 `metrics_not_configured` | 非 local 环境未配置 `DDP_METRICS_ACCESS_TOKEN` | 生成并注入令牌 |
| `/metrics` 返回 401 `not_authenticated` | 未带 Bearer、Bearer 值错误，或来源不在白名单 | 检查令牌文件；若启用了网段白名单，核对对端地址 |
| Prometheus 显示目标 `up == 0` 且错误含 401/503 | 同上，见 API 侧日志（`ddp.metrics` logger） | 同上 |
| 启动报错 `DDP_METRICS_ACCESS_TOKEN 长度不足 32` | 令牌过短 | 用 `openssl rand -hex 32` 重新生成 |
| 启动报错 `DDP_METRICS_ALLOWED_SOURCE_NETWORKS 非法` | 网段格式错误，或写成 `10.1.2.3/8`（带主机位） | 改为合法 CIDR，如 `10.0.0.0/8` |

## 7. 未纳入契约

`/metrics` 保持 `include_in_schema=False`：它是运维接口而非业务契约，抓取端
约定使用根级路径，纳入版本前缀或 OpenAPI 会让抓取配置随 API 版本迁移失效，
也会把运维接口带进 `scripts/check_contract_parity.py` 的比对范围。
是否正式纳入 `packages/contracts` 由 System Architect 走 ADR 决定。
