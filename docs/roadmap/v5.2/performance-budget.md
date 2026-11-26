# DDP 性能预算表（阶段 4-2）

> 制定日期：2026-11-26｜基线来源：`docs/testing/evidence/2026-11-26-prod-readonly-perf/`
> 环境口径：单机生产（docker-compose PG 16.16 + MinIO，API 单进程），只读端点，并发 8，每端点 ≤700 请求

## 一、预算定义

**p95 延迟预算**是"正常负载下允许的最慢响应时间"。超预算不算故障，但必须进入优化队列；
**连续 3 天超预算**才升级为事故。

| 域 | 端点 | p95 预算 | 当前实测 | 状态 |
|---|---|---|---|---|
| 采集 | `GET /collection-tasks` | ≤150ms | 75.66ms（limit 50：87.93ms） | ✅ |
| 采集 | `GET /collection-tasks/{id}` | ≤100ms | 未单独测（同域） | — |
| 标注 | `GET /annotation-tasks` | ≤150ms | 未单独测 | — |
| 标注 | `POST /annotation-submissions/{id}/records` | ≤300ms | 未测（写路径，压测未覆盖） | — |
| 标注 | `GET /annotation-backflow` | ≤100ms | 未测 | — |
| 运维 | `GET /ops/token-ledger` | ≤200ms | 未测（聚合查询） | — |
| 平台 | `GET /health` | ≤50ms | 未测 | — |
| 导出 | `POST /llm-exports` | ≤2000ms | 未测（大记录集应异步） | — |

**预算取值依据**：标注员交互类 ≤150ms（人类可感知阈值约 100–200ms）；写操作 ≤300ms；
聚合报表 ≤200ms；导出类允许秒级。

## 二、已识别瓶颈与处置

| 瓶颈 | 证据 | 状态 | 处置 |
|---|---|---|---|
| `collection-tasks` 列表 N+1（每行 2 次关联查询） | 实测 limit 1→20→50 = 8.6→38.9→87.7ms；查询数 2N+1 | ✅ 已修复（PR 见阶段 4-2） | 批量预取，固定 3 次查询；回归测试断言查询数与页大小无关 |
| 缺 p95 采集的自动化 | 手动跑 `scripts/perf/readonly_load.py` | ⬜ 待做 | 阶段 4-1 SLO 指标落地后接入看板 |
| 写路径无压测覆盖 | 只读压测红线不含写 | ⬜ 待做 | 需数据隔离方案后再压（生产写压测风险高） |

## 三、超预算处置流程

1. **发现**：看板 p95 超预算 → 当班 owner 记录到 `docs/ops/dashboard-spec.md` 对应条目
2. **分级**：
   - 超预算 <2 倍且无错误率上升 → 记优化队列，本迭代内处理
   - 超预算 ≥2 倍或伴随错误率上升 → 当班降级（限流/关特性开关），2 小时内定位
3. **定位**：先看 Prometheus 慢端点指标 → 再看 PG `pg_stat_statements`/慢查询日志
4. **修复后**：用 `scripts/perf/readonly_load.py --execute` 重跑同参数，把新基线写入证据目录

## 四、采集方式

```bash
# 只读压测（默认 dry-run，必须显式 --execute）
python scripts/perf/readonly_load.py --base-url http://127.0.0.1:8077 \
  --token "$TOKEN" --endpoints /api/v1/projects/$PID/collection-tasks \
  --concurrency 8 --requests 700 --execute
```

**红线**：仅 GET、并发 ≤32、请求 ≤2000、时长 ≤180s、PG 连接 >80% 自动中止。

## 五、写路径容量基线（审计 R1，2026-11-26）

### 实测（隔离临时库，每次写入 = 审计行 + outbox 事件双写）

| 并发 | p50 | p95 | p99 | TPS | 死锁 |
|---|---|---|---|---|---|
| 1 | 1.27ms | 1.96ms | 5.69ms | 698 | 0 |
| 4 | 1.29ms | 2.44ms | 5.26ms | 2,443 | 0 |
| 16 | 5.30ms | 8.54ms | 17.59ms | 2,548（饱和） | 0 |

证据与复现方式：`docs/testing/evidence/2026-11-26-write-path-capacity/`

### 写入 p95 预算

| 域 | 写操作 | p95 预算 | 依据 |
|---|---|---|---|
| 标注 | 提交记录（事务骨架 + 业务校验） | ≤300ms | 人机交互，用户等待可接受上限 |
| 标注 | 任务/批次创建 | ≤300ms | 同上 |
| 回流 | 单批受理（≤200 条） | ≤2000ms | 批处理，非交互 |
| 导出 | 触发导出任务 | ≤500ms | 异步任务，只需快速受理 |

### 连接池配置红线（必须遵守）

- PG `max_connections=100`，**所有进程连接总和 ≤ 80**（留 20% 给迁移/监控/人工）
- 单 API 实例：`pool_size=8, max_overflow=8`（实测 4–8 并发即饱和，更宽只增延迟）
- 13 个 compose 服务若各配 8，合计 104 > 100 → **会打满**。worker 类服务应 `pool_size=2`
- 扩容判定：所需实例数 = `ceil(业务写入 QPS / 1,200)`

### 当前水位

生产库最大表 240 行，实际写入 QPS < 1 → 现有配置有 **1000 倍以上余量**。
需关注容量的三个场景：批量导入（万级 Episode）、批量回流受理、导出集中触发。
