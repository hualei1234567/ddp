# ADR-0051：训练引用删除语义——CASCADE 与 append-only 守卫互斥的处置

- 状态：已接受
- 日期：2026-11-28
- 决策者：DDP V6.1 Sprint 3 代理（v61-adrw）；批准人：lead（长期授权）
- 相关任务：DDP V6.1 Sprint 3「`external_training_references` 删除语义冲突」
- 相关 ADR：[0036 DatasetVersion → TrainingRun 血缘关系](0036-training-lineage-relation.md)
- 影响范围：仅文档。本 ADR **不修改任何代码**；只把既有行为裁决为已知语义，
  并规定项目下线的替代路径（archive 而非 delete）。

---

## 1. 背景

### 1.1 冲突现象

`external_training_references` 的外键定义与删除守卫相互矛盾：

| 约束 | 位置 | 语义 |
|---|---|---|
| `project_id` 外键 `ondelete="CASCADE"` | `backend/app/infrastructure/db/models/training.py:322` | 删除 project 时级联删除引用行 |
| 触发器 `external_training_reference_delete_forbidden` | `backend/app/infrastructure/db/training_guards.py:320` | 禁止删除该表**任何行**（append-only：引用行即 run 记录，见 ADR-0036） |

删「有训练引用的项目」时，级联的 DELETE 撞上守卫触发器直接失败。
Sprint 2 迁移测试在 SQLite 与 PostgreSQL 上均实测触发
`IntegrityError: external_training_reference_delete_forbidden`。

### 1.2 影响面

- **项目删除不可达**：任何拥有 `external_training_references` 行的项目，
  `DELETE projects` 在生产上必然失败。这意味着：
  - GDPR / 数据清理场景下「删除项目」永远失败；
  - 现实中项目下线只能走其他途径（见 §3.3）。
- **迁移 0088 继承同一不可达路径**：`training_run_evidence`
  （reference RESTRICT + project CASCADE）同样依赖「项目删除级联」这条链，
  在引用守卫未放行时同样不可达。
- 无数据损坏风险：失败是显式 IntegrityError，事务回滚，不存在半删除状态。

---

## 2. 方案对比与决策

### 2.1 候选方案

| 方案 | 做法 | 优点 | 否决/采纳理由 |
|---|---|---|---|
| **A. 维持现状，文档化** | 承认「项目删除不可达」为已知行为；项目下线走 archive 而非 delete | 零代码改动、零风险、与 append-only 审计语义一致 | **采纳** |
| B. 守卫放行级联删除 | 用 session 变量 / 上下文（如 `SET LOCAL app.cascade_project_delete`）让守卫区分「人为删引用」与「项目级联删」 | 恢复 delete 可用 | 否决：引入隐式旁路状态，复杂度高且**易被滥用**——任何想删引用的代码都可以临时设一个会话变量绕过审计不可变语义，破坏 ADR-0036 的核心保证 |
| C. 去掉 CASCADE 改 RESTRICT | 外键改 RESTRICT，项目删除前置检查训练引用并要求先迁出 | 显式、无隐式行为 | 否决：改动语义最大（需迁移 + 前置检查 + 「迁出」流程定义），而本项目当前**没有真实的项目硬删除需求**；为不存在的需求付出最大改动不符合最小改动优先 |

### 2.2 决策：方案 A

理由：

- **与现实一致**：生产上该路径本就不可达，代码行为已经是「守卫赢」；
  文档化只是让事实成为契约，不引入任何新行为。
- **符合审计不可变语义**：ADR-0036 已确立「引用行即 run 记录」，
  append-only 是有意为之的系统级约束。CASCADE 在这张表上当初就是
  与守卫矛盾的遗留写法；与其解开矛盾去扩大删除能力，不如确认删除不该发生。
- **最小改动优先**：当前无真实删除需求（项目下线用 archive 已覆盖），
  B、C 都是给未来可能用不到的能力付费。
- 保留 CASCADE 本身也无害：它永远不会被触发（守卫在前拦截），
  改成 RESTRICT 才需要一次迁移；本 ADR 不为纯修饰性改动付迁移成本。

---

## 3. 后果

### 3.1 正面

- 「项目删除不可达」从隐藏的意外行为变成显式的、有编号的已知语义，
  排障与测试断言可直接引用本 ADR；
- 不引入任何迁移风险与代码改动；
- 训练引用 append-only 审计链完整保留。

### 3.2 负面与边界

- `DELETE projects` API 对含训练引用的项目返回失败——调用方必须改走 archive；
  相关错误信息应导向本 ADR 的处置路径；
- CASCADE 子句名不副实（永不触发），新读者可能误判行为，需依赖本 ADR 澄清；
- `training_run_evidence`（迁移 0088）继承了同一不可达路径：同样不构成数据风险，
  但「删项目连带删 evidence」同样不可用。

### 3.3 GDPR / 清理场景的处置建议

- **项目下线走 archive，不走 delete**：归档后项目不可见、不可新建引用，
  数据与审计链保留，满足「可解释、可追溯」优先于「物理删除」的口径；
- 若法规明确要求物理删除个人数据：
  1. 先对项目内数据做**匿名化/脱敏**（改写字段而非删行），保持引用行存在；
  2. 匿名化不足以满足时，才启用单独审批的、可审计的硬删除补偿任务——
     该任务必须临时、显式地禁用守卫（等效于方案 B 的受控形态），
     全程留痕，不允许成为常规代码路径；
- 归档流程要求：archive 标记 + 访问收紧 + 引用行保留 + 审计记录，
  与既有审计不可变语义一致。

---

## 4. 回滚

- 本 ADR 选 A，**无需代码变更，无回滚对象**；若对本裁决本身反悔，
  直接以新 ADR 覆盖语义即可。
- 未来若出现真实的硬删除需求，迁移路径：
  - **选 B**：新增迁移在守卫触发器上增加会话上下文判断
    （PG 用 `current_setting('app.cascade_project_delete', true)`，
    SQLite 用临时旁路表或重建触发器），同时为绕过路径加审计写入；
    风险点：旁路被滥用，需配代码评审红线。
  - **选 C**：迁移 0088 之后的迁移把两张表的外键改 RESTRICT，
    项目删除 API 增加前置检查（存在训练引用则 409 并提示先 archive/迁出）；
    风险点：需定义「迁出」语义，改动面最大。
  - 两者都必须由新 ADR 批准后实施，本 ADR 不预授权。
