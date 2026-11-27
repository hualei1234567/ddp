### MEM-OPS-002 | 小表当前不需要分区；n_live_tup 因 PG 容器重启而不可信

- **类型与状态**：`OPS` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-27`
- **适用范围**：项目 `DDP` | 环节 `运维/数据库容量` | 批次/资产 `PostgreSQL 16 业务库 public schema` | 密级 `INTERNAL`
- **核心结论**：DDP 当前生产库**暂不需要分区，也暂不需要新做归档机制**——最大表 12,518 行 / 6.7 MiB，实测业务写入 0.0007 行/秒，距阈值差 170 万倍。但这个"不需要"**必须绑死触发阈值**（5000 万行 / 50 GiB / 1000 万行归档），否则会退化成"永远不需要"。同时记住：**`pg_stat_user_tables.n_live_tup` 在 PG 容器重启后会归零而表数据存活，实测偏差高达 358 倍，任何行数结论都必须用精确 `COUNT(*)`**。
- **关键依据与细节**：

  **实测基线（2026-11-27，docker 容器 `ddp-postgres`，PG 16.16）**
  | 指标 | 值 |
  |---|---|
  | 表数 / 总行数 / 总体积 | 103 张 / 37,003 行 / 30.8 MiB |
  | 最大表 | `outbox_events` 12,518 行 / 6,856 KiB（索引占 36.1%） |
  | 第二大表 | `audit_logs` 12,481 行 / 6,496 KiB（索引占 42.9%） |
  | 全库最大膨胀率 | 0.50%（`outbox_events` 62 死元组 / 12,518 行） |
  | 业务写入实测 | outbox 有机基线 **45.4 行/天** + audit **14.7 行/天** = **0.0007 行/秒** |
  | 相对单实例 1200 TPS 容量 | 约 **1,725,000 倍**余量 |
  | 判定分布 | 103/103 表 = `no_partition_needed` |

  **陷阱一：`n_live_tup` 因容器重启归零（反直觉，最容易踩）**
  | 表 | `n_live_tup` | 精确 `COUNT(*)` | 偏差 |
  |---|---:|---:|---:|
  | `outbox_events` | **35** | **12,518** | **358 倍** |
  | `audit_logs` | **39** | **12,481** | **320 倍** |

  - 根因：`ddp-postgres` 于 **2026-11-23T05:25:03Z 重启**（`docker inspect` 证实）。
    PG 统计信息在**共享内存**里，重启即清零；表数据在磁盘上存活。
  - **决定性证据**：`outbox_events` 中 `created_at >= 2026-11-23 05:25:03+00`
    的行恰好 **35** 行 = `n_live_tup`。
  - **`pg_stat_database.stats_reset` 为空 ≠ 统计新鲜**：该字段只记录**显式
    `pg_stat_reset()` 调用**，容器重启**不写**它。不能用它当新鲜度证据。
  - 连带影响：`docs/testing/evidence/2026-11-26-index-audit/README.md` 写
    「`audit_logs` 39 行……建在小表上」——真实行数是 12,481 行。
    该报告「无可删索引」的核心结论**不受影响**（依赖 `idx_scan` 累计 3,619,952 次，
    样本充分），但"小表豁免"的论证基础需修正。

  **陷阱二：压测尖峰会污染增长外推（差 27 倍）**
  - `outbox_events` 按日行数：11-14:28 / 11-15:**857** / 11-16:21 /
    11-17:**7968** / 11-18:**3375** / 11-21:234 / 11-23:18 / 11-24:1 / 11-25:14 / 11-26:2
  - 含尖峰日均 = **1,251.8 行/天**；剔除尖峰后 = **45.4 行/天**。
  - 尖峰来自压测与 e2e 演练（对应 `2026-11-15-human1m-*` 等证据目录），
    **不代表业务流量**。
  - 剔除方法：**中位数 × 10** 阈值法（不是 p95）。
    本库每表只有 4~10 个有数据日，p95 ≈ 最大值，什么都剔不掉
    （实测 `outbox_events` 的 p95 = 7968 = 最大值）。

  **触发阈值的推导（写入 TPS × 保留天数 = 预期行数）**
  - 写入容量实测（`2026-11-26-write-path-capacity`）：单实例建议 1,200 TPS，
    单机饱和 2,548 TPS；每次领域写 = 1 审计行 + 1 outbox 事件。
  - **50,000,000 行 ÷ 1,200 行/秒 = 11.6 小时**（÷ 2,548 = 5.5 小时）。
    → 阈值**不是遥不可及**：业务一旦跑满单实例容量，半天即触发行数线。
    但**今天**实测 0.0007 行/秒，距触发差 170 万倍。
  - 体积线按实测每行成本换算：**`episodes` 3,181 B/行**（含 jsonb）是唯一
    **体积先触**的表 → 约 **16,875,520 行 / 50 GiB**；其余表行数先触
    （`outbox_events` 561 B/行 → 50 GiB 需 9,572 万行）。
  - 归档线 1,000 万行的依据：`maintenance.py` 的 `sweep_once()` 是
    **单条无分批 DELETE**；删 1000 万行 `outbox_events` 产生 **≈5.22 GiB WAL**。

  **归档能力已存在（不另起平行实现）**
  - `backend/app/workers/maintenance.py` → `sweep_once()`：
    删 `outbox_events` 中 `status IN ('PUBLISHED','RECORDED')` 且
    `created_at < now() - outbox_retention_days`（默认 **30 天**）的行；
    **不删** `PENDING`/`FAILED`。`idempotency_records` 按 `expires_at` 自过期。
  - 实测：`ddp-maintenance-worker` 运行中，最近 200 次清扫**全成功**（0 失败）；
    `outbox_events` 超 30 天行数 = **0** → 保留策略正在生效。
  - ⚠️ `audit_logs` 有 `trg_audit_logs_no_update/no_delete`
    （`ddp_audit_logs_append_only_guard`），**任何 DELETE 都被拒绝**——
    这是安全语义不是性能问题；其归档须走分区 `DETACH`，且需先与安全/合规方确认。

- **影响与后续动作**：
  - **当前不实施分区、不新建归档表/新定时任务**（现有 `maintenance.py` 已覆盖）。
  - 触发线（代码常量 `scripts/db/table_growth_audit.py`，放宽须走评审）：
    分区 5000 万行 / 50 GiB；归档 1000 万行；增长预警 100 万行（30 天投影）；
    大表候选 10 万行。
  - 重跑时机：任一表 > 10 万行 或 > 1 GiB；单日新增 > 1 万行；
    **业务上线/批量导入前**；**PG 容器重启后**；每季度。
  - `audit_logs` 若达 1000 万行，需先定"审计日志可否按期归档"的合规口径。
  - 责任人：运维/SRE；**已完成**（V5.2 审计 R?，`feat/v52-taskd-partition` 分支）。
  - ⚠️ 诚实边界：数据窗口仅 **10 天**，有机基线只由 **7 个日样本**支撑，
    是"暂不需要"结论最大的不确定性来源。

---

#### 工程扩展字段

- **check_ref**：`scripts/tests/test_table_growth_audit.py`（46 条断言：只读护栏 / DSN 护栏 / 脱敏 / 尖峰剔除 / 判定优先级 / 排序确定性 / 阈值钉死）+ `scripts/db/table_growth_audit.py`
- **recurrence**：`1`
- **tags**：`postgres,pg_stat,container_restart,partitioning,archive,retention,outbox,audit_logs,maintenance,threshold,table_growth,readiness`
