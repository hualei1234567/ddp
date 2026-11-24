# DDP 备份与恢复 Runbook（FR-043）

- 版本：v1.0 · 2026-11-24
- 适用：本地 docker compose 栈、单机部署（laptop / 单节点）
- 配套演练脚本：`scripts/ops/backup-restore-drill.sh`
- 最近演练证据：`docs/testing/evidence/2026-11-24-fr043-drill/backup-restore-drill.md`

---

## 1. DDP 的状态在哪里（三处，缺一处都不算恢复）

| # | 位置 | 内容 | 丢失后果 |
|---|---|---|---|
| 1 | **PostgreSQL** | 项目/用户/设备/采集会话/上传会话/RawAsset/Episode/标注任务与提交/数据集/版本/导出/质检/outbox/审计 | 业务事实全丢，平台回到空库 |
| 2 | **MinIO** | RawAsset 原始字节、导出产物、Dataset Manifest | 元数据还在但**内容无法下载**，所有导出不可用 |
| 3 | **Agent Spool**（端侧） | SQLite WAL 状态库 + 待上传数据文件 | 端侧白采一次数据；已上传的不受影响 |

> **最容易漏的是第 3 处**：它不在服务器上，而在每台采集机上。端侧机器损坏 = 未上传的采集数据永久丢失。

### 不在 DDP 状态范围内

- Redis / 消息队列：本系统用 **PostgreSQL outbox** 而非外部 MQ，无需单独备份
- 配置与密钥：`backend/.env`、`DDP_JWT_SECRET`、S3 凭据 —— **必须独立于数据库备份另行保管**（见 §5）

---

## 2. 备份策略

### 2.1 数据库（PostgreSQL）

```bash
# 逻辑备份（推荐日常）：自带 schema + 数据 + 约束 + 索引
docker exec ddp-postgres pg_dump -U ddp -d ddp --no-owner --no-acl \
  > ddp-$(date +%Y%m%d-%H%M%S).sql

# 校验产物（必须做，否则可能备份出 0 字节而不自知）
ls -l ddp-*.sql          # 大小应 > 1MB（当前库 17MB 量级）
shasum -a 256 ddp-*.sql  # 记录摘要便于核对
```

**频率建议**：日更环境每日一次；有写入的演示环境每日两次。
**保留**：至少 7 个日备 + 4 个周备。

### 2.2 对象存储（MinIO）

```bash
MC=/opt/bitnami/minio-client/bin/mc
docker exec ddp-minio sh -c \
  "$MC alias set local http://127.0.0.1:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\""
# 全量镜像到宿主机备份目录
docker exec ddp-minio sh -c "$MC mirror local /backup/minio-$(date +%Y%m%d)"
```

**关键**：`mc mirror` 后必须比对**对象数**与**总量**，否则「备份成功」可能只是空目录。

### 2.3 Agent Spool（端侧）

```bash
# 停 agent 后打整包（含 WAL 伴生文件；运行中直接 cp 会得到不一致的库）
systemctl stop ddp-edge-agent
tar -czf /backup/spool-$(date +%Y%m%d).tgz -C /var/lib/ddp-edge-agent .
systemctl start ddp-edge-agent
```

> 若不能停机：先 `sqlite3 state.sqlite3 "PRAGMA wal_checkpoint(TRUNCATE)"`，
> 再分别打包 `state.sqlite3` 与 `data/`。**不要**只拷 `state.sqlite3` 而漏掉 `data/`。

---

## 3. 恢复演练（可随时重跑，不动生产库）

```bash
# 全套：PostgreSQL + MinIO + Spool + 应用层
bash scripts/ops/backup-restore-drill.sh

# 只跑 PG + Spool（MinIO 不可用时）
SKIP_MINIO=1 bash scripts/ops/backup-restore-drill.sh

# 落盘证据（人工演练时用）
EVIDENCE_DIR=docs/testing/evidence/$(date +%Y-%m-%d)-fr043-drill \
  bash scripts/ops/backup-restore-drill.sh
```

**演练的安全边界（脚本已强制）**：
- PG 恢复到**独立新库** `ddp_restore_drill`，**绝不覆盖** `ddp`
- MinIO 只删**一个抽样对象**再恢复，不动整桶
- Spool 在 `mktemp` 临时目录操作副本，不动真实 Spool
- 结束自动清理（EXIT trap 兜底，异常退出也清）

### 演练校验的 7 个不变量

| # | 不变量 | 为什么必须查 |
|---|---|---|
| 1 | 表数一致 | 少表 = schema 恢复不全 |
| 2 | alembic 版本一致 | 版本不符 = 迁移链断裂，应用起不来 |
| 3 | **RPO 锚点一致**（`audit_logs.occurred_at` 最大值） | 不一致 = 备份窗口后的数据丢了 |
| 4 | 受检业务表行数一致 | 数据残缺 |
| 5 | 触发器/约束/**索引**数一致 | **部分唯一索引丢失会让去重语义静默失效**（本项目真实踩过内容重复撞索引的坑） |
| 6 | MinIO 对象数 + 抽样对象 sha256 | 只有"有备份"不够，要证明"删了能回来且内容一致" |
| 7 | 恢复库可执行跨表 JOIN | 表在但外键/索引坏了也等于没恢复 |

---

## 4. 真实故障恢复步骤

### 4.1 数据库损坏 / 误删

```bash
# 1) 停应用与所有 worker（避免恢复过程中继续写入）
docker compose stop api worker domain-worker export-worker \
  processing-worker ingestion-job-worker maintenance-worker datasource-worker

# 2) 确认备份可用
shasum -a 256 ddp-YYYYMMDD.sql    # 与记录比对

# 3) 恢复到新库验证，**确认无误再切换**
docker exec ddp-postgres psql -U ddp -d postgres -c "CREATE DATABASE ddp_restore"
docker exec -i ddp-postgres psql -U ddp -d ddp_restore -v ON_ERROR_STOP=1 < ddp-YYYYMMDD.sql
# 校验 alembic 版本与关键表行数
docker exec ddp-postgres psql -U ddp -d ddp_restore -tAc "select version_num from alembic_version"

# 4) 切换（旧库改名保留，不删）
docker exec ddp-postgres psql -U ddp -d postgres -c "ALTER DATABASE ddp RENAME TO ddp_broken_$(date +%Y%m%d)"
docker exec ddp-postgres psql -U ddp -d postgres -c "ALTER DATABASE ddp_restore RENAME TO ddp"

# 5) 起应用并验证
docker compose up -d
curl -sf http://127.0.0.1:8000/health && echo OK
```

### 4.2 对象存储数据丢失

```bash
# 全量回灌（谨慎：会覆盖同名对象）
docker exec ddp-minio sh -c "$MC mirror /backup/minio-YYYYMMDD local --overwrite"
# 单对象恢复
docker exec ddp-minio sh -c "$MC cp /backup/.../raw/<key> local/ddp-raw/<key>"
# 校验：对象数 + 抽样 sha256
```

### 4.3 端侧 Spool 恢复

```bash
systemctl stop ddp-edge-agent
tar -xzf /backup/spool-YYYYMMDD.tgz -C /var/lib/ddp-edge-agent
systemctl start ddp-edge-agent
# 校验：agent 日志应显示 pending 条目被重新拾起（不是 0 条）
journalctl -u ddp-edge-agent -n 50 | grep -i "pending\|resume"
```

---

## 5. 密钥与配置（不在数据库备份里！）

| 项 | 存放 | 丢失后果 |
|---|---|---|
| `DDP_JWT_SECRET` | 部署环境变量 / 密钥管理 | 全员登录失效，需重新签发 |
| `DDP_S3_ACCESS_KEY` / `SECRET_KEY` | 同上 | 无法访问对象存储 |
| `DDP_METRICS_ACCESS_TOKEN` | 同上 | 监控采集失效 |
| Agent 配对 token | 数据库（**哈希存储**） | 可重新签发，无需备份明文 |

**规则**：密钥必须与数据库备份**分开保管**，且不得写进任何代码仓库或备份文件。

---

## 6. RPO / RTO 实测基线（2026-11-24）

| 指标 | 实测值 | 说明 |
|---|---|---|
| **RPO** | 至备份时点 | 恢复后 `audit_logs.occurred_at` 最大值与源库一致 |
| **RTO（数据恢复）** | **9s**（脚本全流程） | PG 备份 0s / MinIO 备份 2s / MinIO 恢复 1s / PG 恢复 1s / Spool 0s / 应用层校验 1s |
| 数据量 | PG 17.3MB / MinIO 744MiB（2094 对象） | 当前本地栈规模 |

> **容量提示**：744MiB 备份需 2s，按线性外推，100GiB 约需 5 分钟。真实生产 RTO 需按实际数据量重测，本表仅证明**流程可用且可量化**。

---

## 7. 演练频率与验收标准

| 频率 | 动作 |
|---|---|
| 每次发版前 | 跑一次 `backup-restore-drill.sh`，产物留证据 |
| 每月 | 做一次**真实故障演练**（在 staging/隔离环境按 §4 全流程走一遍） |
| 每季度 | 更新本文档，重测 RPO/RTO 基线 |

**验收标准**：§3 的 7 个不变量全部通过，且证据文件已落盘。任一失败即视为**恢复能力不可用**，必须修复后重跑。

---

## 8. 已知限制（诚实声明）

1. **备份不含密钥**：密钥丢失只能重新签发，无法从备份恢复
2. **未做 PITR**：当前是逻辑备份（`pg_dump`），恢复粒度 = 备份时点；如需任意时间点恢复需开启 WAL 归档（未实现）
3. **MinIO 为全量镜像**：数据量大时耗时会线性增长，未做增量
4. **Spool 备份需停机或 checkpoint**：热拷贝可能得到不一致的 SQLite 状态
5. **演练在本地栈验证**：RTO 数字不代表生产环境，需按实际数据量重测
