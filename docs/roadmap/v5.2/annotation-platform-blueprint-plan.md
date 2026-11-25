
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
