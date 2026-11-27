# DDP 告警真实接收端（services/alert-receiver）

> 审计缺口：**「告警未接真实 Alertmanager 实例」**——`infra/monitoring/alertmanager.yml`
> 的 webhook receiver 指向占位地址 `http://127.0.0.1:9/ddp-alerts`（永远连不通），
> 于是「规则能被 Prometheus 求值」与「告警真的发得出去」之间是断的。

本服务就是那个**真实接收端**：Alertmanager 路由 → 分组 → HTTP POST → **落盘留证**
→ 可查询、可复核。

---

## 1. 定位与边界

| 项 | 说明 |
|---|---|
| 职责 | 接收 Alertmanager webhook payload，落盘为 NDJSON，提供查询与统计 |
| 技术栈 | **Python 标准库**（`http.server` / `json` / `fcntl` / `threading`），**零第三方依赖** |
| 不负责 | 告警的判定、分组、路由（那是 Prometheus / Alertmanager 的职责） |
| 不负责 | 通知到人（飞书 / 邮件 / 钉钉）——生产接入见 `docs/ops/alerting-runbook.md` |

**为什么不用 FastAPI**：接收端是「最后一公里」，依赖越少越不容易在出事时自己先挂掉；
本服务只有 2 个源文件，不需要框架能力。镜像固定 `python:3.11-slim` digest，
且**不装任何 pip 包**。

---

## 2. 端点

| 方法 | 路径 | 用途 | 状态码 |
|---|---|---|---|
| POST | `/alerts` | 接收 Alertmanager webhook payload | 200 / 400 / 413 / 500 |
| GET | `/alerts` | 列出已收到的告警（JSON 数组，**最新在前**，支持 `?limit=N`） | 200 |
| GET | `/health` | 健康检查（compose healthcheck 用） | 200 |
| GET | `/stats` | 计数统计（总数 / firing / resolved / 按 alertname 分组） | 200 |

### 2.1 `POST /alerts`

请求头要求：

- `Content-Type: application/json`（**必须**；Alertmanager 的固定行为）。不合法 → **400**；
- `Content-Length` 必须存在（不支持 chunked 传输）→ 缺失则 **400**；
- 请求体 **超过 1 MiB** → **413**（不分配内存直接拒绝）。

请求体为 Alertmanager 的 **webhook v4 格式**：

```json
{
  "version": "4",
  "groupKey": "{}:{alertname=\"DdpApiDown\"}",
  "status": "firing",
  "receiver": "default-webhook",
  "groupLabels": {"alertname": "DdpApiDown"},
  "commonLabels": {"alertname": "DdpApiDown", "severity": "critical", "component": "api"},
  "commonAnnotations": {"summary": "...", "description": "..."},
  "externalURL": "http://localhost:9193",
  "alerts": [
    {
      "status": "firing",
      "labels": {"alertname": "DdpApiDown", "severity": "critical", "component": "api"},
      "annotations": {"summary": "...", "description": "..."},
      "startsAt": "2026-11-27T10:00:00.000Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://localhost:9190/graph?g0.expr=...",
      "fingerprint": "1a2b3c4d"
    }
  ]
}
```

成功响应：

```json
{"status": "accepted", "stored": true}
```

### 2.2 `GET /alerts`

```json
{
  "count": 1,
  "alerts": [ { "version": "4", "status": "firing", "...": "..." } ]
}
```

`alerts` 数组里的每一项就是**原样落盘的一条 webhook payload**（不裁剪、不改写）。
按落盘时间**倒序**（最新在前）。`?limit=N` 取最近 N 条。

### 2.3 `GET /stats`

```json
{
  "total": 6,
  "firing": 5,
  "resolved": 1,
  "unknown_status": 0,
  "by_alertname": {"DdpApiDown": 1, "DdpOutboxStale": 1}
}
```

---

## 3. 落盘格式与保证

- **路径**：`$ALERT_RECEIVER_DIR/alerts.ndjson`（默认 `/var/lib/ddp-alerts/alerts.ndjson`）；
- **格式**：NDJSON——**每条告警一行**，一行一个完整 JSON 对象；
- **写入方式**：**追加**（`mode="a"`），绝不截断既有证据；
- **并发安全**：`fcntl.flock` 排他锁包住「追加 + flush + fsync」，多进程/多线程并发写
  不会产生半行交错；
- **崩溃安全**：每次追加后 `flush()` + `fsync()`——告警是「证据」，进程崩溃也不丢；
- **查询为准**：查询接口**从落盘文件读回**，不依赖内存缓存。
  进程重启后仍可查询（这是「留下证据」的核心）。

**坏行容忍**：读回时若某一行不是合法 JSON（极端并发下的半行），该行被跳过，
其余记录仍可查询——宁可少一条，也不让一行坏数据毁掉全部可查性。

---

## 4. 安全约束

| 约束 | 实现 |
|---|---|
| 不记录凭据 | 不打印请求头、不落盘任何 token；日志只记 `alertname` 与 `status` |
| 不记录来源 IP | 覆写 `BaseHTTPRequestHandler.address_string`，访问日志里的来源一律显示 `-` |
| 请求体上限 | 1 MiB，超限 413 |
| Content-Type 校验 | 非 `application/json` 一律 400 |
| 空闲连接保护 | socket 超时 30s；丢弃残余 body 超时 5s |

---

## 5. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ALERT_RECEIVER_DIR` | `/var/lib/ddp-alerts` | 落盘目录 |
| `ALERT_RECEIVER_HOST` | `0.0.0.0` | 监听地址 |
| `ALERT_RECEIVER_PORT` | `8080` | 监听端口 |
| `ALERT_RECEIVER_LOG_LEVEL` | `INFO` | 日志级别 |

---

## 6. 接入方式

### 6.1 docker compose（推荐）

`docker-compose.yml` 里的 `monitoring` profile 已含本服务：

```bash
docker compose --profile monitoring up -d --build alert-receiver alertmanager prometheus
```

服务名 `alert-receiver`，容器名 `ddp-alert-receiver`，宿主端口 `9194`，
命名卷 `ddp-alertdata` 挂在 `/var/lib/ddp-alerts`。

### 6.2 本地直接跑

```bash
cd services/alert-receiver
ALERT_RECEIVER_DIR=/tmp/ddp-alerts python3 -m app.main
```

### 6.3 手工投递一条告警（自检用）

```bash
curl -sS -X POST http://127.0.0.1:9194/alerts \
  -H 'Content-Type: application/json' \
  -d '{"version":"4","status":"firing","commonLabels":{"alertname":"DdpApiDown"},"alerts":[]}'
```

---

## 7. 测试

```bash
python3 -m pytest services/alert-receiver/tests -q
```

覆盖：POST 正常落盘 + GET 查得到、非法 Content-Type → 400、超大 body → 413、
NDJSON 每行一个 JSON、`/health` → 200、`/stats` 计数、`?limit=N`、
坏行不毁全局、进程重启后仍可查、撒谎 Content-Length 不挂死。

---

## 8. 已知边界

- **单机单进程**：用 `ThreadingHTTPServer`，多副本部署需自行处理卷共享与去重；
- **不重试、不去重**：Alertmanager 侧的 `repeat_interval` 重发会**原样再落一条**
  （本服务是证据记录器，不是告警去重器）；
- **无鉴权**：监听在 compose 内网（`alert-receiver:8080`），
  宿主端口 `9194` 仅供本地观察，**生产不应把该端口暴露到公网**；
- **存储无上限**：NDJSON 只会增长，卷占用需自行监控与轮转。
