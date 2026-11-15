# outbox 事件语义界定 v1.0

> 关联：审计 R-13（P2）。现象：**83 种事件类型，只有 2 种有消费者**
> （`ingestion.raw_asset.validation_requested`、`dataset.export.requested`），
> 其余事件永久停在 `PENDING`，表单调增长，且无法区分"漏了消费者"与"本来就不该有消费"。

## 1. 根因

`outbox_events` 同时承担了两个职责：

1. **变更留痕**（谁在什么时候改了什么）—— 用于审计追溯与事件回放；
2. **集成投递**（通知某个 worker 去做事）—— 需要消费者、重试、死信。

两种职责的**生命周期完全不同**：留痕记录应当永久保留或按保留策略归档；
投递消息应当在投递成功后标记 `PUBLISHED` 并定期清理。混在一张表里，
必然出现"要么该清的不敢清，要么该投的没人投"。

## 2. 界定规则（本次决策）

每个事件类型必须**显式**归入下列两类之一。归类写在本文件的表格里，
新增事件时必须在 PR 中同步登记，否则评审打回。

### 2.1 投递类（DISPATCH）

**定义**：有明确的执行方需要据此做事；没有消费者就是缺陷。

**判据**：去掉这个事件后，某个后台动作永远不会发生（而不是"少了一条记录"）。

**要求**：
- 必须有消费者（worker 或 adapter），且在 CI 里有该消费者的测试；
- 投递成功 → `PUBLISHED`；失败 → `mark_retry` 带退避；超限 → `mark_dead_letter`；
- 必须有清理/归档策略（`PUBLISHED` 超过保留期可删）。

### 2.2 留痕类（AUDIT）

**定义**：只记录"发生了什么"，不需要任何执行方响应。

**判据**：去掉这个事件后，系统行为完全不变，只是少了一条可查询的记录。

**要求**：
- **不要求消费者**，也不应因为它没有消费者就被判定为缺陷；
- 写入即视为完成：`status` 直接为 `PUBLISHED`（或专用 `RECORDED`），
  不进入重试队列，避免被 worker 误捞；
- 受保留策略约束，不与投递类混用清理逻辑。

## 3. 当前归类

### 3.1 投递类（3 个，均有消费者与测试）

| 事件 | 消费者 | 状态 |
|---|---|---|
| `ingestion.raw_asset.validation_requested` | `workers/outbox.py` | ✅ 已实现 |
| `dataset.export.requested` | `workers/exports.py` | ✅ 已实现 |
| `datasource.import_requested` | `workers/datasources.py` | ✅ 已实现 |

投递类清单的唯一权威是 `scripts/check_outbox_events.py` 的 `DISPATCH_EVENTS`：
它同时被 CI 与 `make check` 校验，声明与实现不一致会直接失败。

### 3.2 留痕类（82 个）

实测前缀分布（由脚本从源码提取，与附录清单一致）：

| 前缀 | 数量 |
|---|---|
| `annotation.*` | 24 |
| `dataset.*` | 14 |
| `ingestion.*` | 12 |
| `modelops.*` | 11 |
| `collection.*` | 6 |
| `training.*` | 6 |
| `supplier.*` | 4 |
| `datasource.*` | 3 |
| `device.*` | 1 |
| `sensor.*` | 1 |

`ingestion.*` 的 12 条里有 1 条是投递类（上述 validation_requested），
其余 11 条（上传会话/分片的进度与状态留痕）为留痕类。

## 4. 待办与完成情况

| 项 | 内容 | 优先级 | 状态 |
|---|---|---|---|
| **T-1** | `record_change` 增加 `dispatch: bool` 参数 | P2 | ✅ 已实现 |
| **T-2** | `claim_next` 只捞 `PENDING` 且 `dispatch=true` 的行 | P2 | ✅ 已实现 |
| **T-3** | 投递类事件的保留/归档任务 | P2 | ❌ 未做（与阶段 8 维护 worker 一起） |
| **T-4** | CI 增加"投递类事件必须有消费者测试"检查 | P2 | ✅ 已实现 |
| **T-5** | 事件清单提取脚本 | P3 | ✅ 已实现（原脚本并不存在，本次新建） |

### 4.1 T-1 / T-2 的实现

`record_change(..., dispatch: bool = False)`，默认 `False`——默认值刻意选
"不需要消费者"那一侧，避免新增事件时无意间留下一条永远不会被消费的待投递消息。
投递类必须显式传 `True`，并在本文件的归类表与
`scripts/check_outbox_events.py` 的白名单里同时登记。

写入语义：

| dispatch | status | 说明 |
|---|---|---|
| `False`（留痕类） | `RECORDED` | 写入即终态，不进重试队列 |
| `True`（投递类） | `PENDING` | 由 worker 抢占、重试、必要时进死信 |

数据库约束 `dispatch_status_consistent` 把这条对应关系写进表：

```sql
(dispatch = false AND status = 'RECORDED')
OR (dispatch = true AND status <> 'RECORDED')
```

没有这条约束时，"投递消息被误标成已记录"只会表现为"消费者一直收不到"，
很难排查。现在它是一插入就失败的硬错误。

`claim_next` 同时按 `dispatch = true` 与 `status = 'PENDING'` 过滤。
两个条件看起来冗余（约束已保证一致性），但保留 `dispatch` 判断是刻意防御：
万一将来有人放宽约束，抢占端仍然不会捞错。

存量数据在迁移 `0030` 里全部回填为 `dispatch = true`——它们此前都是 `PENDING`
等消费者；若改成留痕类，等于把可能待投递的消息静默丢掉。

### 4.2 T-4 / T-5 的实现

`scripts/check_outbox_events.py` 做三件事，`make check` 第 7 步与 CI 都以阻断方式执行：

1. **提取**：用 AST 扫描所有事件类型字符串，**同时**识别字面量与模块级常量。
   T-5 记的"漏掉常量形式"就是因为旧做法只按 `event_type="..."` 字面匹配。
2. **校验**：每个投递类事件必须有消费者文件，且该文件里确实出现该事件类型；
   并且在测试里被引用——没有测试的消费者等于没验证过。
3. **防回归**：源码里 `dispatch=True` 的写入点必须与白名单完全一致，
   双向都查（白名单有但源码没有 → 声明过期；源码有但白名单没有 → 漏登记）。

实测提取到 93 种事件类型（旧口径的 83 条是漏了常量形式的结果），
其中投递类 3 种。

## 5. 对阶段 7 数据平面的约束

`datasource.import_requested` 归为**投递类**（下一批实现 worker 消费它）。
在 worker 落地前，该事件会停在 `PENDING` —— 这是**已知且有意**的状态，
不是缺陷，但必须在阶段 7 第二批交付时一并补齐消费者，否则应降级为留痕类。

## 附录：完整事件清单

见 `docs/architecture/outbox事件清单-v1.0.txt`（由脚本从源码提取，便于比对漂移）。
