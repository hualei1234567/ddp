# 大表增长与分区/归档 SOP

- 版本：v1.0 · 2026-11-27
- 分支基线：`feat/v52-taskd-partition`
- 定位：本目录是**运营操作**的唯一入口。领域规则、阈值、状态机以代码为准；
  本文只写「谁、在什么条件下、调哪个脚本、按什么标准判定、出问题怎么处置」。
- 证据来源：[`docs/testing/evidence/2026-11-27-table-growth-audit/`](../testing/evidence/2026-11-27-table-growth-audit/README.md)
  （2026-11-27 生产库实测：103 张表 / 37,003 行 / 30.8 MiB）
- 状态：**判定已实测**（VERIFIED：脚本可复跑，数字来自真实库）；
  分区/归档**未实施**（PLANNED，且当前**不应实施**——见 §1）

---

## 1. 当前判定：**暂不需要分区，也暂不需要新做归档机制**

| 项 | 判定 | 依据 |
|---|---|---|
| 单表分区 | **暂不需要** | 最大表 12,518 行，距 5000 万行阈值差 **3,994 倍** |
| 单表归档 | **暂不需要新做** | `outbox_events` 的 30 天保留 + 现有清扫**已生效**（实测超期行数 = 0） |
| 新建归档表/新定时任务 | **不需要** | `backend/app/workers/maintenance.py` 已覆盖；另起一套会造成两份保留策略打架 |
| **必须做的事** | **钉死触发阈值** | 否则"暂不需要"会退化成"永远不需要"——见 §3 |

> **诚实边界**：判定基于 **10 天数据窗口**（2026-11-14 ~ 11-26），
> 其中 3 天为压测尖峰，有机基线仅由 7 个日样本支撑。
> 这是本判定**最大的不确定性来源**。若真实业务流量特征不同，基线会变——
> 因此 §5 规定了必须重跑审计的时机。

---

## 2. 怎么跑审计（只读，可随时执行）

```bash
# 1) dry-run（默认，不连库）
python3 scripts/db/table_growth_audit.py --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp

# 2) 真实执行（只读 SELECT）
python3 scripts/db/table_growth_audit.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp \
  --execute --format json --out /tmp/table-growth-audit.json
```

**脚本的三条硬规则（防误判，不是防误删）**：

1. **行数用精确 `COUNT(*)`，不用 `n_live_tup`** ——
   PG 容器重启会让统计归零而数据存活（实测 `outbox_events`
   统计 35 行、真实 12,518 行，**偏差 358 倍**）。
2. **统计漂移 > 10 倍即判统计不可信** —— 该表的膨胀率与增长率改用
   **表内时间戳列的按日分布**，不用 `n_live_tup`。
3. **只剔除「中位数 × 10」以上的尖峰日** —— 压测/演练流量不得当作业务基线
   （实测 `outbox_events` 剔除尖峰后基线从 1,251.8 降到 **45.4 行/天**）。

**只读保证（三层）**：

| 层 | 机制 |
|---|---|
| 代码 | 全部 SQL 为 `SELECT`/`WITH`，无任何 DDL/DML |
| 运行 | 每条 SQL 执行前经 `assert_select_only()` 断言：前缀 + 词边界关键字扫描（CTE 内藏写操作同样被拦） |
| 目标 | 默认只允许 loopback（`localhost`/`127.0.0.1`/`::1`）；非 loopback 需显式 `--allow-non-loopback`（**只放开读**） |

**输出字段**：行数（精确）、总体积/数据体积/索引体积、索引占比、
膨胀率（`n_dead_tup`/精确行数）、最近 vacuum/analyze 时间、
统计可信度、按日行数分布、有机基线、30 天投影、判定与理由。

**DSN 脱敏**：报告与终端输出口令一律显示为 `***`（复用 `scripts/perf/core.py` 的
`mask_dsn`），主机与库名保留以便核对目标。

---

## 3. 触发阈值（代码常量，放宽需走评审）

阈值定义在 `scripts/db/table_growth_audit.py`，**不是 CLI 参数**——
想放宽必须改代码并让评审看到（单测
`test_thresholds_are_not_silently_lowered` 钉死当前值）。

| 阈值 | 数值 | 触发后做什么 |
|---|---|---|
| **分区评估（行数）** | **50,000,000 行** | 启动分区评估（ADR + 迁移 + 回滚评审），**不是立即分区** |
| **分区评估（体积）** | **50 GiB**（含索引） | 同上 |
| **归档触发** | **10,000,000 行** | 把 `maintenance.py` 的单条 DELETE 改**分批**或改**分区整体摘除** |
| **增长预警** | **1,000,000 行**（30 天投影） | 提前复查，不等触阈才反应 |
| **大表候选** | **100,000 行** | 纳入候选清单；此线以上算不出增长率 → 判 `insufficient_data` |
| **通用复查线** | **10,000,000 行** | 任一表超过即复查 |

### 3.1 阈值的推导依据

**写入容量实测**（`docs/testing/evidence/2026-11-26-write-path-capacity/README.md`）：
1 并发 698 TPS / 4 并发 2,443 TPS / 16 并发 2,548 TPS（饱和）；
部署建议**单实例承接 1,200 TPS**。

**每次领域写 = 1 审计行 + 1 outbox 事件**（同一事务的公共骨架，同上证据）。

| 场景 | 触及 5000 万行所需时间 |
|---|---:|
| 单实例 1,200 TPS | 41,667 秒 ≈ **11.6 小时** |
| 单机饱和 2,548 TPS | 19,623 秒 ≈ **5.5 小时** |

> **这是阈值的关键含义**：5000 万行**不是遥不可及的数字**。
> 平台一旦承认真实业务流量并跑满单实例容量，`outbox_events`/`audit_logs`
> 会在**半天内**触发行数线。
> **分区是"业务起量后立刻要面对"的问题；但今天（实测 0.0007 行/秒）
> 它离触发还差 170 万倍余量。**

**体积线换算**（按实测每行成本）：

| 表 | 每行成本 | 触及 50 GiB 所需行数 | 先触发的线 |
|---|---:|---:|---|
| `outbox_events` | 561 B/行 | 95,726,913 | 行数（50M） |
| `audit_logs` | 533 B/行 | 100,733,351 | 行数（50M） |
| `data_source_media_assets` | 874 B/行 | 61,424,735 | 行数（50M） |
| `data_lineage_edges` | 686 B/行 | 78,284,098 | 行数（50M） |
| `acceptance_ledger_entries` | 640 B/行 | 83,953,875 | 行数（50M） |
| `annotation_quality_findings` | 596 B/行 | 90,025,768 | 行数（50M） |
| `annotation_assignment_events` | 519 B/行 | 103,477,894 | 行数（50M） |
| **`episodes`** | **3,181 B/行** | **16,875,520** | **体积（50 GiB）先触** |

> **`episodes` 是唯一体积先触的表**（每行 3,181 B，因含 `jsonb` 的
> `device_ids`/`sensor_ids`/`scene_labels`）。它是业务数据的真正载体，
> 其他表都是它的派生——**因此 `episodes` 的评估线更早：约 1,690 万行 / 50 GiB。**

**归档线的代码依据**：`maintenance.py` 的 `sweep_once()` 是**单条无分批 DELETE**。
按实测每行成本，删 1000 万行 `outbox_events` 产生 **≈ 5.22 GiB WAL**，
删 1000 万行 `audit_logs` 产生 **≈ 4.96 GiB WAL**。
GiB 级单条 DELETE 必然长时间锁表且 autovacuum 追不上——
所以归档阈值定在 1000 万行，到时必须改分批或改分区整体摘除。

### 3.2 每张表的**具体**评估线（可直接抄进工单）

| 表 | 当前行数 | 当前体积 | 评估线 |
|---|---:|---:|---|
| `outbox_events` | 12,518 | 6,856 KiB | 50,000,000 行 / 50 GiB |
| `audit_logs` | 12,481 | 6,496 KiB | 50,000,000 行 / 50 GiB ⚠️ 见 §4.3 |
| `episodes` | 206 | 640 KiB | **16,875,520 行 / 50 GiB**（体积先触） |
| `data_source_media_assets` | 1,509 | 1,288 KiB | 50,000,000 行 |
| `annotation_quality_findings` | 1,044 | 608 KiB | 50,000,000 行 |
| `data_lineage_edges` | 872 | 584 KiB | 50,000,000 行 |
| `acceptance_ledger_entries` | 743 | 464 KiB | 50,000,000 行 |
| `annotation_assignment_events` | 600 | 304 KiB | 50,000,000 行 |
| `episode_raw_assets` | 524 | 280 KiB | 50,000,000 行 |
| `lineage_node_refs` | 470 | 200 KiB | 50,000,000 行 |

---

## 4. 归档策略（**复用现有能力，不另起平行实现**）

### 4.1 现有机制

`backend/app/workers/maintenance.py` → `MaintenanceWorker.sweep_once()`：

- **归档**：`outbox_events` 中 `status IN ('PUBLISHED','RECORDED')` 且
  `created_at < now() - outbox_retention_days`（`backend/app/core/config.py:288`
  默认 **30 天**）的行被删除。
- **不删**：`PENDING`（仍在等消费者）与 `FAILED`（死信需人工处理）。
- **清扫**：`idempotency_records` 中 `expires_at < now()` 的行被删除。
- 运行方式：`ddp-maintenance-worker` 容器，`DDP_MAINTENANCE_ENABLED=true`。

**实测状态（2026-11-27）**：最近 200 次"清扫完成"日志**全部成功**（0 失败）；
`outbox_events` 中**超过 30 天的行数 = 0**——保留策略正在生效。

### 4.2 各表的归档策略（当前）

| 表 | 当前策略 | 触发后的动作 |
|---|---|---|
| `outbox_events` | **维持现状**（30 天保留 + 现有清扫） | > 1000 万行：单条 DELETE 改**分批**（每批 10 万行）或改**分区整体摘除** |
| `idempotency_records` | **维持现状**（`expires_at` 自过期） | > 1000 万行：同上加分批 |
| `audit_logs` | **维持现状** | > 1000 万行：**先解决 append-only 语义**（见 §4.3） |
| `episodes` | **不归档**（业务主数据） | > 1,690 万行 / 50 GiB：评估按 `created_at` 月度分区，**归档用 DETACH 而非 DELETE** |
| 其余表 | **不归档** | 达 1000 万行再单独评估 |

### 4.3 `audit_logs` 的特殊约束（⚠️ 评估前必读）

`audit_logs` 上有**两个 BEFORE 触发器**：
`trg_audit_logs_no_update` 与 `trg_audit_logs_no_delete`，
两者都执行 `ddp_audit_logs_append_only_guard()`，
触发 `RAISE EXCEPTION 'audit_log_append_only'`。

**含义**：**任何 DELETE 都被拒绝**——这不是性能问题，是**安全语义**。
所以 `audit_logs` 的归档路径与 `outbox_events` **不同**：
分区后用 `DETACH PARTITION`（整块摘除，语义上比 DELETE 干净）
而不是 DELETE 行。

**评估分区时必须先与安全/合规方确认**「审计日志可否按期归档」，
再动触发器或选分区方案。**本文不预设该结论。**

### 4.4 明确不做的事

- **不新建归档表 / 归档 schema**：现有 `maintenance.py` 已够用；
  另起一套会造成两份保留策略互相打架。
- **不新建定时任务**：归档已挂在维护 worker 主循环里。
- **不改 `audit_logs` 的 append-only 触发器**：那是安全语义，不是性能问题。

---

## 5. 何时重跑审计（必读）

| 触发条件 | 原因 |
|---|---|
| 任一表行数 **> 10 万** | 突破大表候选线，需有增长率结论 |
| 任一表体积 **> 1 GiB** | 逼近体积阈值 |
| 单日新增行数 **> 10,000** | 说明业务流量跃迁（可能是首次真实业务负载） |
| **业务上线 / 批量导入前** | 导入会瞬间改变量级，需重新判定 |
| **PG 容器重启后** | 统计归零（§6），需重算可信度 |
| 每季度例行 | 数据增长会改变判定 |

### 5.1 判定输出的读法

脚本对每张表给一个判定：

| 判定 | 含义 | 要做什么 |
|---|---|---|
| `no_partition_needed` | 规模与增长率都远低于阈值 | 无需动作；复查即可 |
| `growth_review` | 30 天投影已超增长预警线 | 提前复查，准备分区方案 |
| `partition_review` | 已达分区阈值或保留期内将触阈 | **启动分区评估**（ADR + 迁移 + 回滚） |
| `archive_needed` | 已达归档阈值 | 改造清扫策略（分批 / DETACH） |
| `insufficient_data` | 规模已达候选线但**算不出增长率** | 人工复查：补时间列或人工采样 |

> `insufficient_data` 只给**已达大表候选线**的表。
> 小表即使没有时间列也判 `no_partition_needed`——
> 一张 1044 行的表距离 5000 万行差 4 万倍，增长率未知不影响当前判定。
> 把小表标成 `insufficient_data` 会淹没真正需要关注的表。

---

## 6. 已知陷阱：`n_live_tup` 不可信（PG 重启即归零）

**实测（2026-11-27）**：103 张表中 **83 张**的
`pg_stat_user_tables.n_live_tup` 与精确 `COUNT(*)` 严重不符。

| 表 | `n_live_tup` | 精确 `COUNT(*)` | 偏差 |
|---|---:|---:|---:|
| `outbox_events` | **35** | **12,518** | **358 倍** |
| `audit_logs` | **39** | **12,481** | **320 倍** |

**根因**：`ddp-postgres` 容器于 **2026-11-23T05:25:03Z 重启**
（`docker inspect` 证实）。PG 的统计信息存在**共享内存**里，
容器重启即清零，而表数据在磁盘上存活。
**决定性证据**：`outbox_events` 中 `created_at >= 2026-11-23 05:25:03+00`
的行恰好 **35** 行，与 `n_live_tup=35` **完全相等**。

> **⚠️ `pg_stat_database.stats_reset` 为空 ≠ 统计新鲜**：
> `stats_reset` 只记录**显式 `pg_stat_reset()` 调用**，
> 容器重启**不写**该字段。因此不能拿它当统计新鲜度的证据。

**对本 SOP 的影响**：

- 行数一律用精确 `COUNT(*)`；
- 膨胀率（`n_dead_tup`）同样受统计归零影响，
  故**不基于膨胀率下任何结论**；
- 增长率改用**表内时间戳列的按日分布**（不依赖统计信息）。

**对既有证据的影响**：`docs/testing/evidence/2026-11-26-index-audit/README.md`
写「`audit_logs` 39 行……都建在小表上」——其**真实行数是 12,481 行**。
该报告「没有可删索引」的核心结论**不受影响**
（零使用判定依赖 `idx_scan` 累计 3,619,952 次，样本充分），
但其"小表豁免"的论证基础**需按本节修正**。

---

## 7. 术语

| 术语 | 含义 |
|---|---|
| **有机基线** | 剔除「中位数 × 10」以上的尖峰日（压测/演练）后，日常日增行数的均值；用于外推的唯一口径 |
| **尖峰日** | 日增量超过「中位数 × 10」的日子；本库中对应压测与 e2e 演练日 |
| **统计可信** | 精确 `COUNT(*)` 与 `n_live_tup` 的比值 ≤ 10 倍；不可信时改用时间列分布 |
| **分区评估触发** | 达到阈值后**启动评估**（ADR + 迁移 + 回滚评审），不等于立即实施分区 |
| **归档** | 把超过保留期的终态数据移出主表；本项目当前仅 `outbox_events` 需要 |
| **append-only** | `audit_logs` 的语义：触发器拒绝 UPDATE/DELETE；归档需走分区 DETACH 而非 DELETE |

---

## 8. 相关文档

| 文档 | 关系 |
|---|---|
| [`docs/testing/evidence/2026-11-27-table-growth-audit/README.md`](../testing/evidence/2026-11-27-table-growth-audit/README.md) | 本文的实测证据与完整推导 |
| [`docs/ops/backup-restore-runbook.md`](backup-restore-runbook.md) | 备份与恢复演练（当前库 17.3 MB，RTO 9s → 备份无压力） |
| [`docs/ops/README.md`](README.md) | 运营 SOP 总索引与角色权限矩阵 |
| `docs/testing/evidence/2026-11-26-write-path-capacity/README.md` | 写入 TPS 实测（阈值推导的输入） |
| `docs/memory/MEM-OPS-002-table-growth-partition-thresholds.md` | 记忆卡片（判定与阈值的沉淀） |
