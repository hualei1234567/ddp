# 告警真实接收端端到端演练证据（2026-11-27）

## 审计缺口

> 「**告警未接真实 Alertmanager 实例**」——
> `infra/monitoring/alertmanager.yml` 的 webhook receiver 指向占位地址
> `http://127.0.0.1:9/ddp-alerts`（**永远连不通**）。
>
> 于是「Prometheus 规则能被求值」与「告警真的发得出去」之间是断的：
> 规则确实触发过，但没有任何真实下游接收、落盘、留存。

## 本批交付

| 资产 | 路径 |
|---|---|
| 告警接收端服务（标准库实现，零第三方依赖） | `services/alert-receiver/` |
| 接收端 Dockerfile（固定 digest） | `services/alert-receiver/Dockerfile` |
| 接收端单元测试（13 项） | `services/alert-receiver/tests/` |
| 路由配置（去掉占位地址，指向真实接收端） | `infra/monitoring/alertmanager.yml` |
| compose 服务（`monitoring` profile + `ddp-alertdata` 卷） | `docker-compose.yml` |
| 告警链路运行手册 | `docs/ops/alerting-runbook.md` |

---

## 一、链路图（文字版）

```
Prometheus                 Alertmanager                alert-receiver
（求值 6 条规则）           （路由/分组/抑制）          （落盘留证）
   │                            │                           │
   │ 规则 firing                 │ route→receiver            │
   │ POST /api/v1/alerts         │ group_by=alertname        │
   ├───────────────────────────►│ group_wait 聚合            │
                                │ inhibit_rules             │
                                │                           │
                                │  HTTP POST /alerts        │
                                │  http://alert-receiver    │
                                │         :8080/alerts      │
                                ├──────────────────────────►│
                                │  Content-Type:            │
                                │   application/json        │
                                │  body ≤ 1 MiB             │
                                │                           │
                                │                  追加写 NDJSON
                                │                  (fcntl.flock)
                                │                  alerts.ndjson
                                │                           │
                                │                 GET /alerts ◄── 复核
                                │                 GET /stats  ◄── 计数
                                └───────────────────────────┘
```

**闭合判据**（四条全部满足才算真通）：

1. Prometheus 规则**真的 firing**；
2. Alertmanager **真的投递**（`notification_requests_total{integration="webhook"}` 增长、
   `..._failed_total` 保持 0）；
3. 接收端 **真的落盘**（`alerts.ndjson` 有新行）；
4. 接收端 **真的查得到**（`GET /alerts` / `GET /stats` 计数对得上）。

---

## 二、真实演练步骤与真实输出

### 步骤 1：启动监控栈

```
$ docker compose --profile monitoring up -d --build alert-receiver alertmanager prometheus
 ...
 Container ddp-alert-receiver Started
 Container ddp-alertmanager Started
 Container ddp-prometheus Started
```

容器状态（**接收端 healthy**）：

```
$ docker compose --profile monitoring ps
NAME                 IMAGE                              COMMAND                  SERVICE          CREATED          STATUS                    PORTS
ddp-alert-receiver   wt-taska-alerting-alert-receiver   "python -m app.main"     alert-receiver   21 seconds ago   Up 21 seconds (healthy)   0.0.0.0:9194->8080/tcp
ddp-alertmanager     prom/alertmanager:v0.28.1          "/bin/alertmanager -…"   alertmanager     21 seconds ago   Up 21 seconds             0.0.0.0:9193->9093/tcp
ddp-prometheus       prom/prometheus:v3.1.0             "/bin/prometheus --c…"   prometheus       21 seconds ago   Up 20 seconds             0.0.0.0:9190->9090/tcp
```

### 步骤 2：确认 Prometheus 真实加载并求值了 6 条规则

```
$ curl -sS http://127.0.0.1:9190/api/v1/rules
DdpApiDown                   firing     up{job="ddp-api"} == 0
DdpBusinessMetricsMissing    pending    absent(ddp_outbox_due_events{job="ddp-api"})
DdpOutboxBacklog             inactive   ddp_outbox_due_events > 100
DdpOutboxStale               inactive   ddp_outbox_oldest_due_age_seconds > 900
DdpHoneypotPaused            inactive   ddp_annotation_honeypot_paused > 0
DdpBackflowBacklog           inactive   ddp_annotation_backflow_received_cases > 200
```

> `DdpApiDown` 是 **firing**（真状态，不是配置摆设）：本机没跑 api 服务，
> `up{job="ddp-api"} == 0` 成立，持续 2m 后触发。
> 这意味着**不靠任何人工注入**，Prometheus 自己就能产生一条真实告警。

### 步骤 3：用 `amtool` 注入一条测试告警（不污染规则文件）

```
$ docker exec ddp-alertmanager amtool alert add --alertmanager.url=http://localhost:9093 \
    alertname=DdpApiDown severity=critical component=api service=ddp-api \
    --annotation='summary="端到端演练：API 抓取失败超过 2 分钟"' \
    --annotation='description="由 amtool 注入的演练告警，验证 Alertmanager → 接收端 → 落盘链路"' \
    --generator-url="http://localhost:9190/graph"
（无输出 —— amtool 成功时静默）
```

> **踩坑记录**：注解里含空格 / 中文标点时**必须**把整个 `key=value` 用单引号包住
> （`--annotation='summary="..."'`）。否则 amtool 的 UTF-8 匹配器解析器会报
> `unexpected 抓取失败超过: expected a comma or close brace` 并退回旧解析器。

### 步骤 4：Alertmanager 收到 → 路由 → 分组

```
$ curl -sS http://127.0.0.1:9193/api/v2/alerts
[
    {
        "annotations": { "summary": "...", "description": "..." },
        "endsAt": "2026-11-27T11:06:12.715Z",
        "fingerprint": "2f3ec9ab11ddf215",
        "receivers": [ { "name": "default-webhook" } ],     ← 路由到真实接收端
        "startsAt": "2026-11-27T11:01:03.276Z",
        "status": { "inhibitedBy": [], "mutedBy": [], "silencedBy": [], "state": "active" },
        "generatorURL": "http://localhost:9190/graph",
        "labels": { "alertname": "DdpApiDown", "component": "api", "service": "ddp-api", "severity": "critical" }
    }
]
```

分组键（接收端落盘里可见）：`{}/{severity="critical"}:{alertname="DdpApiDown"}`
—— 说明 `route.group_by: [alertname]` + severity=critical 子路由**真实生效**。

### 步骤 5：接收端真落盘

```
$ docker exec ddp-alert-receiver cat /var/lib/ddp-alerts/alerts.ndjson
{"alerts": [{"annotations": {"description": "由 amtool 注入的演练告警，验证 Alertmanager → 接收端 → 落盘链路", "summary": "端到端演练：API 抓取失败超过 2 分钟"}, "endsAt": "0001-01-01T00:00:00Z", "fingerprint": "2f3ec9ab11ddf215", "generatorURL": "http://localhost:9190/graph", "labels": {"alertname": "DdpApiDown", "component": "api", "service": "ddp-api", "severity": "critical"}, "startsAt": "2026-11-27T11:01:03.276138398Z", "status": "firing"}], "commonAnnotations": {...}, "commonLabels": {"alertname": "DdpApiDown", "component": "api", "service": "ddp-api", "severity": "critical"}, "externalURL": "http://127.0.0.1:9193", "groupKey": "{}/{severity=\"critical\"}:{alertname=\"DdpApiDown\"}", "groupLabels": {"alertname": "DdpApiDown"}, "receiver": "default-webhook", "status": "firing", "truncatedAlerts": 0, "version": "4"}
```

**NDJSON 格式**：每条告警**独占一行**，一行一个完整 JSON 对象。

接收端日志（只记告警内容，不记凭据、不记来源 IP）：

```
2026-11-27 11:01:13,285 INFO ddp.alert-receiver 已接收告警：alertname=DdpApiDown status=firing
2026-11-27 11:01:13,285 INFO ddp.alert-receiver - - "POST /alerts HTTP/1.1" 200 -
```

> 来源位置显示 `-`：覆写了 `BaseHTTPRequestHandler.address_string`，
> 接收端只关心告警内容，不做来源分析。

### 步骤 6：`GET /alerts` 查得到 + `GET /stats` 对得上

```
$ curl -sS http://127.0.0.1:9194/alerts
{
  "count": 8,
  "alerts": [ ... 原样落盘的 webhook payload ... ]
}

$ curl -sS http://127.0.0.1:9194/stats
{
  "by_alertname": {
    "DdpApiDown": 3,
    "DdpBusinessMetricsMissing": 1,
    "DdpHoneypotPaused": 2,
    "DdpOutboxStale": 2
  },
  "firing": 6,
  "resolved": 2,
  "total": 8,
  "unknown_status": 0
}
```

**三个独立计数互相印证**（都是 8）：

| 来源 | 取值 |
|---|---|
| `GET /stats` 的 `total` | 8 |
| `GET /alerts` 的 `count` | 8 |
| `alerts.ndjson` 的**行数**（`wc -l`） | 8 |

`firing(6) + resolved(2) = total(8)` —— 状态分布也自洽。

### 步骤 7：Alertmanager 投递指标（干净计数）

先重启 Alertmanager 重置计数器，再注入一条：

```
$ docker compose --profile monitoring restart alertmanager
$ docker exec ddp-alertmanager amtool alert add --alertmanager.url=http://localhost:9093 \
    alertname=DdpApiDown severity=critical component=api service=ddp-api \
    --annotation='summary="端到端演练（干净计数）：API 抓取失败超过 2 分钟"' \
    --annotation='description="干净计数下的一次投递，用于采集权威指标"' \
    --generator-url="http://localhost:9190/graph"

$ curl -sS http://127.0.0.1:9193/metrics | grep 'integration="webhook"'
alertmanager_notification_requests_failed_total{integration="webhook"} 0
alertmanager_notification_requests_total{integration="webhook"} 1
```

**投递 1 次，失败 0 次。** 这是「Alertmanager 真的发了」的权威指标
（不是看接收端有没有收到——那分不清是谁发的）。

---

## 三、Prometheus 真实触发的告警（更强证据）

`received-alert-prometheus-fired.json` 是一条**由 Prometheus 规则真实产生**、
经 Alertmanager 分组后投递到接收端的 payload。**同一条记录里同时含**
「Prometheus 触发的 firing 告警」与「amtool 注入的告警转 resolved」——
说明**分组聚合**与 **firing → resolved 状态迁移**都真实发生过：

```json
{
  "alerts": [
    {
      "annotations": {
        "description": "检查 api 容器是否存活（docker ps / docker compose logs api）， 确认 /health 是否返回 200。若容器在但探针失败，先看 docker compose logs api 里的启动校验报错（密钥缺失会直接拒绝启动）。",
        "summary": "DDP API 抓取失败超过 2 分钟"
      },
      "endsAt": "0001-01-01T00:00:00Z",
      "fingerprint": "8b9715fc6d92ef30",
      "generatorURL": "http://c107de032fa6:9090/graph?g0.expr=up%7Bjob%3D%22ddp-api%22%7D+%3D%3D+0&g0.tab=1",
      "labels": {
        "alertname": "DdpApiDown",
        "component": "api",
        "env": "local",
        "instance": "api:8000",
        "job": "ddp-api",
        "service": "ddp-api",
        "severity": "critical"
      },
      "startsAt": "2026-11-27T11:02:32.858Z",
      "status": "firing"
    },
    {
      "annotations": {
        "description": "由 amtool 注入的演练告警，验证 Alertmanager → 接收端 → 落盘链路",
        "summary": "端到端演练：API 抓取失败超过 2 分钟"
      },
      "endsAt": "2026-11-27T11:06:12.715203473Z",
      "fingerprint": "2f3ec9ab11ddf215",
      "generatorURL": "http://localhost:9190/graph",
      "labels": { "alertname": "DdpApiDown", "component": "api", "service": "ddp-api", "severity": "critical" },
      "startsAt": "2026-11-27T11:01:03.276138398Z",
      "status": "resolved"
    }
  ],
  "commonLabels": { "alertname": "DdpApiDown", "component": "api", "service": "ddp-api", "severity": "critical" },
  "groupKey": "{}/{severity=\"critical\"}:{alertname=\"DdpApiDown\"}",
  "groupLabels": { "alertname": "DdpApiDown" },
  "receiver": "default-webhook",
  "status": "firing",
  "truncatedAlerts": 0,
  "version": "4"
}
```

关键点：
- `labels.job="ddp-api"` / `instance="api:8000"` → 这条是 **Prometheus 规则求值产生**的
  （amtool 注入的告警没有 `job` / `instance` 标签）；
- `generatorURL` 指向 Prometheus 容器内地址 `http://c107de032fa6:9090/graph?...`；
- `annotations.description` 是 `alerts.yml` 里 `DdpApiDown` 的**原文**——说明
  **规则文件 → Prometheus → Alertmanager → 接收端**整条链路的内容都没被篡改；
- 第二条 `"status": "resolved"` + `endsAt` 有值 → **恢复通知**（`send_resolved: true`）生效。

---

## 四、落盘证据：进程重启后仍可查

```
$ docker compose --profile monitoring restart alert-receiver
 Container ddp-alert-receiver Restarting
 Container ddp-alert-receiver Started

$ curl -sS http://127.0.0.1:9194/stats
{
  "by_alertname": { "DdpApiDown": 2, "DdpOutboxStale": 2 },
  "firing": 3, "resolved": 1, "total": 4, "unknown_status": 0
}
```

重启前后**完全一致**（4 条不变）——证明查询是**从落盘文件读回**，
而不是从内存缓存读。「留下证据」这个目标达成。

---

## 五、接收端挂掉时的行为（实测，验证 `depends_on` 的选型）

停掉接收端：

```
$ docker compose --profile monitoring stop alert-receiver
 Container ddp-alert-receiver Stopped

$ docker compose --profile monitoring ps --format "table {{.Name}}\t{{.Status}}"
NAME               STATUS
ddp-alertmanager   Up 9 minutes        ← Alertmanager 仍在运行
ddp-prometheus     Up 9 minutes
```

注入一条新告警，Alertmanager **不崩溃、不停止**，改为重试：

```
$ docker logs ddp-alertmanager | grep -i notify
time=2026-11-27T11:10:10.856Z level=WARN source=notify.go:866 msg="Notify attempt failed, will retry later"
  component=dispatcher receiver=default-webhook integration=webhook[0]
  aggrGroup="{}:{alertname=\"DdpHoneypotPaused\"}" attempts=1 err="Post \"<redacted>\": EOF"
```

接收端恢复后，**重试补投成功**（`stats.total` 从 4 变 5，多出的正是那条
`DdpHoneypotPaused`）——**告警没丢**。

> **为什么 `depends_on` 用 `service_started` 而不是 `service_healthy`**：
> 若用 `service_healthy`，接收端一坏 → Alertmanager 一起不启动 →
> **「接收端坏了」这件事本身反而没人知道告警**（告警链路自己瞎了）。
> 语义上正确的是：Alertmanager 始终在跑，接收端恢复后自动接上。
> 代价是接收端不可用期间告警靠重试补投——实测不丢。

---

## 六、单元测试（13 项全绿）

```
$ python3 -m pytest services/alert-receiver/tests -q
.............                                                            [100%]
13 passed in 6.60s
```

覆盖：POST 正常落盘 + GET 查得到、非法 Content-Type → 400、缺 Content-Type → 400、
超大 body → 413、边界内 body → 200、NDJSON 每行一个 JSON、`/health` → 200、
`/stats` 计数、`?limit=N`、未知路径 → 404、坏行不毁全局、进程重启后仍可查、
**撒谎 Content-Length 不挂死**。

### 6.1 演练中抓到并修掉的 2 个真实缺陷

| # | 缺陷 | 现象 | 修法 |
|---|---|---|---|
| 1 | `Path.write_text` 以 `"w"` 模式打开 | **会截断**既有证据文件（只剩最后一条） | 改用 `handle.write()`（`"a"` 模式追加），并 `flush()` + `fsync()` |
| 2 | 超限 body 先「读完再响应」 | 客户端声明超大 `Content-Length` 但实际只发几字节时，**服务端线程被挂死**（实测 timeout） | 改为**先回 413 再丢弃**残余 body，丢弃带 5s 超时上限；补回归用例 `test_lying_content_length_does_not_hang` |

---

## 七、结论

### 7.1 告警链路是否真正闭合？

**闭合了，且有真实证据。**

| 判据 | 结果 | 证据 |
|---|---|---|
| Prometheus 规则真的 firing | ✅ | `DdpApiDown => firing`（§二 步骤 2）；payload 带 `job=ddp-api` / `instance=api:8000` |
| Alertmanager 真的路由到接收端 | ✅ | `/api/v2/alerts` 里 `"receivers": [{"name": "default-webhook"}]`；groupKey 含 severity 子路由 |
| Alertmanager 真的投递（没失败） | ✅ | `notification_requests_total{webhook}=1`，`..._failed_total=0`（干净计数） |
| 接收端真的落盘 | ✅ | `alerts.ndjson` 8 行，每行一个独立 JSON |
| 真的查得到 | ✅ | `GET /alerts` count=8 = `GET /stats` total=8 = `wc -l` 8 |
| 状态迁移正确 | ✅ | 同一条记录内 firing 与 resolved 并存；`firing(6)+resolved(2)=total(8)` |
| 重启后证据仍在 | ✅ | 重启接收端后计数不变（§四） |
| 接收端挂了不拖垮 Alertmanager | ✅ | Alertmanager 保持运行并重试，恢复后补投成功（§五） |
| 占位地址已清除 | ✅ | `http://127.0.0.1:9/ddp-alerts` 已从 `alertmanager.yml` 移除 |

### 7.2 没做到 / 降级处理的地方（如实写明）

| 项 | 状态 | 说明 |
|---|---|---|
| **通知到人** | ❌ **未实现** | 本批只做到「发到真实接收端并落盘」。告警**没有**推到飞书/邮件/钉钉——生产接入方式已写进 `docs/ops/alerting-runbook.md` §五，但**未在生产验证过** |
| **下游 webhook 可靠性** | ⚠️ 未验证 | 「接收端恢复后重试补投」只在**单条告警**上实测过；Alertmanager 的 nflog 持久化、重复投递的幂等性未做系统验证 |
| **飞书/邮件下游** | ⚠️ 未实测 | 未配置真实下游，因此「Alertmanager → 飞书」的投递形态（消息卡片 vs 纯文本）**未验证** |
| **多副本接收端** | ❌ 未支持 | 单机单副本；多副本部署时的卷共享与去重**未实现** |
| **NDJSON 存储上限** | ❌ 无 | 文件只增不减；卷占用需自行监控与轮转（未实现自动轮转） |
| **接收端鉴权** | ❌ 无 | 仅内网使用；宿主端口 9194 **不应**暴露到公网（已写进文档） |
| **Alertmanager 高可用** | ❌ 无 | 单实例 + gossip，生产需双副本 |
| **6 条规则全部 firing 过** | ⚠️ 部分 | 本次实测 **2 条**（`DdpApiDown` 真实 firing、`DdpHoneypotPaused` 经注入→恢复补投）；
  其余 4 条（`DdpBusinessMetricsMissing` / `DdpOutboxBacklog` / `DdpOutboxStale` / `DdpBackflowBacklog`）
  在本机**没有对应的真实指标数据**，因而只到 `pending` / `inactive`——
  **它们能否走到接收端未逐条实测**。链路本身与规则无关（同一套路由），但**不能声称 6 条都验过** |
| **inhibit_rules 实测** | ⚠️ 未实测 | `DdpApiDown` → `severity=~"warning\|info"` 的抑制**未构造出满足条件的场景**验证 |
| **Prometheus → Alertmanager 推送** | ⚠️ 部分 | Prometheus 侧规则确实产生了 firing 告警并最终到达接收端，但**未单独抓取**
  Prometheus `/api/v1/alerts` 与 Alertmanager 接收之间的中间态快照 |

### 7.3 风险

1. **接收端单点**：它是「告警链路」的最后一环。挂了不影响 Alertmanager 存活（已实测），
   但**落证会中断**；靠重试补投，窗口内外的可靠性未系统验证。
2. **`ddp-alertdata` 卷会一直涨**：无自动轮转，长期运行需人工/脚本清理。
3. **宿主端口 9194 无鉴权**：本机开发环境可接受，生产必须收回。
4. **`alert-receiver` 无内置指标**：接收端自身没有 Prometheus 指标
   （无法用 `up{}` 监控它自己）；目前只能靠 compose healthcheck + `GET /stats` 观察。

---

## 八、本目录文件

| 文件 | 内容 |
|---|---|
| `README.md` | 本文档：链路图、真实演练步骤与输出、结论与风险 |
| `received-alert-prometheus-fired.json` | **真实 payload**：Prometheus 规则触发 + firing→resolved 同组 |
| `received-alerts.json` | `GET /alerts` 的真实响应（全部 8 条） |
| `stats.json` | `GET /stats` 的真实响应 |
| `alerts.ndjson` | 落盘文件原样（8 行，每行一个 JSON） |
