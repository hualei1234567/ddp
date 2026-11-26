# 监控与告警落地证据（阶段 4 / 审计 R2）

## 审计缺口

> 「指标已暴露、告警规则已写，但**规则从未加载到真实 Prometheus**——有指标没人被叫醒等于没有告警。」

## 本批交付

| 资产 | 路径 |
|---|---|
| 本地抓取配置（无认证，`DDP_ENV=local` 放行） | `infra/monitoring/prometheus.yml` |
| 生产抓取配置（Bearer 令牌） | `infra/monitoring/prometheus.prod.yml` |
| 告警规则（6 条） | `infra/monitoring/alerts.yml` |
| 告警路由 | `infra/monitoring/alertmanager.yml` |
| compose 服务（`monitoring` profile） | `docker-compose.yml` |
| 部署手册 | `docs/ops/monitoring-setup.md` |
| 配置漂移守护 | `backend/tests/unit/test_monitoring_config.py`（6 项） |

## 验证记录（真实工具、真实进程）

### 1–2. 配置与规则语法

```
$ promtool check config /etc/prometheus/prometheus.yml
Checking /etc/prometheus/alerts.yml
  SUCCESS: 6 rules found

$ promtool check rules /etc/prometheus/alerts.yml
  SUCCESS: 6 rules found
```

**抓到并修复 1 个真实错误**：初版 `DdpMetricsScrapeFailing` 用
`up{...} == 0 and on() (time() - 0) > 0`，promtool 报
`comparisons between scalars must use BOOL modifier`。改为
`absent(ddp_outbox_due_events{job="ddp-api"})` 后通过，语义也更准确：
区分「API 挂了」（`DdpApiDown`）与「API 活着但指标采集器异常」。

### 3. Alertmanager 配置

```
$ amtool check-config /etc/alertmanager/alertmanager.yml
 - 1 receivers
 - 0 templates
```

### 4. 真实 Prometheus 加载规则并求值（关键证据）

启动 `prom/prometheus:v3.1.0` 加载本仓库配置，查询 `/api/v1/rules`：

| 规则 | 状态 |
|---|---|
| `DdpApiDown` | inactive（API 未在 compose 内运行，正确） |
| `DdpBusinessMetricsMissing` | **pending**（业务指标确实缺席 → 规则真实求值） |
| `DdpOutboxBacklog` | inactive（指标存在且未超阈值） |
| `DdpOutboxStale` | inactive |
| `DdpHoneypotPaused` | inactive |
| `DdpBackflowBacklog` | inactive |

`DdpBusinessMetricsMissing` 进入 **pending** 是关键：不是静态加载成功，
而是 Prometheus 真的对时间序列做了判断。

### 5. 指标文本格式

```
$ promtool check metrics < /metrics-snapshot.txt
exit=0
```

72 行输出、40 个 `ddp_*` 样本。告警引用的 4 个业务指标在真实 API 上都有值：

```
ddp_outbox_due_events 0.0
ddp_outbox_oldest_due_age_seconds 0.0
ddp_annotation_honeypot_paused 0.0
ddp_annotation_backflow_received_cases 1.0
```

### 6. 配置漂移守护（6 项测试）

1. 两份抓取配置都指向 `api:8000` + `/metrics`，规则文件与 alertmanager 目标已挂载
2. 生产配置带 Bearer 令牌；本地配置不带
3. 每条规则含 `for`/`severity`/`summary`/`description`，名称唯一
4. **告警 expr 引用的 `ddp_*` 指标必须真实存在于 `metrics.py`**
5. Alertmanager 默认接收端与路由引用一致
6. compose 监控服务带 `monitoring` profile、挂配置、暴露端口、命名卷已声明

## 诚实边界

- **抓取端到端未在本机验证**：Docker Desktop（macOS）容器经
  `host.docker.internal` 访问宿主 uvicorn 实测 EOF。已用两条独立证据替代：
  真实 Prometheus **加载规则并求值**（pending）+ `promtool check metrics`
  校验真实 `/metrics` 输出。正常 compose 网络（api 与 prometheus 同网络）
  下配置写法一致。
- **未验证告警到外部接收端的投递**：`alertmanager.yml` 的 webhook 是占位地址。
- 无 HA：Prometheus/Alertmanager 各单实例。
- worker 无独立指标端点：8 个 worker 只能看进程存活与共享 outbox 指标。
