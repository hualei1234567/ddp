# ADR-0027 定时备份与维护 Worker（阶段 8 / R-13 T-3）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0025（outbox 语义，T-3 明确挂到这里）、ADR-0024 前置的备份演练记录、审计 R-4
- 影响范围：`app/workers/maintenance.py`（新增）、配置项、`docker-compose.yml`

## 1. 背景

三件事都指向同一个缺口——**没有周期性运维动作**：

1. **备份靠手工**：`scripts/ops/backup-restore-drill.sh` 能跑通，
   但没人定时跑就等于没有备份。演练记录里"定时备份"一栏写的是"阶段 8"。
2. **outbox 只增不减**：ADR-0025 把事件分成投递类与留痕类后，
   留痕类会永久 `RECORDED`；没有归档策略，表单调增长。
3. **幂等表靠单条删除**：过期记录本来由 `lookup()` 顺带清理，
   但只有在同一个 key 被再次访问时才触发——冷 key 永远留着。

## 2. 决策

新增 `app/workers/maintenance.py`，一个进程做三件事，**默认关闭**：

| 职责 | 触发 | 行为 |
|---|---|---|
| 备份 | `maintenance_backup_interval_seconds`（默认 86400s） | 子进程调 `pg_dump`，产物落 `backup_dir`，按 `backup_retention_days` 清理 |
| 事件归档 | 每轮（`maintenance_poll_interval_seconds`，默认 60s） | 删除超过 `outbox_retention_days` 的 `PUBLISHED` / `RECORDED` |
| 幂等清扫 | 每轮 | 删除 `expires_at < now` 的幂等记录 |

### 2.1 默认关闭，且启用时必须显式配置

`maintenance_enabled` 默认 `False`。理由：这是一个**会删数据**的进程。
让它默认在每台机器上跑，等于把"运维决定"变成"框架默认"。

启用后配置校验强制要求 `backup_dir` 非空（否则备份落到容器临时层，重启即丢），
且备份间隔不得小于轮询间隔。

### 2.2 只删终态事件，死信永不自动删

```python
_ARCHIVABLE_STATUSES = ("PUBLISHED", "RECORDED")
```

`PENDING` 还在等消费者；`FAILED`（死信）意味着有人需要去看。
自动删除死信等于把故障静默掉——这正是 R-13 想消除的那类问题。

### 2.3 备份失败必须显式失败

三个 fail-closed 点：

- 子进程非零退出 → 抛 `RuntimeError`，删除半成品文件；
- 产物小于 1KB → 视为异常（空 dump 冒充成功最危险），删除并抛错；
- 目录不可写 → `mkdir(parents=True)` 直接抛 `OSError`。

主循环里捕获异常并 `logger.exception`，但**继续下一轮**——一次失败后退出进程
会让"没人再试"，而持续重试才有恢复机会。日志带 error 级别，可被监控抓取。

### 2.4 只清自己命名的文件

保留期清理只匹配 `ddp-YYYYmmddTHHMMSSZ.sql`。人工放进去的备份
（比如迁移前手工导出的 `manual-before-migration.sql`）不动——
清理逻辑不该猜测别人的文件用途。

### 2.5 不做 MinIO 镜像

对象存储的复制/版本化属于部署层（S3 复制、bucket versioning），
用 `mc mirror` 在应用进程里做全量镜像会把应用变成存储运维工具，
且随对象数增长必然拖垮进程。本 worker 只管数据库。

## 3. 顺带修掉的一个真实 Bug

`app/workers/datasources.py` 调用 `configure_logging(settings.log_level)`，
但 `configure_logging()` 不接受参数（它内部自己取 `get_settings()`）。
这意味着 `python -m app.workers.datasources` 一启动就 `TypeError`。

此前没发现是因为该 worker 只有测试在跑（测试不经过 `main()`）。
本次照抄这行时立刻撞上，一并修复。`outbox.py` 与 `exports.py` 写法正确。

## 4. 验证

| 项 | 结果 |
|---|---|
| 真实执行 `--once` | 768,007 字节 dump，含 75 个 `CREATE TABLE` |
| 该 dump 导入独立库 | 成功，恢复后 75 张表 |
| 备份失败路径 | 连不上库时抛 `RuntimeError` 且目录内无残留文件 |
| 归档边界 | 90 天前的 `RECORDED` 被删；`PENDING` 与 `FAILED` 保留 |
| 幂等清扫 | 只删过期记录 |
| 保留期清理 | 只删 `ddp-*.sql`；人工文件保留 |
| 积压指标 | `pending_backlog()` 正确区分待投递与死信 |
| 测试 | 6 passed（需 `DDP_TEST_POSTGRES_URL` + `pg_dump`，缺失时标注 BLOCKED 跳过） |

## 5. 未做

| 项 | 原因 |
|---|---|
| 备份加密与异地存储 | 属部署层；本机验证环境无对应设施 |
| 以 `--once` 交给 cron/systemd timer 的部署样例 | staging 部署仍是 BLOCKED（用户已确认只本机） |
| 备份完成的告警通道 | 需要外部通知渠道，不在当前范围 |
