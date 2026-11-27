# 告警链路运行手册（alerting-runbook）

> 覆盖审计项「**告警未接真实 Alertmanager 实例**」。
> 目标读者：值班工程师 / 平台管理员。
> 定位：**链路怎么走、看到告警该做什么、怎么自证它真的通了**。
> 配套：`infra/monitoring/alerts.yml`（规则）、`infra/monitoring/alertmanager.yml`（路由）、
> `services/alert-receiver/`（接收端）、`docs/ops/monitoring-setup.md`（监控栈部署）。

---

## 一、链路拓扑

```
Prometheus                       Alertmanager                    alert-receiver
（求值规则）                      （路由 / 分组 / 抑制）           （落盘留证）
    │                                   │                              │
    │ 规则 firing                        │ 按 route 选 receiver        │
    ├──────────────────────────────────►│ 按 alertname 分组            │
    │   POST /api/v1/alerts             │ group_wait 聚合             │
    │                                   │ inhibit_rules 抑制           │
    │                                   │                              │
    │                                   │  HTTP POST /alerts           │
    │                                   ├────────────────────────────►│
    │                                   │   Content-Type: application/json
    │                                   │   body ≤ 1 MiB              │
    │                                   │                              │
    │                                   │                    追加写 NDJSON
    │                                   │                    (fcntl.flock)
    │                                   │                    alerts.ndjson
    │                                   │                              │
    │                                   │                    GET /alerts  ◄── 人工复核
    │                                   │                    GET /stats   ◄── 计数核对
    └───────────────────────────────────┴──────────────────────────────┘

    观测入口：Prometheus UI :9190   Alertmanager UI :9193   接收端 :9194
```

**链路闭合的判据**（四条都满足才算真通）：

1. Prometheus 规则**真的 firing**（不是 pending，不是 inactive）；
2. Alertmanager **真的投递**（`alertmanager_notification_requests_total{integration="webhook"}` 增长，
   `..._failed_total` 不增长）；
3. 接收端 **真的落盘**（`alerts.ndjson` 有新行）；
4. 接收端 **真的查得到**（`GET /alerts` / `GET /stats` 能查到且计数对得上）。

---

## 二、组件与端口

| 组件 | compose 服务名 | 容器名 | 宿主端口 | 作用 |
|---|---|---|---|---|
| Prometheus | `prometheus` | `ddp-prometheus` | 9190 | 规则求值 |
| Alertmanager | `alertmanager` | `ddp-alertmanager` | 9193 | 路由 / 分组 / 抑制 |
| 告警接收端 | `alert-receiver` | `ddp-alert-receiver` | 9194 | 接收 → 落盘 → 查询 |

```bash
# 启动监控栈（接收端 + Alertmanager + Prometheus）
docker compose --profile monitoring up -d --build alert-receiver alertmanager prometheus

# 查看状态
docker compose --profile monitoring ps
```

> ⚠️ 接收端的宿主端口 9194 **仅供本地观察**，生产不应暴露到公网
> （服务本身无鉴权）。

---

## 三、6 条规则：看到该做什么

> 逐条核对自 `infra/monitoring/alerts.yml`（2026-11-27）。指标名与阈值以该文件为准。

| # | 告警名 | 触发表达式 | 持续 | 严重度 | 处置动作 |
|---|---|---|---|---|---|
| 1 | `DdpApiDown` | `up{job="ddp-api"} == 0` | 2m | critical | 看 `docker compose ps` 里 api 是否存活；`curl localhost:8000/health` 是否 200。容器在但探针失败 → 看 `docker compose logs api` 的**启动校验报错**（缺密钥会直接拒绝启动） |
| 2 | `DdpBusinessMetricsMissing` | `absent(ddp_outbox_due_events{job="ddp-api"})` | 15m | warning | **先看 `DdpApiDown` 是否同时存在**（API 挂了自然没指标，按那个处理）。否则 = `/metrics` 可达但采集器异常：查 api 日志里 `BusinessMetricsCollector` 异常堆栈（单组采集失败只缺席、不补 0），以及连接池耗尽 / 权限变更 |
| 3 | `DdpOutboxBacklog` | `ddp_outbox_due_events > 100` | 10m | warning | 查对应 worker 是否存活（`docker compose ps \| grep worker`）；看 `outbox_events` 表 `failed` 计数是否在涨。若**只有单一事件类型**积压，查该事件消费者是否注册（`scripts/check_outbox_registry.py`） |
| 4 | `DdpOutboxStale` | `ddp_outbox_oldest_due_age_seconds > 900` | 5m | critical | 消费者**实际已停止**。先确认 worker 容器数量与日志；worker 正常但不消费 → 查事件类型是否在投递白名单内、`outbox_events.attempts` 是否已达上限（标 `FAILED` 后不再重试） |
| 5 | `DdpHoneypotPaused` | `ddp_annotation_honeypot_paused > 0` | 0m | warning | 有标注员因黄金题熔断被暂停。按 `docs/ops/honeypot-appeal.md` 处理：查该标注员窗口准确率与错题分布，确认不是黄金题答案错误后，由 owner 决定 resume 还是培训。**不要批量 resume** |
| 6 | `DdpBackflowBacklog` | `ddp_annotation_backflow_received_cases > 200` | 30m | info | 回流案例积压未转换。按 `docs/ops/backflow-intake.md` 确认是否需配置 conversion 目标（`sample_set_id` + `schema_id`），或由运营批量转任务。持续积压 = 评测失败率上升，需回看模型能力 |

### 3.1 抑制规则（`alertmanager.yml` 的 `inhibit_rules`）

| 源 | 目标 | 等值标签 | 含义 |
|---|---|---|---|
| `alertname="DdpApiDown"` | `severity=~"warning\|info"` | `component` | API 整体挂了就不再逐条报依赖异常，直接看 `DdpApiDown` |

> 例外：`DdpApiDown` 本身是 critical，不在抑制目标内；`DdpOutboxStale`
> （critical）同样不受此抑制影响。

### 3.2 路由与分组（`alertmanager.yml` 的 `route`）

| 项 | 值 | 说明 |
|---|---|---|
| 默认 receiver | `default-webhook` | 指向 `http://alert-receiver:8080/alerts` |
| `group_by` | `[alertname]` | 按告警名聚合 |
| `group_wait` | 30s（critical 10s） | 首次聚合等待 |
| `group_interval` | 5m | 同组新增告警的发送间隔 |
| `repeat_interval` | 4h（critical 1h，info 24h） | 持续未恢复的重发间隔 |
| severity=info | 单独路由 | 每日汇总即可 |
| severity=critical | 单独路由 | 更短等待、更频繁重发 |

---

## 四、怎么演练（验证链路真的通）

### 4.1 方式 A：用 `amtool` 注入一条测试告警（推荐，不污染规则文件）

```bash
docker exec ddp-alertmanager amtool alert add --alertmanager.url=http://localhost:9093 \
  alertname=DdpApiDown severity=critical component=api service=ddp-api \
  --annotation='summary="演练：API 抓取失败超过 2 分钟"' \
  --annotation='description="演练告警"' \
  --generator-url="http://localhost:9190/graph"
```

> **注意**：注解值里有空格或中文标点时**必须用单引号包住整个 `key=value`**
> （`--annotation='summary="..."'`）。否则 amtool 的 UTF-8 匹配器解析器会报
> `unexpected ...: expected a comma or close brace` 并退回旧解析器。

注入后 **等 `group_wait`（critical 10s / 默认 30s）**，然后查接收端。

### 4.2 方式 B：让 Prometheus 规则真实 firing

```bash
# 临时把阈值改小，观察规则进入 PENDING → FIRING，然后热重载（无需重启）：
#   编辑 infra/monitoring/alerts.yml，把 ddp_outbox_due_events > 100 改成 > -1
curl -X POST http://localhost:9190/-/reload

# 观察
curl -s http://localhost:9190/api/v1/alerts | python3 -m json.tool
```

> 演练完**改回阈值并再次 reload**。
> 注意：`DdpApiDown` 在 api 服务未运行时**本来就会 firing**（`up{job="ddp-api"} == 0`），
> 这是最省事的真实触发方式。

### 4.3 确认接收端真收到了

```bash
# 1) 健康
curl -s http://127.0.0.1:9194/health | python3 -m json.tool

# 2) 查最近 5 条
curl -s "http://127.0.0.1:9194/alerts?limit=5" | python3 -m json.tool

# 3) 计数
curl -s http://127.0.0.1:9194/stats | python3 -m json.tool

# 4) 看落盘文件（原始证据）
docker exec ddp-alert-receiver cat /var/lib/ddp-alerts/alerts.ndjson
```

**交叉核对**（三条数字必须一致）：

| 来源 | 取值 |
|---|---|
| `GET /stats` 的 `total` | 收到的告警条数 |
| `alerts.ndjson` 的**行数** | 应与 `total` 相等 |
| `GET /alerts` 的 `count` | 应与 `total` 相等 |

### 4.4 确认 Alertmanager 真投递了（而不是接收端自己伪造）

```bash
curl -s http://127.0.0.1:9193/metrics | grep 'integration="webhook"'
```

| 指标 | 期望 |
|---|---|
| `alertmanager_notification_requests_total{integration="webhook"}` | 随演练增长 |
| `alertmanager_notification_requests_failed_total{integration="webhook"}` | **保持 0** |

```bash
# Alertmanager 侧的告警分组
curl -s http://127.0.0.1:9193/api/v2/alerts | python3 -m json.tool
# 每条应带 "receivers": [{"name": "default-webhook"}]
```

### 4.5 完整演练记录

真实演练的完整输出见
`docs/testing/evidence/2026-11-27-alert-receiver/README.md`。

---

## 五、生产接入真实下游（飞书 / 邮件 / 钉钉）

**Alertmanager 不支持环境变量插值**，URL 只能写在配置里。接入方式：

### 5.1 改 `receivers` 段（其余不动）

`infra/monitoring/alertmanager.yml`：

```yaml
receivers:
  # 飞书：机器人 Webhook（**URL 本身即凭据**，不要提交进仓库）
  - name: default-webhook
    webhook_configs:
      - url_file: /etc/alertmanager/feishu_webhook_url
        send_resolved: true

  # 或邮件
  - name: default-webhook
    email_configs:
      - to: 'oncall@example.com'
        send_resolved: true
```

> **凭据处理**：飞书机器人 Webhook URL 等同于密钥。**不要**写进
> `alertmanager.yml` 并提交——用 `url_file` 指向挂载进来的文件：
>
> ```yaml
>   volumes:
>     - ./infra/monitoring/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
>     - ddp-amdata:/alertmanager
> ```
> 把 URL 写进 compose 里 secret 文件的挂载源，文件本身加进 `.gitignore`。
> 可参考 `infra/monitoring/prometheus.prod.yml` 用 `credentials_file` 的做法。

### 5.2 保留本地接收端（推荐）

**不要删掉 `alert-receiver`**：它是「链路自证」的锚点。生产可以两者并存——
Alertmanager 的 `receivers` 段支持多个集成，同一条告警会同时发给飞书和本地接收端：

```yaml
receivers:
  - name: default-webhook
    webhook_configs:
      - url_file: /etc/alertmanager/feishu_webhook_url
        send_resolved: true
      - url: http://alert-receiver:8080/alerts   # 本地留证
        send_resolved: true
```

这样「告警发到人」和「告警留下证据」两件事互不打架。

### 5.3 切换到「只看不发」

把 `route.receiver` 改成 `default-log`（**空接收端**，无 `webhook_configs`）：
Alertmanager 自身的分组 / 路由 / 静默 / 抑制照常工作，告警分组仍可在 UI 与日志查看，
但**不会外发**。用于维护窗口或下游故障时的临时静默。

> 反面做法是「配一个连不通的 URL」——那会让 Alertmanager 反复重试失败，
> 看起来接好了其实什么都没发生（这正是本批要修掉的占位地址问题）。

### 5.4 生产还需补的（当前 **未实现**，别当已具备）

| 项 | 现状 | 说明 |
|---|---|---|
| Alertmanager 高可用 | 单实例 | 生产需双副本 + gossip |
| 告警持久化 | 内存 nflog + `ddp-amdata` 卷 | 重启后可能重发（幂等性由接收端保证） |
| 值班排班 | 无 | 飞书/钉钉机器人不做 on-call 排班 |
| 告警升级 | 无 | `repeat_interval` 只是重发，不是升级 |
| 接收端鉴权 | 无 | 内网访问；对外暴露需加反向代理与鉴权 |

---

## 六、常见故障排查

| 现象 | 排查步骤 | 常见原因 |
|---|---|---|
| 接收端 `/health` 不通 | `docker compose --profile monitoring ps`；`docker logs ddp-alert-receiver` | 容器未起 / 端口被占（9194） |
| Alertmanager 里没有告警 | `curl -s http://127.0.0.1:9193/api/v2/alerts` | Prometheus 没把告警推过来 → 查 Prometheus `/api/v1/alerts` 状态；`up{job="ddp-api"}==1` 时 `DdpApiDown` 本就不该 firing |
| 规则一直 pending | `curl -s http://127.0.0.1:9190/api/v1/rules` 看 `state` 与 `for` | 未到 `for` 时长；或表达式无数据 |
| 规则一直 inactive | 检查 `expr` 里的**指标名**是否与 `backend/app/core/metrics.py` 一致 | 指标改名而告警没跟着改（**沉默的失效**，`backend/tests/unit/test_monitoring_config.py` 专门防这个） |
| Alertmanager 有告警，接收端没有 | `curl -s http://127.0.0.1:9193/metrics \| grep 'integration="webhook"'` 看 `failed_total` | 接收端挂了 / DNS 解析不到 `alert-receiver` / 响应非 2xx → 看 `docker logs ddp-alertmanager` 的 `Notify attempt failed` |
| 接收端 `/alerts` 返回 400 | 看日志里的 `error` 字段 | Content-Type 不是 `application/json`；或 body 不是合法 JSON |
| 接收端返回 413 | 同上 | body 超过 1 MiB 上限 |
| 告警发了但 `stats` 计数对不上 | `wc -l` 数 NDJSON 行数 vs `stats.total` | 文件里有坏行（极端并发下的半行）——坏行被跳过，其余仍可查 |
| 接收端日志看不到来源 IP | 设计如此 | 覆写了 `address_string`，只记内容不记来源 |

### 6.1 接收端挂掉时会发生什么（**已实测**）

| 环节 | 行为 |
|---|---|
| Alertmanager | **不停止**（`depends_on` 用 `service_started` 而非 `service_healthy`），继续重试投递，日志出现 `Notify attempt failed, will retry later` |
| 告警是否丢 | **不丢**：接收端恢复后重试补投（实测补投成功，`stats.total` 从 4 变 5） |
| 为什么这样设计 | 若用 `service_healthy`，接收端一坏 → Alertmanager 一起不启动 → **「接收端坏了」这件事本身反而没人知道告警** |

---

## 七、已知边界

- **单机单副本**：接收端用 `ThreadingHTTPServer`，多副本需自行处理卷共享与去重；
- **不去重**：Alertmanager 的 `repeat_interval` 重发会**原样再落一条**（接收端是证据记录器，不是告警去重器）；
- **存储无上限**：NDJSON 只增不减，卷占用需自行监控与轮转；
- **无鉴权**：仅内网使用；
- **单条上限 1 MiB**：超大告警组（`truncatedAlerts` 机制下 Alertmanager 会截断）不受影响，但自造的超大 body 会得 413。

---

## 八、相关文件

| 文件 | 作用 |
|---|---|
| `infra/monitoring/alerts.yml` | 6 条告警规则（权威来源） |
| `infra/monitoring/alertmanager.yml` | 路由 / 分组 / 抑制 / 接收端 |
| `infra/monitoring/prometheus.yml` | 抓取配置（本地） |
| `infra/monitoring/prometheus.prod.yml` | 抓取配置（生产，带 Bearer 令牌） |
| `services/alert-receiver/` | 告警接收端（标准库实现） |
| `docker-compose.yml` | `monitoring` profile 的三个服务 + `ddp-alertdata` 卷 |
| `docs/ops/monitoring-setup.md` | 监控栈部署（更偏「怎么起起来」） |
| `docs/ops/honeypot-appeal.md` | 第 5 条规则（熔断申诉）的处置流程 |
| `docs/ops/backflow-intake.md` | 第 6 条规则（回流）的处置流程 |
| `docs/testing/evidence/2026-11-27-alert-receiver/` | 本链路的真实演练证据 |
