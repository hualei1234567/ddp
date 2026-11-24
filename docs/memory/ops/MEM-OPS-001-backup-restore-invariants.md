### MEM-OPS-001 | 备份恢复演练的 7 个不变量与 RPO/RTO 基线

- **类型与状态**：`OPS` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `运维/灾难恢复` | 批次/资产 `PG + MinIO + Agent Spool` | 密级 `INTERNAL`
- **核心结论**：DDP 的状态分散在**三处**（PostgreSQL 业务事实、MinIO 内容字节、Agent Spool 待上传队列），缺一处都不算恢复。演练必须验证 **7 个不变量**，且全部可自动化断言。
- **关键依据与细节**：

  **三处状态**
  | # | 位置 | 内容 | 丢失后果 |
  |---|---|---|---|
  | 1 | PostgreSQL | 项目/资产/Episode/标注/数据集/outbox/审计 | 业务事实全丢 |
  | 2 | MinIO | RawAsset 原始字节、导出产物、Manifest | 元数据在但内容无法下载 |
  | 3 | **Agent Spool**（端侧） | SQLite WAL + 待上传文件 | 端侧白采一次 |

  > Spool 最容易漏——它不在服务器上，而在每台采集机。此前完全未纳入演练。

  **7 个不变量（每次演练必须全过）**
  1. 表数一致
  2. alembic 版本一致
  3. **RPO 锚点一致**（`audit_logs.max(occurred_at)`）——不一致 = 备份窗口后数据丢了
  4. 受检业务表行数一致
  5. 触发器 / 约束 / **索引**数一致（部分唯一索引丢失会让去重语义**静默失效**）
  6. MinIO 对象数 + 抽样对象 sha256（**真删再恢复**，不能只 mirror 完事）
  7. 恢复库可执行跨表 JOIN（表在但外键坏了 = 没恢复）

  **实测基线（2026-11-24，本地栈）**
  | 指标 | 值 |
  |---|---|
  | RPO | 至备份时点（恢复后 `occurred_at` 最大值与源库一致） |
  | RTO | **10s**（PG 备份 0s / MinIO 备份 2s / MinIO 恢复 1s / PG 恢复 1s / Spool 0s / 应用层校验 0~1s） |
  | 数据量 | PG 17.3MB / MinIO 744MiB（2094 对象） |

  > 容量提示：744MiB 需 2s，按线性外推 100GiB 约 5 分钟。**生产 RTO 需按实际数据量重测。**

  **安全边界（脚本已强制）**
  - PG 恢复到**独立新库** `ddp_restore_drill`，绝不覆盖 `ddp`
  - MinIO 只删**一个抽样对象**再恢复，不动整桶
  - Spool 在 `mktemp` 临时目录操作副本，不动真实 Spool
  - `trap cleanup EXIT` 兜底，异常退出也清理

- **影响与后续动作**：
  - 每次发版前跑一次演练，产物留证据
  - 每月复审 RPO/RTO 基线，每季度按实际数据量重测
  - **反向验证**：故意跳过 Spool 恢复 / 篡改 RPO 锚点，脚本必须 exit 1（已实测通过）
  - 责任人：运维/SRE；**已完成**（PR #103）

---

#### 工程扩展字段

- **check_ref**：`scripts/tests/test_backup_restore_drill.py`（11 条结构校验）+ `scripts/ops/backup-restore-drill.sh`
- **recurrence**：`1`
- **tags**：`backup,restore,rpo,rto,postgres,minio,spool,drill,invariant`
