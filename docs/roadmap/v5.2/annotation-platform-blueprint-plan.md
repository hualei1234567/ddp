# 标注平台全面建设方案（蓝图 ↔ DDP 现状映射与实施）

> 蓝图来源：`llm_annotation_platform_archify.html`（6 大子系统 N1–N6）。
> 本文档为现状盘点、逐项映射、冲突处置与分阶段路线图，是 V5.2 阶段 B 的执行依据。
> 假设标注（无依据处的合理假设）均以 **[假设]** 显式标记。

## 一、现状盘点（实测结论）

### 1.1 技术栈与目录
- 后端：FastAPI + SQLAlchemy + Alembic（DDD 分层 api→application→domain→infrastructure）
- 前端：React 18 + TS + Vite + AntD（`apps/web/src/pages/annotation/` 15 个页面）
- 契约：OpenAPI `1.36.0`（在途 PR 至 1.37–1.39），标注相关路径 100 处
- 数据：PostgreSQL + MinIO；**无 Redis / ClickHouse / Kafka / pgvector**（实测零引入）
- 数据平台库：`data-platform/`（lerobot/canonical/hdf5/llm_export）
- 门禁：`scripts/check.sh` 10 步；迁移 head = 0074（0075–0077 已预分配给在途分支）

### 1.2 标注域已有能力（强复用点，全部实测存在）
| 能力 | 现状实现 | 蓝图对应 |
|---|---|---|
| 任务状态机 | `AnnotationTaskStatus`：ASSIGNED/SUBMITTED/ARBITRATION/ACCEPTED 全链 + 批次/分配/提交/记录四级状态 | N2 ✓ |
| 租约锁 | `assignment.py`：`lease_expires_at` + EXPIRED 状态 + 过期判定（**DB 级**） | N2（Redis 实现，见冲突 C2） |
| 双盲标注 | `review_policy.py`：`ReviewMode.DOUBLE` + `hides_labels_from()` | N2 ✓ |
| 冲突恢复/仲裁 | `arbitration.py` + ConflictError 409 | N2 ✓ |
| 一致性 IAA | `iaa.py`：Cohen's Kappa + percent agreement（多人 κ 部分有） | N3 ✓ |
| 黄金盲测题 | `gold_set.py`：blind=true 强制 | N3 ✓（熔断联动缺失，见 G3） |
| AI 预标注 | `ai_suggestion.py` + `suggestion_agreement.py`（采纳/修改/拒绝统计） | N3 ✓ |
| 抽检 | `quality_sample.py`：确定性稳定排序抽检 | N2/N3 ✓ |
| 打回重标 | review 驳回闭环 + 返工计数（overdue/workload） | N2 ✓ |
| 数据集版本快照 | `DatasetVersion.frozen_at/frozen_by` + Frozen 引用保护（V51-017） | N4 ✓ |
| 训练导出 | `ExportFormat(LE_ROBOT/CANONICAL)` + 导出校验/血缘 | N4 ✓（格式扩展见 G4） |
| 审计 | `audit_logs` + `DatasetAuditMixin` | N6 ✓ |
| 权限 | `ProjectRole` + 项目作用域依赖注入；17 unit + 19 api 测试文件 | N2 ✓ |
| SFT/DPO 导出库 | `ddp_data/llm_export`（本轮新增，第三方验证 PASS） | N4 ✓ |
| LLM 标注载体 | V52-001~004（在途 PR）：TEXT/DIALOGUE/PREFERENCE_PAIR/AGENT_TRACE | N1 ✓ |
| 统计看板 | V52-012~014（在途）：overview/breakdown + 脱敏导出 | N4 ✓ |

### 1.3 缺口清单（蓝图要求但现状没有）
| # | 缺口 | 蓝图 | 优先级 |
|---|---|---|---|
| G1 | LLM-as-Judge **偏差校正**（位置偏差 Swap&Average、长度惩罚）与**置信度路由**（高置信自动放行/灰区人工） | N3 | P1 |
| G2 | 黄金题**熔断联动**：连续准确率 <85% 自动暂停派单并冻结结算 | N3 | P1 |
| G3 | **技能标签路由**：按标注员领域标签/质量分路由高难度任务 | N2 | P2 |
| G4 | 导出格式扩展：**PRM Step-Reward JSONL**（步骤级奖励）+ GRPO rollout 格式 | N4 | P1 |
| G5 | **Bad Case 回流**：评测失败集 Webhook→自动建任务 | N5 | P2 |
| G6 | **向量语义去重**（pgvector）与难例聚类 | N1 | P3 |
| G7 | OLAP 看板（ClickHouse/Kafka） | N4 | P3（用 PG 聚合替代，见 C1） |
| G8 | Token 成本核算（预标注/Judge 按 项目×模型 统计） | N6 | P2 |
| G9 | 前端轨迹树/步骤级标注交互 + 暗水印 | N1/N2 | P2 |
| G10 | 压测保障（千人并发领单） | N6 | P3 |

## 二、蓝图功能映射表（N1–N6 → 现状/在途/缺口）

| 蓝图功能点 | 现状 | 处置 |
|---|---|---|
| N1 统一 Sample Envelope | V52-001~004 `item_type`+`item_payload`（在途） | 复用推进 |
| N1 Agent 轨迹解析/PRM 步级标注 | 载体有（AGENT_TRACE），步级 PRM 无 | G4 + P2 实施 |
| N1 向量去重 | 无 | G6（P3，pgvector 迁移） |
| N1 动态模板引擎 | `schema_presets.py` 部分覆盖 | 不新建，增量扩 |
| N2 状态机/租约/双盲/仲裁/抽检/驳回 | 全有 | 不动（防回归） |
| N2 技能标签路由 | 无 | G3 |
| N2 水印/禁复制 | 无 | G9（前端） |
| N3 规则校验/预标注/IAA/黄金题 | 全有 | 不动 |
| N3 Judge 偏差校正+置信度路由 | 无 | G1（本轮实施） |
| N3 Honeypot 熔断 | 无联动 | G2（本轮实施） |
| N4 复合检索 | 分页过滤已有（annotation_pagination） | 够用 |
| N4 不可变快照+导出 | 有 | G4 扩 PRM 格式 |
| N4 防污染(泄露) | leakage.py（HARD/SOFT 规则）已有 | 不动 |
| N4 OLAP ClickHouse | 无 | C1（替代方案） |
| N5 Bad Case 回流 | 无 | G5 |
| N5 配比台账 | DatasetVersion+血缘部分覆盖 | 增量 |
| N6 审计/备份 | audit_logs + MEM-OPS-001 演练 | 不动 |
| N6 Token 成本 | 无 | G8 |
| N6 压测 | 无 | G10（P3） |

## 三、冲突清单与处置

| 类型 | 冲突 | 处置建议 |
|---|---|---|
| **C1 架构冲突** | 蓝图 N4 要求 Kafka→ClickHouse OLAP；现状纯 PG 且无消息队列基建 | **不引入新基建**。用 PG 物化聚合表 + 既有 outbox 事件驱动增量刷新达成"亚秒级看板"的 90% 价值；ClickHouse 移入 V5.3 备选 |
| **C2 架构冲突** | 蓝图 N2 Redis 租约锁 vs 现状 DB `lease_expires_at` 已正确实现并测试覆盖 | **保留 DB 实现**（正确性已证），Redis 仅作为 P3 性能优化记录；不改核心调度 |
| **C3 数据模型冲突** | 偏好标注：蓝图"统一 JSONB Envelope" vs 仓库决策 MEM-DEC-002（偏好=JSON 约定值不加标签类型） | **一致不冲突**：V52-003 用 `item_payload` 承载 + JSON 约定值标签，两条约束同时满足 |
| **C4 接口冲突** | 蓝图"格式转换器一键导出 SFT/DPO/PRM" vs 既有 `ExportFormat(LE_ROBOT/CANONICAL)` 契约已冻结 1.36 | **纯增量**：新增枚举值（PRM_STEP_REWARD 等）+ additive 契约 1.40.0，不删旧值 |
| **C5 依赖冲突** | 蓝图向量检索依赖 pgvector/Milvus；现状 PG 未装扩展 | G6 延后到 P3；如实施需先验证本机 PG 是否有 pgvector，无则降级为"精确哈希+分词相似"近似方案 |
| **C6 排期冲突** | 蓝图 N1 载体与在途 V52-A 阶段（PR #127–130 + llm-items）重叠 | **合并执行**：以在途分支为准，蓝图映射表作为其验收依据，不重复建任务 |
| **C7 迁移编号冲突** | 多线并行都要建迁移 | 资源预分配：0075=llm-items、0076=ops-funnel、0077=analytics、**0078=judge-router、0079=honeypot、0080=prm-export** |

## 四、依赖清单

- G1（Judge 校正路由）→ 依赖 ai_suggestion（有）→ 阻塞：无；被 G8（成本）依赖
- G2（熔断）→ 依赖 gold_set（有）+ assignment/派单入口 → 阻塞：无
- G4（PRM 导出）→ 依赖 V52-004 AGENT_TRACE 载体（在途）+ llm_export writer（已并入）
- G5（回流）→ 依赖 G4 之后 + 契约 1.41；独立可后置
- G8（成本）→ 依赖 G1 上线产生 Judge 调用记录
- G9（前端轨迹树）→ 依赖 V52-004 合并 + 契约 1.38
- G3（技能路由）→ 依赖 workload/quality 分（有）；独立

## 五、分阶段建设路线图

### 阶段 0（进行中，今日闭环）
- PR #127–130 合并（V51 收尾：能力矩阵/多 Episode/Canonical 1.1/DATA_CLEANING）
- V52-A：V52-001~004 载体（迁移 0075/契约 1.38）、V52-015/016 导出库（已 PASS）、V52-012~014 统计（契约 1.39）

### 阶段 1：先基础能力（本轮实施，全部增量零破坏）
| 任务 | 内容 | 迁移 | 契约 | 测试 |
|---|---|---|---|---|
| T1 G1 | `domain/annotation/judge_routing.py`：位置偏差校正（Swap&Average 双向判定）+ 长度惩罚 + 置信度三区路由（AUTO_APPROVE≥0.92 / HUMAN 0.5–0.85 / 其余标准流） | 无（纯领域） | 无（内部能力） | 单测 ≥12 |
| T2 G2 | `domain/annotation/honeypot_breaker.py`：黄金题滑动窗口准确率，<85% 连续 N 次 → 产出 PAUSE 指令，派单用例消费 | 无（复用 gold_set） | 无 | 单测 ≥8 |
| T3 G4a | `ddp_data/llm_export` 增 `PRM_STEP_REWARD_JSONL` writer（步骤级 正确/冗余/幻觉/致命→reward 映射） | 无 | 无 | 单测 ≥8 + roundtrip |

### 阶段 2：核心标注流程增强（下一步）
- T4：PRM 步级标注域模型（Fork 分叉重写 → Step-DPO 对）+ 契约 1.40 additive
- T5：技能标签路由（assignment 过滤器）+ 迁移 0080（annotator_skill_tags）
- T6：Token 成本台账（ai_suggestion 调用记录聚合）+ 迁移 0081

### 阶段 3：增强与辅助（P3，按需排期）
- G5 Bad Case 回流 Webhook（契约 1.41）；G6 向量去重（pgvector 可用性验证后）；G9 前端轨迹树+水印；G10 压测脚本

### 实施步骤模板（每个任务）
1. worktree 新建 → 2. 领域纯函数 + 单测 → 3. 接线现有用例（可选注入，默认旧行为不变）→ 4. 契约/migration（若需，用预分配号）→ 5. `ruff`+定向 pytest → 6. check.sh（持锁串行）→ 7. commit+PR → 8. CI 绿→合并 → 9. 记忆卡片/星云

## 六、风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| Judge 路由误放行低质数据 | AUTO_APPROVE 仅当 judge 与人工一致且 ≥0.92；默认关闭需显式开启 | 特性开关关闭即回旧行为 |
| 熔断误伤正常标注员 | 窗口 ≥20 题、连续 2 轮才触发；PAUSE 可手动解除 | 删除 PAUSE 记录即恢复 |
| PRM 格式与训练侧不匹配 | 先第三方 datasets 实测（同 llm_export 方法论） | 格式版本化 manifest |
| 迁移冲突 | 编号预分配；batch 模式兼容 SQLite | 每迁移带 downgrade |
| 并行 check.sh 污染 | 全局锁串行（MEM-INC-009） | — |

## 七、验收要点（蓝图自检 → 本项目落地口径）
- N1：四种 payload 校验器全覆盖（V52 单测）；PRM 步级可标注（阶段2）；去重（P3）
- N2：领单冲突 409 + 租约过期释放（已有测试）；双盲+仲裁（已有）；技能路由（阶段2）
- N3：规则拦截（已有 quality_rules）；预标注（已有）；Judge 偏差校正+置信度路由（T1）；Kappa（已有）；Honeypot 熔断（T2）
- N4：快照+冻结（已有）；SFT/DPO/PRM 导出（llm_export）；防污染（已有 leakage）；看板（V52-012~014）
- N5：回流 Webhook（阶段3）；配比台账（增量）
- N6：审计（已有）；Token 成本（阶段2）；备份演练（已有 MEM-OPS-001）

---

## 八、实施进度（滚动更新）

- 2026-11-25 阶段1 T1：`judge_routing.py`（位置偏差校正+长度惩罚+三区路由）+18 单测 ✅
- 2026-11-25 阶段1 T2：`honeypot_breaker.py`（黄金题滑窗熔断+暂停/解除）+9 单测 ✅
- 2026-11-25 阶段1 T3：PRM Step-Reward 导出（`llm_export/prm.py`：四类标签 reward 映射、未标注步骤不进训练文件、index 连续性强制、roundtrip 校验）+12 单测 ✅（PR #131）

**阶段 1 判定（2026-11-25 晚间）：T1/T2/T3 全部完成，阶段 1 done。**
`datasets5.2.0` 导出库（SFT / 偏好对 / PRM）验证 PASS；三方真实验证证据见 `docs/testing/evidence/2026-11-25-llm-export-verify/`（PR #133）。

**阶段 2（2026-11-25 收口）**：三线并行开发（子代理模式，见 MEM-SOP-003）

- PR #134 PRM 步级标注域模型——步级标注实体、四类标签与 reward 映射、契约 1.40.0（`paths.stage10.yaml`）→ **已合并**
- PR #135 Token 成本台账——judge/PRM Token 计量与成本归集、迁移 **0081**（`ai_token_usage`，Decimal 量化 4 位小数）→ **已合并**
- PR #136 技能标签路由——按技能标签路由标注任务到对应 Judge/人工队列、迁移 **0080** → **已合并**
- 契约版本演进至 **1.40.0**（唯一 bump，其余 PR 走 fragments + `$ref` 延迟策略）
- 迁移链最终态：`0074 → 0078 → 0081 → 0080`（main 实测 head 唯一；并行期三个 PR 均以 0074 为基线导致双 head，后合方重排解决，见 MEM-INC-018）
- 本阶段沉淀 6 类真实坑点入库：MEM-INC-016（契约 parity 强制同批交付）、MEM-INC-017（Redocly 路径参数三坑）、MEM-INC-018（迁移链冲突）、MEM-INC-019（SQLAlchemy 类名全局唯一）、MEM-INC-020（SQLite batch 撞跨表触发器）、MEM-INC-021（JSONB 回读 + Decimal 金额），以及协作模式 MEM-SOP-003

**阶段 3（2026-11-25 开工 → 11-26 收口）**：三线并行，两线由主控接手收口

- **web-ui**（PR #138，已合并）：LLM 标注四类展示组件（文本/对话树/A-B 盲测双栏/轨迹树）+ 页面按 `item_type` 分发 + 暗水印；28 项前端测试，含判别联合类型与 jsdom 双轨测试范式
- **honeypot-wiring**（PR #139，已合并）：黄金题熔断**从能力变机制**——迁移 0083 `annotation_honeypot_state`；黄金题提交推进滑窗状态，连续 2 窗 <85% 自动 PAUSED 并拦截**新**派单（不收回已分配）；owner 可 resume；开关 `DDP_HONEYPOT_BREAKER_ENABLED` 默认 false = 全入口短路（零回归）；配套运营 runbook
- **badcase-backflow**（PR #140）：蓝图 N5 数据飞轮**入口**——迁移 0082 `annotation_backflow_cases`；评测失败案例回调 → 三层幂等判定（批次内/库内预查询/唯一键兜底）→ 可选合并转标注任务；限流按 source 独立计数；19 项测试
- 迁移链最终态：`0074 → 0078 → 0081 → 0080 → 0083 → 0082`（单 head，主控串行重排）

**阶段 3 判定（2026-11-26 凌晨）：UI + 熔断接线 + 数据飞轮入口三件落地。**
阶段 3 剩余 P3 级项（如实标注为未做，不影响本轮验收）：
- 向量语义去重（G6）——需 pgvector 可用性验证与容量评估，**未启动**
- 端到端压测（G10）——需 staging 环境与真实流量模型，**未启动**
- fork 自动物化端点、Step-DPO 写路径回流——阶段 2 遗留，**未接线**
- 技能标签写入 API——阶段 2 遗留（只交付读取端口与表），**未建**
- 前端偏好值提交动作接线——UI 仅草稿态（页面注释已标明边界），**未接线**

---

**阶段 4（2026-11-26 开工）：生产化与规模化**（方案见 `phase4-production-plan.md`）

收官：阶段 3 遗留 5 项全部清零
- PR #143 生产缺陷修复：回流幂等重发 409（唯一键表写拒绝行）→ 跳过判决不落库 + 回归测试
- PR #144 G10 生产只读压测基线：2100 请求 0 错误，p95 75.66ms，PG 峰值 18/100；**发现 collection-tasks N+1**
- PR #145 fork 自动物化（materialize=true 单请求落库，冲突递增）+ Step-DPO 写路径（JSON 约定值入提交链，导出兼容单测钉死）
- PR #146 G6 语义去重：生产无 pgvector → **pg_trgm 真实实现**（VECTOR 后端显式报未实现，不假装）；迁移 0084
- PR #147 技能标签写入 API（owner/本人权限矩阵）+ 前端偏好值提交接线

主线四线（对应 phase4-production-plan §三）：
- **4-1 可观测（PR #150）**：3 个 SLO 指标（回流积压/熔断暂停/近 1 小时通过提交，采集时查库、标签基数有界）+ `GET /api/v1/health/dependencies`（任一依赖异常返回 200 degraded，探活 ≤2s，不回显异常原文）
- **4-2 性能容量（PR #148）**：修复 `GET /collection-tasks` N+1（**41 → 3 次查询**，与页大小无关）；回归测试用 SQLAlchemy `before_cursor_execute` 计数真实 SQL；`performance-budget.md` 建立各域 p95 预算与超预算分级处置
- **4-3 发布纪律（本文档配套）**：`docs/ops/release-discipline.md`——迁移五步法（离线 SQL 审查 → 备份 → 一次性验证库双向往返 → 生产实跑 → 落地确认）、迁移号铁律、特性开关清单、回滚演练记录
- **4-5 运营闭环（PR #149）**：`docs/ops/` 四份 SOP（索引与角色分工 / 熔断申诉通道 / 回流受理 / 看板口径），19 个 API 路径 + 11 个环境变量 + 13 个指标名全部实证
- **4-4 生态对接（PR #151）**：`POST/POST /llm-exports` 把已就绪的 `ddp_data.llm_export`（datasets 5.2.0 验证 PASS）暴露为 API，返回制品清单（sha256/行数/格式版本）

生产验证（不设 staging 的"三重等价"）：
- 迁移：备份点 → 一次性验证库 `0074→0082→0074→0082` 与 `0082→0084→0082→0084` 往返 → 生产实跑（生产 head 已达 **0084**，5 张新表 + pg_trgm + 2 索引确认）
- 端到端演练（8 环节，证据 `docs/testing/evidence/2026-11-26-prod-e2e-drill/`）：回流受理 ✅ / 幂等 ❌→✅（抓到真实缺陷）/ 列表 ✅ / PRM 标注 ✅ / fork→Step-DPO ✅ / Token 台账 ✅ / 熔断 resume ✅ / SFT 导出 ✅
- 压测：只读红线（仅 GET、并发 ≤32、≤2000 请求、≤180s、PG 连接 >80% 中止、默认 dry-run）

**阶段 4 判定（2026-11-26）：生产化四支柱（可观测/可扩展/可运营/可审计）首轮落地。**
剩余项（如实标注，不影响本轮验收）：
- 告警规则仅文档化（`dashboard-spec.md` 口径），未接真实 Alertmanager 实例
- 写路径压测未覆盖（需数据隔离方案；生产写压风险高，本轮只做只读）
- 大表分区/归档策略仅评估未实施（需真实数据量）
- Webhook 出站（标注完成通知下游）未做

---

**阶段 5（2026-11-27）：数据飞轮生产化闭环**（方案见 `phase5-flywheel-closure.md`）

从「功能完备」到「回路闭合」——四个硬问题各有交付：

| # | 硬问题 | 交付 | 实测 |
|---|---|---|---|
| Q1 | 导出的训练数据真能被训练消费吗？ | PR #164 训练消费闭环（numpy 微型训练循环） | SFT loss **73.55 → 9.36**；DPO 判对率 **0.5 → 1.0**；同种子可复现 |
| Q2 | 业务事件下游能感知吗？ | PR #165 + #168 Webhook 出站（迁移 0085） | 签名 + 连续 5 次失败自动 PAUSED + **SSRF 守卫**（Semgrep 抓到的真实风险）+ 缺密钥报 503 |
| Q3 | 数据从哪来到哪去能追溯吗？ | PR #166 数据集版本对比 | 条目差异/统计变化/**策略指纹变化**；血缘查询复核确认既有能力（深度上限 3、节点上限 100、显式 truncated），**不重复建设** |
| Q4 | 审计最危险的空白补了吗？ | PR #163 维护任务补测 | `workers/maintenance.py` 覆盖率 **0% → 79%**（12 项测试） |

**审计执行期的额外收获**：
- PR #162 **依赖漏洞 48 → 0**（fastapi 0.115.6 → 0.133.1 跨大版本，全量 4057 passed）
- PR #160/#161 审计报告与完整结论（12 项风险逐项交代 + 4 项额外发现）
- PR #157 索引审计**推翻了审计假设**：354 个零使用索引但**0 个可删**（225 约束背书 + 129 小表 seq scan 更优）
- PR #155 监控栈真实加载（promtool SUCCESS 6 rules；真实 Prometheus 加载后规则进入 pending）

**生产环境验证（不设 staging）**：迁移 0085 五步法全过；Webhook 订阅端到端
（创建 201 / 列表无 secret / 库内存密文 + 64 位 hash / 删除 204）；演练数据已清理。
证据见 `docs/testing/evidence/2026-11-27-phase5-prod/`。

**阶段 5 判定（2026-11-27 上午）：回路闭合的四个硬问题全部有交付并有实测证据。**
剩余项（如实标注，不影响本轮验收）：
- 告警规则未接真实 Alertmanager 实例（规则已写并被真实 Prometheus 加载求值，缺接收端）
- 写路径压测未覆盖（已建立事务骨架容量基线 698→2548 TPS，真实端到端写压测需数据隔离方案）
- 大表分区/归档策略仅评估未实施（生产数据量百级）
- Webhook 未接真实下游（投递用假 transport 验证）
- OTel 分布式 trace 未接入（阶段 4-4 遗留）

---

**阶段 6（2026-11-27）：闭环剩余 5 项**（本节为本轮新增）

阶段 5 之后剩下的 5 项，本质都是**「有能力但没接通」**或**「结论缺实测」**。
本阶段逐项给出可复核的实测结论——**该做的做了，不该做的明确判定为「暂不需要」**。

| # | 剩余项 | 交付 | 实测结论 |
|---|---|---|---|
| R1 | 告警未接真实 Alertmanager 实例 | `services/alert-receiver/`（纯标准库 HTTP 服务）+ `alertmanager.yml` 占位地址替换 + compose 服务 | **链路真实闭合**：Prometheus 真实求值触发 `DdpApiDown` → Alertmanager 分组路由 → 接收端 NDJSON 落盘 → 三处计数互印；接收端挂掉不影响 Alertmanager 且恢复后**补投成功**（告警不丢）；接收端重启后历史仍在（查询读文件非内存） |
| R2 | 写路径端到端压测未覆盖 | `scripts/perf/write_path_e2e.py`（真实 HTTP 标注提交，含完整前置数据与真实 JWT） | 并发 1/4/16 → TPS **15.97 / 26.46 / 24.75**，三档**错误率 0%**；连接池红线**安全**（峰值 17 条 / 上限 100，占用 26%，余量 74） |
| R3 | 大表分区/归档仅评估未实施 | `scripts/db/table_growth_audit.py`（只读，三层护栏 + AST 负向验证） | **明确判定：暂不需要**。全库 103 表 / 37,003 行 / 30.8 MiB，最大表 12,518 行；距 5000 万行阈值差 **3994 倍**。触发阈值已钉死为代码常量（分区 5000 万行 / 50 GiB、归档 1000 万行、预警 100 万行） |
| R4 | Webhook 未接真实下游 | 迁移 **0086** `webhook_outbox` + 登记器 + `workers/webhook_outbound.py` + 生产演练脚本 | **接线完成**（阶段 5 遗留的真实缺口：投递用例 `deliver_event` 无任何生产调用点）。生产库真实 HTTP 投递 + **下游独立验签通过** + 记录标 DELIVERED + 演练数据零残留 |
| R5 | OTel 分布式 trace 未接入 | 见 `docs/adr/` 与 `docs/ops/otel-tracing.md`（阶段 6 同批交付） | 默认关闭、零开销；trace_id 贯通日志与响应头 |

### 阶段 6 的额外收获（方法论层面）

1. **「有能力无接线」比「缺能力」更隐蔽**（MEM-INC-027）：阶段 5 的投递用例
   单测全绿，但生产代码**零调用点**。新增判据：异步能力交付必须回答
   「谁在什么事务里触发它」，并有一条从业务动作到能力生效的端到端用例。
2. **PG 统计在容器重启后归零**（MEM-OPS-002）：`n_live_tup` 报 35 行、真实
   12,518 行（**偏差 358 倍**）；103 张表中 **83 张统计不可信**。审计类结论
   必须用精确 `COUNT(*)`，不能信 `pg_stat_user_tables`。
3. **出站守卫的本地例外要单独开关**（MEM-INC-028）：不得为本地联调削弱
   SSRF 守卫；按出站面分别开 `*_ALLOW_PRIVATE`，生产保持 fail-closed。
4. **HTTP 头名大小写不敏感 / SQLite 不存时区**（MEM-INC-029）：写"验证外部
   系统真的收到请求"的测试时的两个隐性陷阱。

### 阶段 6 判定（2026-11-27 晚）

5 项剩余全部有实测结论。**其中 3 项是"做完了"（R1/R2/R4），1 项是
"明确判定暂不需要并钉死阈值"（R3），1 项是能力接入（R5）**。

仍如实标注的边界（不夸大）：
- 告警「通知到人」（飞书/邮件）**未接**——只做到「发到真实接收端并落盘」；
  6 条规则中只 2 条有真实 firing 证据，`inhibit_rules` 未实测；
- Webhook 白名单只含 3 个事件类型；熔断与回流事件**不写 outbox 事件**，
  因此无法外推（需先补 outbox 事件）；
- `webhook_outbox` 无归档、无恢复订阅的 HTTP 端点、无 Prometheus 指标；
- 写路径压测是**单进程**口径（多副本需按「副本数 × 30 连接上限」重算红线）；
- 分区结论的数据窗口仅 10 天（其中 3 天为压测尖峰），有机基线只由
  **7 个日样本**支撑——这是「暂不需要」结论最大的不确定性来源。
