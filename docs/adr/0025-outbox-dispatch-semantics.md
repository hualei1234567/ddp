# ADR-0025 outbox 投递类与留痕类的语义分离（R-13）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：`docs/architecture/outbox事件语义界定-v1.0.md`（规则）、审计 R-13（P2）
- 影响范围：`outbox_events` 表、`record_change`、三处 `claim_next`、CI

## 1. 背景

`outbox_events` 同时承担两个职责：

1. **变更留痕** —— 谁在什么时候改了什么，用于审计追溯；
2. **集成投递** —— 通知 worker 去做事，需要消费者、重试、死信。

两者的生命周期完全不同。混在一张表里的后果已经出现：93 种事件类型，
只有 3 种有消费者，其余永久停在 `PENDING`。这既让表单调增长，
又让"漏了消费者"和"本来就不该有消费"无法区分——两种情况在数据上长得一模一样。

## 2. 决策

**每个事件类型显式归入两类之一，并把分类写进表结构。**

| dispatch | status | 含义 |
|---|---|---|
| `false`（留痕类） | `RECORDED` | 写入即终态，不进重试队列 |
| `true`（投递类） | `PENDING` → `PUBLISHED`/`FAILED` | 由 worker 抢占、重试、必要时进死信 |

具体改动：

1. `record_change(..., dispatch: bool = False)`。**默认值是 `False`**：
   新增事件时默认走留痕，不会因为忘了登记而留下一条永远不被消费的待投递消息。
   投递类必须显式传 `True`。
2. `OutboxEvent` 增加 `dispatch` 字段；四处 `enqueue` 实现按它决定初始状态。
3. `claim_next` 增加 `dispatch = true` 过滤（与 `status = PENDING` 同时判断）。
4. 数据库约束 `dispatch_status_consistent`：
   ```sql
   (dispatch = false AND status = 'RECORDED')
   OR (dispatch = true AND status <> 'RECORDED')
   ```
   把"投递消息不得被标成已记录"变成插入就失败的硬错误。没有它，
   这类错误只表现为"消费者一直收不到"，排查成本极高。
5. `status` 允许值增加 `RECORDED`（迁移 `0030`）。

### 2.1 三份 `OutboxEvent` 端口副本一并收敛

`application/{collection,datasets,annotation}/ports.py` 各自定义了一份
`OutboxEvent` dataclass（R-10 的同类问题）。本次改为从 `app.application.audit`
重新导出，否则新增 `dispatch` 字段会在三处产生行为不一致的副本。

## 3. 存量数据

迁移 `0030` 把存量的 `PENDING` 行全部回填为 `dispatch = true`。

**为什么不标成留痕类**：它们此前都是 `PENDING` 等消费者。改成 `RECORDED`
等于把可能待投递的消息静默丢掉——比多留几条待处理消息危险得多。

迁移可回滚：`downgrade` 先把 `RECORDED` 落回 `PUBLISHED`，再恢复旧约束并删列。
真实 PostgreSQL 上已验证 `0029 → 0030 → 0029 → 0030`。

### 3.1 SQLite 的特殊处理

SQLite 不支持 `ALTER ... DROP CONSTRAINT`。迁移在 SQLite 分支重建表以替换
`status` 的 CHECK 与 `dispatch_status_consistent` 约束（PostgreSQL 走原生约束）。
重建时显式列出所有列——迁移是历史快照，不改动历史版本；将来若有列变动，
新迁移必须重新处理这张表，不能靠"重建函数自动跟随"。

## 4. CI 检查（T-4 / T-5）

`scripts/check_outbox_events.py`，在 `make check` 第 7 步与 CI 中以阻断方式执行：

1. **提取**：AST 扫描所有事件类型，**同时**识别字面量与模块级常量。
   T-5 记的"漏掉常量形式"就是因为旧做法只按 `event_type="..."` 字面匹配——
   `dataset.export.requested` 来自 `EXPORT_REQUESTED_EVENT` 常量，因此漏掉。
   正确提取到 93 种（旧口径 83 条）。
2. **校验**：每个投递类事件必须有消费者文件且该文件确实出现该事件类型，
   并在测试里被引用——没有测试的消费者等于没验证过。
3. **防回归**：源码里 `dispatch=True` 的写入点与白名单必须完全一致，双向都查。

> T-5 原文说"修正既有提取脚本"，但仓库里并不存在那个脚本——
> 清单是从一次性命令得出后写进文档的。本次把它变成可重复执行的检查。

## 5. 未做

| 项 | 原因 |
|---|---|
| T-3 投递类事件的保留/归档任务 | 需要与 R-14 幂等表清扫合并成一个维护 worker，属阶段 8 |
| 留痕类的分批归档 | 同上；当前数据量下不是瓶颈 |

## 6. 验收证据

| 项 | 结果 |
|---|---|
| 迁移升降（真实 PostgreSQL） | `0029 → 0030 → 0029 → 0030` 通过 |
| 迁移升降（SQLite，含约束重建） | 2 passed |
| 语义测试（留痕 RECORDED / 投递 PENDING / 抢占过滤 / 约束兜底） | 5 passed |
| 存量 34 条 PENDING 行回填 | `dispatch = t`（psql 实测） |
| 真实 PostgreSQL 全量 | 958 passed / 0 skipped |
| `make check`（含新增第 7 步） | 通过 |
