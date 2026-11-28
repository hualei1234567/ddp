# DDP V6.1 S0 审计 A：能力核验（审计代理 A，只读）

- 日期：2026-11-28
- 仓库：`/Users/hualei/vscodeworkspace/DataPlatform` @ `c5f78be`（main，PR #171 后）
- 性质：只读审计；本文档为唯一产出
- 用例计数口径：`grep -c "def test_" <文件>`

---

## ① 10 域测试数表

| # | 域 | 代表测试文件（路径:用例数） | 域判定 |
|---|---|---|---|
| 1 | STREAM_EXTRACTION | `backend/tests/unit/test_stream_extraction_stage.py`(**9**)；`backend/tests/integration/test_stream_extraction_stage_e2e.py`(**8**) | ✅ 存在 |
| 2 | Canonical v1 | `backend/tests/unit/test_canonical_v1.py`(**26**)；`test_canonical_json.py`(**9**)；另有 projection **3** + json_schema **8** | ✅ 存在 |
| 3 | LeRobot 导出 | `data-platform/tests/test_lerobot_frame_writer.py`(**13**)；`backend/tests/unit/test_lerobot_export_writer.py`(**12**)；另有 parser **13** / v3_time_manifest **7** / event_graph_adapter **9** | ✅ 存在 |
| 4 | Judge | `backend/tests/unit/test_judge_routing.py`(**18**)；`backend/app/domain/annotation/judge_routing.py` 域模型在 | ✅ 存在 |
| 5 | 黄金题 | 无独立文件；`backend/tests/unit/test_honeypot_breaker.py`(**9**，黄金盲测题熔断 V52-007) + `backend/tests/api/test_annotation_workflow_api.py`(**18**，gold_refs=21，GoldSet 聚合/准确率) | ✅ 存在（分散于熔断器+工作流测试，无单列黄金题测试文件） |
| 6 | PRM | `data-platform/tests/test_llm_export_prm.py`(**12**)；`backend/tests/unit/test_prm_step_domain.py`(**30**)；另有 `test_annotation_prm_api.py`(**14**) | ✅ 存在 |
| 7 | SFT-DPO | `data-platform/tests/test_llm_export.py`(**17**；其中 SFT 4 例 + DPO 导出/解析用例，DPO 引用 18 处；`writers.py:1` 明示 SFT_MESSAGES_JSONL 与 PREFERENCE_DPO_JSONL 双 writer) | ✅ 存在 |
| 8 | Token 台账 | `backend/tests/unit/test_token_usage.py`(**27**)；`backend/tests/api/test_token_ledger_api.py`(**7**) | ✅ 存在 |
| 9 | Badcase 回流 | `backend/tests/domain/test_backflow_domain.py`(**8**)；`backend/tests/api/test_annotation_backflow_api.py`(**9**) | ✅ 存在 |
| 10 | ModelOps(evaluation+requirement) | `backend/tests/api/test_modelops_api.py`(**23**，含 :78 评测→反馈→需求闭环测试)；`backend/tests/unit/test_modelops_rules.py`(**14**)；另有 gaps **24** / gap_candidates **10** / gap_evidence **18** / failure_cases **11** / comparability **10** | ✅ 存在 |

---

## ② 3 个关键问题判定

### a) EvaluationRow 存在，评测指标字段 = `metrics` JSON —— **是**

`backend/app/infrastructure/db/models/modelops.py:135-186`，表 `evaluations`：
- `metrics: Mapped[dict]`（JSON，`nullable=False, default=dict`）→ **:152**
- 配套：`thresholds`(:153)、`verdict`(:154，CHECK 约束)、`report_ref`(:155)、`failure_cases`(:157-160，迁移 0061 结构化失败明细)、`baseline_evaluation_id`(:141) + `baseline_dataset_version_id`(:145，迁移 0043 跨数据集基线快照)、`error_code/error_summary`(:162-163)
- 近邻同构字段：`ModelVersionRow.metrics_summary`:111、`InferenceTaskRow.metrics`:238
- 消费面：`evaluation_routes.py` API + `apps/web/src/routes.tsx:317-319` 前端 evaluations 列表/详情页已接

### b) Badcase→DataRequirement 归因链：**真实填充；retest 有写入点** —— 两项均 VERIFIED

**列定义**：`modelops.py:303`(`source_feedback_ids`)、`:304`(`source_evaluation_ids`)、`:317`(`retest_evaluation_ids`)，均为 JSON list 非空默认。

**`source_feedback_ids` / `source_evaluation_ids` 填充点（grep 实证，3 处生产写入）**：
1. `backend/app/application/modelops/feedback.py:326-327` —— 反馈一键建需求：`source_feedback_ids=[feedback.feedback_id]`、`source_evaluation_ids=([feedback.evaluation_id] if feedback.evaluation_id else [])`，`source_type="MODEL_FEEDBACK"`
2. `backend/app/application/modelops/requirements.py:129-130` —— 手工建需求透传 command 字段（`commands.py:90-91`）
3. `backend/app/application/modelops/gap_candidates.py:111-112` —— Gap 候选一键建需求携带 `source_evaluation_ids=list(candidate.source_evaluation_ids)`

**测试断言闭环**：`backend/tests/api/test_modelops_api.py:78`(评测→反馈→需求全链测试) + `:164`(`assert requirement["source_feedback_ids"] == [feedback_body["feedback_id"]]`)；`test_modelops_gap_candidates_api.py:144,233`(source_evaluation_ids 断言)。
反向查询亦有仓储支持：`infrastructure/repositories/modelops.py:709`（按 feedback_id 反查需求）。

**`retest_evaluation_ids` 写入点：存在** —— `backend/app/application/modelops/requirements.py:312-336` `_link_retest()`：经 `apply_action` 短路处理 `link-retest`(:204)，校验评测存在/同项目/数据集版本 ∈ 需求关联版本后 append；`close` 动作在 :284 强制"关闭前必须有关联复测"（`DataRequirementNotSatisfiedError`）。API 入口 `requirement_routes.py:95-116` `POST /data-requirements/{id}/actions/link-retest`。测试：`test_modelops_api.py:197-222`（建复测评测→link-retest→close 全链）。

### c) TrainingRun 证据结构：**PARTIAL（近邻表齐、主表缺失，证据字段散落 JSON）**

无 `training_runs` 表。`backend/app/infrastructure/db/models/training.py` 全部 6 表：`training_requests`:51 / `training_request_revisions`:112 / `training_deliveries`:196 / `external_training_references`:318 / `dataset_grants`:449 / `training_adapter_callbacks`:525。逐项对照：

| 证据项 | 现状 | 位置 |
|---|---|---|
| dataset_version | ✅ 有（delivery 与 external ref 均落列） | `training.py` deliveries `dataset_version_id`(:205 附近)、external refs `dataset_version_id` |
| commit | ⚠️ 仅 `code_version:String(100)` 可空字符串，无 sha 约束 | `training.py:356`（对照：datasources.py:89 才有 40 位 commit CHECK） |
| config hash | ⚠️ 只有请求修订快照哈希 `revision_sha256`(:134) + 脱敏 `config_summary` 8KiB JSON(:354)；无训练时 config_hash 列（jobs.py:70 的 config_hash 属处理作业，与训练无关） | `training.py` |
| seed | ❌ 完全缺失（唯一 seed 在 dataset_policies.py:138，属抽样策略） | — |
| checkpoint | ❌ 缺失（`artifact_manifest_sha256`/`aggregate_sha256` 是**数据集交付**产物哈希，非模型 checkpoint） | `training.py` deliveries |
| 外部 run 引用 | ✅ `external_run_id/external_run_url/external_status` + `model_ref/evaluation_ref/artifacts` 自由 JSON | `training.py:318-356` |

血缘侧仅是图边概念：`domain/episodes/enums.py:99` 注释引用 ADR-0036 "DatasetVersion → TrainingRun"；`application/datasets/lineage.py:80` `record_training_run()` 只登记血缘边（回填见 `lineage_backfill.py`），底座仍是 external_training_references，非 run 表。与审计 D 判定一致（`docs/roadmap/v6.1/s0/D-infra-contract-frontend-risk.md`："training_run 部分（近邻已有，主表缺失）"）。
**结论：`-training evidence PARTIAL`**（V6.1 需 0087+ 迁移补结构化 `training_runs`：seed/config_hash/checkpoint/commit 均无结构化落点）。

---

## ③ V61 各项现状

> ⚠️ 口径说明：V61-xxx 编号定义**不在仓内**（`docs/roadmap/v6.1/` 仅有 s0 审计 B/C/D 三份，无 FR 矩阵）。以下按主控任务卡语境映射到最接近的既有能力判定；若主控编号另有所指，请以本表能力坐标重新对号。

| ID | 推定对应能力 | 现状 | 一行理由 |
|---|---|---|---|
| V61-011 | Badcase→需求归因链 | **VERIFIED** | `feedback.py:326-327` 真实填充 source_feedback_ids/source_evaluation_ids，API 测试 `test_modelops_api.py:164` 断言闭环 |
| V61-012 | 复测效果对比（retest） | **VERIFIED** | 写入点 `requirements.py:312-336` `_link_retest` + `:284` close 前置门禁 + API `requirement_routes.py:95-116` + 测试 `test_modelops_api.py:197-222` |
| V61-013 | TrainingRun 训练证据结构 | **PARTIAL** | 无 `training_runs` 主表；仅 external_training_references 的 JSON 摘要 + revision_sha256；seed/checkpoint/commit-hash/config-hash 无结构化落点（见 ②c） |
| V61-014 | EvaluationRow 评测指标 | **VERIFIED** | `modelops.py:135-186`，`metrics` JSON :152 + thresholds/verdict/failure_cases(0061) + 基线快照(0043)，API+前端已接 |
| V61-022 | （定义缺位，两读法并列）①Gap 候选一键建需求 ②ModelOps 前端看板 | **VERIFIED**①/**PARTIAL**② | ①`gap_candidates.py:111-112` 携带 source_evaluation_ids 建需求，测试 `test_modelops_gap_candidates_api.py:233`；②`routes.tsx:317-329` evaluations/data-requirements 页已有，Badcase 专属页缺失（与审计 D 一致） |

---

## ④ 主控事实核验补全

- 六级能力矩阵：`ddp_data/capability.py`（ADR-0048，DETECT→ROUND_TRIP）✅（审计 B 已详证）
- Canonical v1：`data-platform/ddp_data/canonical/v1.py` ✅（26+9+3+8=46 个相关用例）
- Webhook 0085+0086：迁移 head=0086（审计 D 核验 76 文件=76 步）✅
- Token 台账迁移 0081、技能路由 0080、PRM 0078（SESSION_STATE 阶段 2 记录，模型文件 `annotation_ai_token_usage.py`/`prm_annotation.py` 在库）✅

## ⑤ 收尾核验

```
$ git status --short | grep -v "docs/roadmap/v6.1" | head -3
?? .claude/SESSION_STATE.md
?? docs/ops/ddp-archify-blueprint.html
```
（除 SESSION_STATE.md 外，另有**先前已存在**的未跟踪 `docs/ops/ddp-archify-blueprint.html`——非本次审计产物，未改动。本审计仅新增 `docs/roadmap/v6.1/s0/A-capability-verification.md`。）
