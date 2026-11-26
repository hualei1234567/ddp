# 监控与告警部署（阶段 4 / 审计 R2）

> 审计结论：指标已暴露、告警规则已写，但**规则从未加载到真实 Prometheus**——
> "有指标没人被叫醒等于没有告警"。本文是把它真正跑起来的操作手册。

## 一、能力现状

| 能力 | 位置 | 状态 |
|---|---|---|
| 指标输出 | `GET /metrics`（根级，Bearer 令牌鉴权，`app/api/observability.py`） | ✅ 已上线 |
| 业务指标 | `backend/app/core/metrics.py`（15 个指标） | ✅ 已上线 |
| 深健康检查 | `GET /api/v1/health/dependencies` | ✅ 已上线 |
| 抓取配置 | `infra/monitoring/prometheus.yml`（本地）/ `prometheus.prod.yml`（生产） | ✅ 本批新增 |
| 告警规则 | `infra/monitoring/alerts.yml`（5 条） | ✅ 本批新增 |
| 告警路由 | `infra/monitoring/alertmanager.yml` | ✅ 本批新增 |
| compose 服务 | `prometheus` + `alertmanager`（`monitoring` profile） | ✅ 本批新增 |
| 配置漂移守护 | `backend/tests/unit/test_monitoring_config.py`（6 项） | ✅ 本批新增 |

## 二、本地启动（3 步）

```bash
# 1) 起数据面 + API（API 必须起来，否则抓取目标永远 down）
docker compose --profile app up -d

# 2) 起监控栈
docker compose --profile monitoring up -d prometheus alertmanager

# 3) 验证抓取成功
curl -s http://localhost:9190/api/v1/targets | python3 -m json.tool | grep -E '"health"|"scrapeUrl"'
# 期望：{"health": "up", ...} 与 "scrapeUrl": "http://api:8000/metrics"

# 验证指标有数据（任选一个业务指标）
curl -s -H "Authorization: Bearer $DDP_METRICS_ACCESS_TOKEN" localhost:8000/metrics | grep ddp_outbox
```

| 界面 | 地址 |
|---|---|
| Prometheus（查询/目标/告警状态） | http://localhost:9190 |
| Alertmanager（告警分组/静默） | http://localhost:9193 |

## 三、验证告警"真能触发"（审计 R2 的验收点）

规则存在 ≠ 规则有效。用一条**可控**的方式验证：

```bash
# 方式 A（推荐）：把阈值临时改小，观察规则进入 PENDING → FIRING
#   编辑 infra/monitoring/alerts.yml，把 ddp_outbox_due_events > 100 改成 > -1
#   然后热重载（无需重启）：
curl -X POST http://localhost:9190/-/reload

# 观察告警状态
curl -s http://localhost:9190/api/v1/alerts | python3 -m json.tool | head -20
# 期望：state = "firing"，且能在 Alertmanager UI 看到该告警

# 验证完改回阈值并再次 reload
```

**方式 B**：直接看 Prometheus UI → Alerts 页面，任何 `inactive` 之外的规则至少证明表达式可求值、指标可解析。若显示 `inactive` 但指标明明有数据，说明 expr 里指标名写错——这正是
`test_monitoring_config.py::test_alert_metric_names_exist_in_code` 要防的问题。

## 四、生产部署差异

| 项 | 本地 | 生产 |
|---|---|---|
| 抓取配置 | `prometheus.yml`（api 以 `DDP_ENV=local` 启动，`/metrics` 本地放行） | `prometheus.prod.yml`（Bearer 令牌，失败关闭） |
| 令牌 | 不需要 | `.env` 的 `DDP_METRICS_ACCESS_TOKEN`，写入 `infra/monitoring/metrics_token`（已 `.gitignore`） |
| 接收端 | webhook 占位（`alertmanager.yml`） | 替换为真实飞书/邮件/值班群 webhook |
| 保留期 | 15d | 按合规要求调整 `--storage.tsdb.retention.time` |

生产切换抓取配置：

```bash
# docker-compose.yml 里 prometheus 的挂载改为 prometheus.prod.yml，
# 并追加令牌挂载：
#   - ./infra/monitoring/prometheus.prod.yml:/etc/prometheus/prometheus.yml:ro
#   - ./infra/monitoring/metrics_token:/etc/prometheus/metrics_token:ro
echo "$DDP_METRICS_ACCESS_TOKEN" > infra/monitoring/metrics_token
chmod 600 infra/monitoring/metrics_token
```

## 五、告警清单与处置

| 告警 | 严重度 | 触发条件 | 第一动作 |
|---|---|---|---|
| `DdpApiDown` | critical | `up{job="ddp-api"} == 0` 持续 2m | 看容器存活与 `/health`；启动校验报错（缺密钥）会拒绝启动 |
| `DdpMetricsScrapeFailing` | warning | 抓取失败 10m（与 Down 并存 = 抓取链路问题） | 核对令牌一致性与 `DDP_METRICS_ALLOWED_SOURCE_NETWORKS` |
| `DdpOutboxBacklog` | warning | 到期未投递 >100 持续 10m | 查 worker 存活；看 failed 计数 |
| `DdpOutboxStale` | **critical** | 最老到期事件等待 >900s | 消费者已停：查 worker 日志与事件类型白名单 |
| `DdpHoneypotPaused` | warning | 熔断暂停人数 >0 | 按 `honeypot-appeal.md` 逐个复核，**不要批量 resume** |
| `DdpBackflowBacklog` | info | 回流未转换 >200 持续 30m | 按 `backflow-intake.md` 配置转换目标或批量转任务 |

## 六、已知边界

- 未验证真实 Alertmanager 到外部接收端（飞书/邮件）的投递：本地只验证"规则能触发、告警能进 UI"
- 无分布式 trace：跨服务/跨 worker 问题仍需靠 `request_id` 串日志（OTel 接入见审计 R9）
- worker 无独立指标端点：8 个 worker 仅能看进程存活与共享的 outbox 指标
- Prometheus 单实例无 HA：本地够用，生产需考虑远端写入或双副本
