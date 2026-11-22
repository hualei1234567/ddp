# ADR-0043 质检异步化的边界：只新增执行入口，不改既有同步路径

- 状态：**ACCEPTED**
- 日期：2026-11-22
- 决策者：System Architect + 后端；批准人：用户（长期授权）
- 关联：ADR-0042（生命周期与结论正交）、V31-030、V31-031、V31-032
- 约束：不推翻既有同步质检的设计与语义，不改写历史运行，不新增契约错误码

## 1. 背景

V3.1 任务卡 V31-031 写的是"提交事件 → Outbox → 质检 Worker（幂等 + 重试 +
死信）"，V31-030 为此把 `quality_runs` 的 `lifecycle` 与结论拆成正交两列。

但仓库里已有三处质检入口，且都**明确写了"不进 Worker"并给出理由**：

| 入口 | 位置 | 现有理由 |
|---|---|---|
| 标注提交后自动质检 | `application/annotation/submissions.py:391` → `quality_checks.py` | "O(记录数 × 规则数) 的纯内存计算……塞进队列只会换来结果不确定何时可见和一套额外的幂等/重试语义" |
| HTTP 单次质检 | `POST /quality/runs` → `QualityRunUseCases.run` | "运行是纯内存计算……不占用 Worker" |
| 处理流水线 `QUALITY_PRE_CHECK` | `application/processing/quality_stage.py` | 已在 Worker 内，与阶段推进同事务；"先算完再一次性落库"保证重试不产生矛盾运行 |

如果按任务卡字面把所有质检都改成异步，会同时推翻上面三处有论证的设计，
并且重新引入它们刻意规避的问题：**"提交已冻结但结论尚未产生"的窗口会被
读成"没问题"**（`submissions.py` 的注释原话）。

## 2. 候选方案

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 全面异步化 | 三处入口全部改成入队 + Worker | 推翻三处有论证的设计；提交后出现结论空窗；标注人看到空结论 |
| B. 保留同步，新增异步执行入口（本方案） | 既有入口原样保留；为"需要排队/重试/可见进度"的新场景提供 Outbox → Worker 路径 | 需要明确"哪些场景走异步"，否则两套入口会漂移 |
| C. 不做异步 | 维持现状 | V31-030 的 `lifecycle` 永远无人写入，等于建了状态机却没有执行方 |

## 3. 决策

**选方案 B。**

1. **同步路径不动**：标注提交质检、`POST /quality/runs`、`QUALITY_PRE_CHECK`
   继续同步执行并直接产出 `SUCCEEDED` 运行。它们的输入是已加载的事实，
   没有大文件 IO，同步是可解释的选择。
2. **异步路径只服务新入口**：由 Outbox 事件 `quality.run.requested` 触发的
   运行先落 `QUEUED`，由 Worker 推进 `RUNNING → SUCCEEDED/FAILED`，失败按
   `max_attempts` 重投（`RETRYING`），超限进入死信。写入只能经
   `SqlQualityRunRepository.enqueue` / `transition`，由领域状态机与数据库
   CHECK 双重把关。
3. **两条路径共用同一套计算**：Worker 执行时调用
   `QualityRunUseCases.prepare`（只算不写），再用 `transition` 落结论；
   不新写第二套规则或聚合逻辑。
4. **结论视图不受影响**：`get` / `list` / `latest_for_subjects` 仍然只认
   `SUCCEEDED`；排队中的运行只经执行路径（`find_any`）可见，直到 V31-032
   提供生命周期查询入口。
5. **并发由 CAS 兜底**：`transition` 以 `lifecycle` 做比较并交换，两个 Worker
   领取同一运行后只有先到者成功，后到者抛 `QualityRunConflictError` 并重读。

### 3.1 Worker 的事务边界：`RUNNING` 先提交再计算

异步执行必须让"执行已开始"成为**持久事实**，因此 Worker 拆成两段事务：

1. `QualityRunUseCases.start` 推进 `QUEUED`（或重投后的 `RETRYING`）→ `RUNNING`，
   **立即提交**；
2. `conclude` 读取事实、计算结论，再以 CAS 推进 `RUNNING → SUCCEEDED`，提交。

反例是"领取与计算同处一个未提交事务"：规则实现崩溃时回滚会把 `RUNNING`
一起抹掉，运行退回 `QUEUED`，而领域状态机不允许 `QUEUED → FAILED`
（`mark_failed` 要求 `started_at` 非空——未开始不能算失败）。结果是失败无法
落库，事件反复重投直到死信，运行永远停在排队态，人工无从判断它到底跑没跑过。

这条约束只适用于需要"失败可落库"的异步路径；同步入口仍然一次事务算完即
提交，`execute_queued` 保留给同事务内联执行的调用方。

## 4. 后果

- `QualityRunRepository` 端口新增 `enqueue` / `find_any` / `transition`，
  既有 `add` 的"只接受已产出结论"约束保持不变。
- 新事件必须登记进 `scripts/check_outbox_events.py` 的 `DISPATCH_EVENTS`，
  否则 `make check` 第 7 步失败——这是既有约束，不是本 ADR 新增。
- 标注域自己的 `QualityCheckRun`（`annotation_quality_runs`）**不并入**
  `quality_runs`：两者语义不同（前者是提交级规则判定，后者是对象级质量结论），
  合并需要单独的数据迁移论证，不在 V31-031 范围。
- V31-032（结论驱动路由）只消费 `SUCCEEDED` 结论，与同步路径产出的运行
  口径一致，不需要区分来源。

## 5. 残余风险

| 风险 | 说明 |
|---|---|
| 两套入口长期并存 | 新增质检场景必须显式选择同步还是异步；ADR 只定边界，不做强制。若漂移，应在评审中收敛 |
| 异步运行不可观测 | V31-032 之前，`QUEUED` / `RUNNING` 只能查库；HTTP 只暴露结论 |
| Worker 侧规则异常 | 规则实现抛错记为 `FAILED` + `error_code`，按 `max_attempts` 重投；不允许降级成"通过" |
