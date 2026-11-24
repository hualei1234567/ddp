# V5.2 岗位职责扩展设计规划（LLM 标注与训练数据平台）

- 状态：**PROPOSED**（待用户确认）
- 来源：用户 2026-11-24 补充的 4 条岗位职责
- 定位：V5.1（具身数据生产闭环）**之上**的扩展包，不替代、不重写 V5.1
- 原则：**复用优先**。先证明现有对象无法表达，才新增（沿用 V5.1 蓝图 §3）

---

## 一、条款逐条拆解

| # | 原文要点 | 拆解后的能力需求 |
|---|---|---|
| **①** | 标注平台后端建设：搭建任务调度、权限、质检等能力，支撑**文本、对话、偏好对比、Agent 轨迹**等 LLM 标注场景，提升标注效率与数据质量 | 4 大能力：任务调度（已有）、权限（已有）、质检（部分有）、**4 种 LLM 标注场景（全缺）** |
| **②** | 智能化质检：实现**预标注、LLM-as-judge 自动审核、一致性校验、抽检**等能力，降低人工成本 | 预标注（有 AI 建议骨架）、**LLM-as-judge（缺）**、一致性校验（已有 IAA）、抽检（已有） |
| **③** | 内部工具后端：开发**数据查询、统计、导出接口**，支撑**评测看板、数据分析**等内部工具后端 | 查询（部分有）、**统计（缺）**、导出（仅训练格式，缺分析格式）、**评测看板（缺）** |
| **④** | 训练数据保障：和算法、标注团队协作，**迭代标注流程**，保障 **SFT / 偏好对齐**等训练数据稳定产出 | **SFT 导出（缺）**、**偏好对导出（缺）**、流程迭代（部分有） |

---

## 二、S0 式只读审计（现有能力对照）

### 2.1 已具备、可直接复用

| 能力 | 实现路径 | 复用价值 |
|---|---|---|
| 任务调度/分配/负载 | `domain/annotation/task.py`、`assignment.py`、`workload.py`（V5.1 已闭环负载提示） | ⭐⭐⭐ 直接复用 |
| 权限矩阵 | `domain/identity/models.py:46` `ROLE_PERMISSIONS` | ⭐⭐⭐ 直接复用 |
| 冲突恢复 | PR #107（V5.1 已完成） | ⭐⭐⭐ 直接复用 |
| **IAA 一致性校验** | `domain/annotation/iaa.py`：`Span`/`iou`/`IAAComputation`/`IAAMetricResult` | ⭐⭐⭐ **条款②直接命中** |
| **抽检** | `domain/annotation/quality_sample.py`：`select_quality_candidates` + seed 稳定排序 | ⭐⭐⭐ **条款②直接命中** |
| AI 建议骨架 | `domain/annotation/ai_suggestion.py`：`model_id`/`model_version`/`prompt_version` 三键必填 + 采纳/修订/拒绝留痕 | ⭐⭐⭐ **LLM-as-judge 的天然载体** |
| QC 规则引擎 | `domain/annotation/quality_rules.py`：`QualityRule`/`QualityRuleRegistry` | ⭐⭐ 可扩展规则类型 |
| 标注 Schema | `domain/annotation/schema.py`：10 种 `AnnotationLabelType` 含 `FREE_TEXT`/`STRING`/`JSON` | ⭐⭐⭐ **能表达文本/偏好标注值** |
| Outbox/Worker | 13 种投递类事件 + 幂等 + 死信 | ⭐⭐⭐ 异步调度复用 |
| Ops 监控 | `/ops/dispatch-queue`（PR #97） | ⭐⭐ 扩展指标 |

### 2.2 关键缺口（**实测确认**）

| 缺口 | 实测证据 | 严重度 |
|---|---|---|
| **无偏好对比标注** | `grep preference/pairwise/ranking` 在 annotation 域 = **0 命中** | 🔴 高 |
| **无对话标注** | `grep dialogue/conversation` = **0 命中** | 🔴 高 |
| **无 Agent 轨迹标注** | `grep trajectory` = **0 命中** | 🔴 高 |
| **无 LLM 能力** | `grep llm/judge` 命中均为误报（`endpoint` 含 `dpo`、`judged` 是英文词） | 🔴 高 |
| **无 SFT/偏好导出格式** | `ExportFormat` 仅 `LE_ROBOT`/`CANONICAL` | 🔴 高 |
| **无统计/分析接口** | `api/v1/` 无 `statistics`/`analytics`/`dashboard` | 🟠 中 |
| **无评测看板后端** | 同上 | 🟠 中 |
| 文本标注 UI 缺失 | 前端仅有视频/时间轴组件 | 🟠 中 |

### 2.3 一个重要的有利事实

`AnnotationLabelType` 已有 **`FREE_TEXT` / `STRING` / `JSON`**，`AnnotationLevel` 已有 `TASK`/`EPISODE`/`TIME_RANGE`/`ACTION_SEGMENT`/`FRAME`/`OBJECT_TRACK`。

**这意味着「文本/对话/偏好」的标注值本身可以用现有 Schema 表达**——缺的不是值类型，而是：
- 承载文本/对话的**媒体类型**（现有只有 Episode/视频）
- **偏好对比**的成对结构（chosen/rejected 二元组）
- **Agent 轨迹**的结构化表示
- 对应的**UI 与导出**

---

## 三、设计策略：三层复用

```
┌─────────────────────────────────────────────────┐
│ L3 导出层：SFT / 偏好对 / Agent 轨迹格式          │  ← 新增（ExportFormat 扩展）
├─────────────────────────────────────────────────┤
│ L2 能力层：LLM-as-judge / 预标注 / 统计 / 看板    │  ← 部分新增（复用 AI 建议 + QC）
├─────────────────────────────────────────────────┤
│ L1 载体层：文本/对话/偏好/轨迹 作为"可标注对象"    │  ← 需扩展（新领域对象或复用 SampleSet）
└─────────────────────────────────────────────────┘
      ↓ 复用 ↓
┌─────────────────────────────────────────────────┐
│ L0 基础设施：Task/Assignment/Schema/Submission/   │  ← 完全复用，不改
│    QC/IAA/抽检/权限/冲突恢复/Outbox/Worker        │
└─────────────────────────────────────────────────┘
```

**核心判断：L0 完全复用，L1 需新增载体，L2/L3 是主要新开发。**

---

## 四、详细设计规划

### 4.1 条款①：4 种 LLM 标注场景

#### 关键设计决策：**新增"可标注对象"抽象，还是复用现有 SampleSet？**

先查现有：`domain/annotation/sample_set.py` 已有 SampleSet（样本集合）。

| 方案 | 做法 | 评价 |
|---|---|---|
| **A（推荐）** | 新增轻量 `AnnotatableItem` 概念（type: TEXT/DIALOGUE/PREFERENCE_PAIR/AGENT_TRACE + payload_ref），挂到现有 SampleSet 下 | 复用 SampleSet/Submission/QC 全链；只加一层"对象类型" |
| B | 为每种场景新建独立领域（TextTask/DialogueTask/...） | ❌ 违反「不新增顶层模块」 |
| C | 强行塞进 Episode | ❌ 语义不符，Episode 有视频/时间轴约束 |

**采用 A。** 新增一个**判别字段**而非新领域。

#### 4.1.1 四类对象的结构

| 类型 | 结构 | 复用点 |
|---|---|---|
| **TEXT** | `{text, source_ref, language, metadata}` | Schema 用 `FREE_TEXT`/`STRING` 标签 |
| **DIALOGUE** | `{turns: [{role, content, ts}], context_ref}` | 每 turn 可标注；复用 `TIME_RANGE` 思想做 turn 索引 |
| **PREFERENCE_PAIR** | `{prompt, chosen, rejected, criteria[], annotator_rationale}` | **新结构**：成对比较 + 理由 |
| **AGENT_TRACE** | `{steps: [{action, observation, tool, result}], task_goal, success}` | 复用 `ACTION_SEGMENT`/`OBJECT_TRACK` 语义 |

#### 4.1.2 偏好对比的特殊性（**最重要的新设计**）

偏好标注不是"给一个值"，而是**成对判断**：
```
给定 prompt P 与两个响应 (A, B)：
  标注结果 = {winner: A|B|TIE, confidence, rationale, criteria: [helpfulness, harmlessness, ...]}
```

**为什么不能只用现有 Schema**：
- 现有 `AnnotationSubmission` 是「标签键 → 值」映射，无「二元组比较」语义
- 需要：`pair_id`、`winner` 枚举、`criteria` 多维度评分、`rationale` 自由文本
- 需要**位置随机化**（避免位置偏差，A/B 顺序随机且记录）

**建议新增**：`PreferencePairLabel` 值结构（作为 `JSON` 类型标签的**约定值规范**，而非新标签类型），或新增 `PREFERENCE_PAIR` 标签类型（需 ADR）。

#### 4.1.3 验收
- 四类对象均可创建任务 → 分配 → 标注 → 提交 → QC → 审核全链路
- 偏好对必须记录 A/B 随机化种子与位置，可复现
- Agent 轨迹的 step 顺序不可错乱

---

### 4.2 条款②：智能化质检

#### 4.2.1 预标注（Pre-annotation）

| 项 | 设计 |
|---|---|
| 复用 | `AiSuggestion`（已有 `model_id`/`model_version`/`prompt_version` 三键必填） |
| 新增 | 批量预标注任务：对 SampleSet 内 N 条调用模型 → 生成 AiSuggestion 草稿 |
| 红线 | **预标注不是最终答案**。必须保留 `source=AI_ASSISTED`，人工确认后才算提交（现有语义已正确） |
| 幂等 | 同 input + model_version + prompt_version 不重复生成（复用复合幂等键设计） |

#### 4.2.2 LLM-as-judge 自动审核（**新能力**）

| 项 | 设计 |
|---|---|
| 载体 | 复用 `AiSuggestion` 结构，`model_id` 记为 judge 模型 |
| 新增 | `JudgeRun`：对待审核 Submission 跑 judge prompt → 输出 {verdict: PASS/REJECT/UNCERTAIN, score, reasons[]} |
| 复用 | `QualityRule` 引擎（judge 作为一个规则类型）、QC Worker、批量重跑（PR #87） |
| **红线** | 蓝图 §5 精神：**judge 只提供证据与建议，不自动代替 Reviewer**。`verdict=REJECT` 必须人工确认才触发返工 |
| 一致性 | judge 与人工结论偏差率纳入监控（条款②"一致性校验"的延伸） |
| 防滥用 | judge 模型身份、prompt 版本、温度等全部记录；同一 submission 多次 judge 结果保留历史 |

#### 4.2.3 一致性校验（**已有，扩展**）

| 项 | 现状 | 扩展 |
|---|---|---|
| IAA | `iaa.py` 有 Span IoU / IAAComputation / IAAObservation | 增加**文本类指标**：Cohen's Kappa、Krippendorff's Alpha（现有是 span-based，文本需 categorical） |
| 采样 | `quality_sample.py` 有稳定 seeded 抽检 | 复用，增加按场景分层抽检 |

#### 4.2.4 抽检（**已有**）

复用 `select_quality_candidates` + `QualitySampleType`，扩展样本类型枚举覆盖新四类对象。

#### 4.2.5 验收
- judge 结果与人工结论偏差率可统计
- judge 不得自动改变 submission 状态（须人工确认）
- 预标注生成后人工确认率、采纳率可查

---

### 4.3 条款③：内部工具后端

#### 4.3.1 数据查询接口

| 项 | 设计 | 复用 |
|---|---|---|
| 通用查询 | 复用现有分页/游标模式（`annotation_pagination.py`） | ⭐⭐⭐ |
| 过滤维度 | 项目/任务/标注员/时间/状态/标签键 | 复用 |
| **禁止** | 不新建"查询中心"；在现有资源端点加过滤参数 | — |

#### 4.3.2 统计接口（**新增**）

蓝图 §19 已有「数据可用性漏斗」思路，扩展为 LLM 标注口径：

| 指标类 | 指标 |
|---|---|
| 产出量 | 提交数、有效标注数、按场景/标注员/日期 |
| 质量 | QC 通过率、judge PASS 率、IAA 分数分布、返工率 |
| 效率 | 单条耗时、冲突率、草稿恢复率、预标注采纳率 |
| 成本 | AI 调用次数/token（若有）、人工工时估算 |

**设计约束**（沿用蓝图 §19.3）：每个指标必须有 `definition`/`query`/`window`/`dimensions`/`known_limitation`。**禁止无定义分母的百分比**。

#### 4.3.3 导出接口（**新增分析格式**）

现有 `ExportFormat` 只有训练格式（`LE_ROBOT`/`CANONICAL`）。新增：
- `ANALYTICS_CSV`：扁平化标注结果，供 Excel/BI
- `ANALYTICS_JSON`：结构化，供内部分析工具

**关键**：分析导出**不是训练导出**——必须走不同格式枚举，避免"分析数据被当训练数据喂模型"。

#### 4.3.4 评测看板后端

| 项 | 设计 |
|---|---|
| 复用 | `/ops/dispatch-queue`（队列健康）+ 新增统计端点 |
| 新增 | `GET /analytics/overview`：一次返回核心指标卡 |
| 不做 | ❌ 不新建前端 BI 系统；只提供只读 API，前端可独立消费 |
| 权限 | 项目负责人口径（沿用 `/ops` 的 `get_owner_scope`） |

#### 4.3.5 验收
- 统计口径有 definition 文档且与真实 SQL 一致
- 导出的分析数据不含敏感字段（脱敏）
- 看板 API 只读、无 payload 泄漏

---

### 4.4 条款④：训练数据保障（SFT / 偏好对齐）

#### 4.4.1 SFT 导出格式（**新增**）

主流 SFT 格式：
| 格式 | 结构 | 优先级 |
|---|---|---|
| **OpenAI messages JSONL** | `{"messages":[{"role","content"},...]}` | ⭐⭐⭐ 最通用 |
| Alpaca | `{"instruction","input","output"}` | ⭐⭐ |
| ShareGPT | `{"conversations":[{"from","value"}]}` | ⭐⭐ |

**建议**：先做 `SFT_MESSAGES_JSONL`（OpenAI 兼容），其余按需。

#### 4.4.2 偏好对导出格式（**新增**）

| 格式 | 结构 | 优先级 |
|---|---|---|
| **DPO/ORPO 标准** | `{"prompt","chosen","rejected"}` JSONL | ⭐⭐⭐ |
| HH-RLHF | 同上 + `chosen_score`/`rejected_score` | ⭐⭐ |
| 带理由版 | 加 `rationale`/`criteria` | ⭐⭐ |

**建议**：`PREFERENCE_DPO_JSONL`，含 `prompt`/`chosen`/`rejected`，可选 `rationale`。

#### 4.4.3 关键红线（沿用 V5.1 精神）

- **不得把未审核通过的标注导出为训练数据** —— Dataset Gate 必须校验 QC/Review 状态
- **导出必须带 provenance**：dataset_version → submission → annotator → judge/expert 走向
- **偏好对的 A/B 位置必须随机化**，否则模型学到位置偏差（这是真实工程陷阱）
- **不得混淆 SFT 与偏好数据**：两者是不同的训练用途，导出格式与 Gate 条件不同

#### 4.4.4 流程迭代支持

| 项 | 设计 |
|---|---|
| Schema 版本化 | 已有（`AnnotationSchemaStatus` DRAFT→PUBLISHED→DEPRECATED） |
| 标注指南版本 | 新增：每次 Schema 变更附 `guideline_version` + 变更说明 |
| 一致性追踪 | 复用 IAA；按 guideline_version 分组对比前后一致性 |
| 返工闭环 | 复用现有 QC → Rework 流转 |

#### 4.4.5 验收
- SFT/偏好导出产物能被对应训练框架读取（需真实训练脚本验证，或至少 schema 校验）
- 冻结 Dataset 后才能导出（复用现有 Gate）
- 导出产物带完整 provenance

---

## 五、新增 FR 矩阵（V5.2，24 项）

| ID | 功能 | 条款 | 优先级 | 基线 | 完成证据 |
|---|---|---|---|---|---|
| V52-001 | 文本标注对象（TEXT） | ① | P0 | MISSING | 创建→标注→提交→QC 全链 |
| V52-002 | 对话标注对象（DIALOGUE） | ① | P0 | MISSING | 多 turn 标注 + turn 定位 |
| V52-003 | 偏好对比标注（PREFERENCE_PAIR） | ① | P0 | MISSING | 成对结构 + A/B 随机化 + 理由 |
| V52-004 | Agent 轨迹标注（AGENT_TRACE） | ① | P0 | MISSING | step 序列 + 工具调用标注 |
| V52-005 | 四类对象的任务调度与分配 | ① | P0 | 部分 | 复用 Task/Assignment，验证四类均可 |
| V52-006 | 四类对象的 QC 接入 | ① | P0 | 部分 | 复用 QC Worker，验证规则可配 |
| V52-007 | 预标注批量任务 | ② | P0 | MISSING | AiSuggestion 批量生成 + 采纳率统计 |
| V52-008 | LLM-as-judge 自动审核 | ② | P0 | MISSING | JudgeRun + 人工确认门禁 |
| V52-009 | judge 与人工一致性追踪 | ② | P1 | MISSING | 偏差率统计 |
| V52-010 | 文本类 IAA 指标（Kappa/Alpha） | ② | P1 | PARTIAL | 现有 span IoU + 新增 categorical |
| V52-011 | 分层抽检（按场景） | ② | P1 | PARTIAL | 扩展现有 seeded 抽检 |
| V52-012 | 标注统计接口 | ③ | P0 | MISSING | 指标 definition + 真实 SQL |
| V52-013 | 分析导出 CSV/JSON | ③ | P0 | MISSING | 脱敏 + 格式校验 |
| V52-014 | 评测看板只读 API | ③ | P1 | MISSING | 核心指标卡 |
| V52-015 | SFT 导出（Messages JSONL） | ④ | P0 | MISSING | 官方格式可解析 |
| V52-016 | 偏好对导出（DPO JSONL） | ④ | P0 | MISSING | 官方格式可解析 |
| V52-017 | 训练数据 Gate（QC/Review 校验） | ④ | P0 | 部分 | 未通过不得导出 |
| V52-018 | 导出 provenance | ④ | P0 | PARTIAL | 完整链路追溯 |
| V52-019 | 标注指南版本化 | ④ | P1 | MISSING | guideline_version + 变更说明 |
| V52-020 | 按指南版本的一致性对比 | ④ | P2 | MISSING | 分组 IAA |
| V52-021 | 文本标注前端 | ① | P0 | MISSING | 文本/对话渲染 + 标注交互 |
| V52-022 | 偏好对比前端 | ① | P0 | MISSING | A/B 并排对比 + 选择 |
| V52-023 | Agent 轨迹前端 | ① | P1 | MISSING | step 时间线渲染 |
| V52-024 | 评测看板前端 | ③ | P2 | MISSING | 指标可视化 |

---

## 六、执行 DAG

```
阶段 A（P0，无外部依赖，可并行）
├─ A1 V52-001/002 文本/对话对象（后端载体）
├─ A2 V52-003/004 偏好/轨迹对象（后端载体）
├─ A3 V52-015/016 SFT/偏好导出格式
└─ A4 V52-012/013 统计 + 分析导出
      ↓
阶段 B（P0，依赖 A）
├─ B1 V52-005/006 四类对象接 Task/QC（复用）
├─ B2 V52-007/008 预标注 + LLM-as-judge
├─ B3 V52-017/018 训练 Gate + provenance
└─ B4 V52-021/022 文本/偏好前端
      ↓
阶段 C（P1）
├─ C1 V52-009/010/011 一致性 + 分层抽检
├─ C2 V52-014 看板 API
├─ C3 V52-019 指南版本化
└─ C4 V52-023 轨迹前端
      ↓
阶段 D（P2）
└─ D1 V52-020/024 深度一致性对比 + 看板前端
```

**并行度**：阶段 A 四项可完全并行（不同文件域）。

---

## 七、API / DB 影响评估

### 7.1 DB 影响

| 变更 | 类型 | 说明 |
|---|---|---|
| 可标注对象类型 | **新增列** | 在 SampleSet/Sample 上加 `item_type` 枚举（TEXT/DIALOGUE/PREFERENCE_PAIR/AGENT_TRACE） |
| 偏好对结构 | **新增值规范** | 优先用 `JSON` 标签 + 约定 schema，避免新表 |
| JudgeRun | **新增表**（可能） | 若复用 AiSuggestion 不够，需 `judge_runs` 表记录 verdict |
| 统计 | 无 | 纯查询，不加表 |
| 导出格式 | **枚举扩展** | `ExportFormat` 加值 |

**原则**：能用 JSON 标签表达的不加表；必须加表时先证明现有无法承载。

### 7.2 API 影响

| 端点 | 类型 | 说明 |
|---|---|---|
| SampleSet 创建 | 扩展 | 加 `item_type` 参数 |
| 偏好对提交 | 新增 | `POST /annotation-submissions` 支持成对值 |
| `POST /judge-runs` | 新增 | 触发 LLM judge |
| `GET /analytics/*` | 新增 | 统计与看板 |
| `POST /exports` | 扩展 | 新格式枚举 |

---

## 八、与 V5.1 的关系

| 维度 | V5.1 | V5.2（本规划） |
|---|---|---|
| 数据对象 | 机器人 Episode（视频/传感器） | LLM 文本/对话/偏好/轨迹 |
| 训练格式 | LeRobot（具身） | SFT / DPO（LLM） |
| 质检 | 规则 + 人工 | **+ LLM-as-judge** |
| 共性基础设施 | Task/Assignment/Schema/Submission/QC/IAA/权限/Outbox | **完全复用** |
| 是否冲突 | 无 | 无——L0 层共用，L1+ 各扩展 |

**结论：V5.2 是 V5.1 的**平行扩展包**，不是替代。两者共享 L0 基础设施，可并行推进。**

---

## 九、需要确认的决策

| # | 决策 | 选项 | 推荐 |
|---|---|---|---|
| 1 | 是否新增 V5.2 版本线 | 是 / 并入 V5.1 | **新增 V5.2**（职责明确不同） |
| 2 | 可标注对象实现方式 | 新领域 / 复用 SampleSet + item_type | **复用 + item_type** |
| 3 | 偏好对结构 | 新标签类型 / JSON 约定值 | **JSON 约定值**（不动 Schema 枚举） |
| 4 | LLM provider | 内部模型 / 外部 API / 都要 | 需确认（影响 AiSuggestion 扩展） |
| 5 | SFT 格式优先级 | OpenAI messages / Alpaca / ShareGPT | **OpenAI messages 先做** |
| 6 | 偏好格式 | DPO 标准 / HH-RLHF / 带理由 | **DPO 标准 + 可选理由** |
| 7 | 优先级 | 全部 P0 并行 / 先 A 阶段 | **先阶段 A**（载体 + 导出，无外部依赖） |
| 8 | 与 V5.1 排期 | V5.1 完成后 / 并行 | **并行**（L0 共用但 L1+ 文件域不重叠） |

---

## 十、诚实边界（本规划阶段）

- 本文件是**设计规划**，不是实现。所有"已具备"项有代码路径，所有"缺失"项有 `grep` 实测。
- **未做**：真实 LLM 调用验证（需模型/API 确认）。
- **未做**：真实文本/对话数据集盘点（需用户提供样本或指定来源）。
- 若后续要做**真实 LLM-as-judge 验收**，需要有许可的模型与真实 judge 输出，否则只能做契约级 + stub 验证（并明确标注）。
