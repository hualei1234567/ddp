# DDP V5.1 验收报告（2026-11-25 早间验收）

- 验收时间：2026-11-25 10:20（Asia/Shanghai）
- 基线：`main @ 6a7f266`，契约 **1.36.0**，迁移 **0073**
- 本轮范围：V5.1 全面建设 —— 真实数据物化闭环 + 训练就绪 + 部署推进
- 证据等级：**E1（实跑）/ E2（代码核实）**

---

## 一、验收结论摘要

| 维度 | 结果 |
|---|---|
| **全链路真实测试** | ✅ **PASS**（真实 RoboMimic 36.7MB → MinIO → 物化 → 3 个产物） |
| **GATE-2 训练就绪** | ✅ **PASS**（官方 `lerobot==0.3.3` 逐帧读取 59 帧真实数据） |
| **部署推进** | ✅ 数据库迁移 **0069 → 0073**（4 个迁移成功应用） |
| **质量门禁** | ✅ main 上 `check.sh` **10 步全过** |
| **PR 交付** | ✅ 7 个 PR 全部 CI 五项全绿后合并 |
| **仓库卫生** | ✅ 单分支、零 worktree、零开放 PR |

---

## 二、7 个 PR 交付清单（早间批次）

| PR | 类型 | 内容 | 关键成果 |
|---|---|---|---|
| **#118** | feat | STREAM_EXTRACTION 阶段 | 首次从真实 payload 产出三件套 |
| **#119** | docs | 记忆批次 2 | 3 类真实教训入库 |
| **#120** | feat | 编排器接线 | **物化链路真正打通**（此前阶段未接入） |
| **#121** | feat | Canonical 1.1 | 补齐 8 个必需字段，1.0 仍可读 |
| **#122** | feat | **LeRobot 逐帧导出** | **GATE-2 PASS**：`METADATA_ONLY` → `LOADER_FRAME` |
| **#123** | docs | 全链路证据 + 部署验证 | 迁移 0069→0073 + 6 项检查全过 |
| **#124** | docs | 记忆批次 3 | Pipeline 版本冲突教训 |

## 二·补、当日下午 6 个 PR 交付清单（对照 §8 P0/P1 全部闭环）

> 基线 `6a7f266 → 17a684b`（2026-11-25 下午，`git log --oneline 4344352..17a684b`）。

| PR | 类型 | 内容 | 闭环的 §8 事项 | 证据 |
|---|---|---|---|---|
| **#126** | fix | 补齐 `processing_*` 四类枚举 CHECK 约束（迁移 **0074**） | **P0** Worker `process_once()` 落库 ✅ | 修复后单次 `process_once()` 完整跑通 9 个阶段，`processing_runs.status=SUCCEEDED`，**落库 66 条 `processing_artifacts`**（含 3 个真实逐帧 NDJSON）；MEM-INC-012 |
| **#127** | feat | ADR-0048 Adapter 六级能力分级矩阵落地 | **P1** Adapter 六级能力矩阵 ✅ | `data-platform/ddp_data/capability.py` + `scripts/check_adapter_capability.py` + 403 行单测 |
| **#128** | feat | 多 Episode 逐帧 LeRobot 数据集组装 + NDJSON 桥接验证 | **P1** 多 Episode 逐帧数据集 ✅ | 5 个真实 demo `[59,58,57,55,51]` 共 **280 帧**，官方 `lerobot==0.3.3` 全集+子集双模式 PASS（`docs/testing/evidence/2026-11-25-lerobot-frame-verify/`） |
| **#129** | feat | Canonical 1.1 补齐 Observation/Action 字段（ADR-0049 扩展）+ 记忆星云同步脚本 | **P0** Observation/Action 字段补齐 ✅ | `_ADDED_IN_1_1_BY_ENTITY` 按实体裁剪；`action.v1.schema.json` / `observation.v1.schema.json` 重导出；MEM-INC-012/013/014 入库 |
| **#130** | feat | DATA_CLEANING 确定性 FLAG 清洗规则与版本化报告 | **P1** DATA_CLEANING FLAG 规则（V51-006）✅ | `cleaning_rules.py` / `cleaning_evaluators.py` / `cleaning_stage.py` + 497 行单测 |
| **#131** | feat | V52-006/007/017：Judge 偏差校正路由 + 黄金题熔断 + PRM 步级奖励导出 | （V5.2 阶段1，见 §8·补） | 39 个单测（18+9+12），`docs/roadmap/v5.2/annotation-platform-blueprint-plan.md` 进度登记 |

---

## 三、全链路真实测试（E1）

### 3.1 样本

| 项 | 值 |
|---|---|
| 来源 | HuggingFace `amandlek/robomimic` rev `74fa018` |
| 文件 | `v1.5/lift/ph/demo_v15.hdf5` |
| 大小 | **36,699,586 B** |
| SHA256 | `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` |
| 许可 | MIT，非 gated |
| 真实内容 | 200 demos / 9,666 帧 / actions(59,7) / states(59,32) / 真实 timestamp |

### 3.2 实测链路

```
真实 HDF5 (36.7MB, SHA256 校验通过)
  → MinIO 上传（36,699,586B 确认）
  → STREAM_EXTRACTION 阶段执行
  → 3 个 Artifact 产出：
       0000.ndjson 78,343B  streams=2  action_source=PRESENT
       0001.ndjson 75,238B  streams=2  action_source=PRESENT
       0002.ndjson 73,737B  streams=2  action_source=PRESENT
  → 共 174 条 observation + 174 条 action，可从 MinIO 读回
```

**6 项检查全 PASS**：上传 / 执行成功 / 产出 Artifact / 类型正确 / 有 checksum / 可读回

### 3.3 GATE-2 逐帧训练就绪（E1，最关键）

官方 `lerobot==0.3.3` Loader 逐帧读取真实数据：

| 检查项 | 结果 |
|---|---|
| Loader 构造 | ✅ PASS |
| 总帧数真实（>1） | ✅ **59 帧** |
| fps 真实（>1） | ✅ **20.0 Hz** |
| `observation.state` 维度 | ✅ **32 维** |
| `action` 维度 | ✅ **7 维** |
| 全部帧可逐帧读取 | ✅ **59/59** |
| `loadable_scope` | ✅ `LOADER_FRAME` |

---

## 四、S0 审计缺口的闭合情况

| S0 发现的问题 | 本轮处理 | 状态 |
|---|---|---|
| `processing_artifacts` = **0 行** | 编排接线 + 全链路测试 → 下午迁移 0074 补齐约束（PR #126） | ✅ **已闭环**（落库 66 条真实产物，见 §6） |
| 所有物化阶段 **SKIPPED** | STREAM_EXTRACTION 接入并成功执行 | ✅ 已解决 |
| SensorStream/Observation/Action **零实例化** | 产出 174 obs + 174 action | ✅ 已解决 |
| 无真实样本 | 下载 RoboMimic 并 SHA256 校验 | ✅ 已解决 |
| **LeRobot 仅 METADATA_ONLY** | 逐帧 writer + GATE-2 验证 | ✅ **已升级 LOADER_FRAME** |
| Canonical 字段缺 10 类 | 1.1 补齐 8 个 SensorStream 字段 → 下午补齐 Observation/Action（PR #129） | ✅ **已补齐**（1.0 仍可读） |
| Adapter 无六级分级 | ADR-0048 已提交 → 下午落地能力矩阵与校验（PR #127） | ✅ **已落地** |

---

## 五、部署验证（E1）

| 步骤 | 结果 |
|---|---|
| 迁移 0069 → 0073 | ✅ 4 个迁移成功应用 |
| `quality_findings` 新增 4 列 | ✅ 全部存在 |
| `edge_agents` 四表 | ✅ 全部存在 |
| PG 表数 | 96 |
| 应用装配 | ✅ OK（processing 路由 6 条） |
| MinIO | ✅ healthy |
| main 上 `check.sh` | ✅ **10 步全过** |

---

## 六、诚实边界（不得扩大宣称）

> **2026-11-25 晚间更新**：仅闭环下表第一行（迁移 0074 + Worker `process_once()` 落库，PR #126）；
> 其余边界**原样保留**，一律不得据此扩大宣称。

| 项 | 状态 | 说明 |
|---|---|---|
| **`processing_artifacts` 表 0 行** | ✅ **已闭环**（2026-11-25 下午，PR #126） | 根因是 V51-001 新增 `STREAM_EXTRACTION` 枚举后无人同步数据库 CHECK 约束（见 MEM-INC-012）。迁移 **0074** 一次性补齐 `processing_runs` / `processing_run_steps` / `processing_artifacts` 三张表的 `stage` 约束与 `kind` 约束共四处；修复后 Worker 单次 `process_once()` 完整跑通 9 个阶段，`processing_runs.status = SUCCEEDED`，**落库 66 条真实 `processing_artifacts`**（含 3 个真实逐帧 NDJSON）。 |
| **视频帧读取** | ❌ 未验证 | RoboMimic `ph` 版本不含图像，`video_provided=false` |
| **真实硬件采集** | ❌ `HARDWARE_CAPTURE_NOT_TESTED` | 无采集硬件 |
| **多 Episode 数据集** | ⚠️ 部分 → ✅ 已验证（PR #128） | 3 个 demo 已验证；**下午补验**：5 个真实 demo（`[59, 58, 57, 55, 51]` 共 **280 帧**）经 STREAM_EXTRACTION NDJSON 桥接，官方 `lerobot==0.3.3` 全集（280 帧）与子集（167 帧）双模式 PASS |
| **真实训练脚本消费** | ❌ 未验证 | `VERIFIED_FOR_TRAINING_PIPELINE` 需目标训练环境 |
| **Observation/Action Canonical 字段** | ⚠️ 未补齐 → ✅ 已补齐（PR #129） | ADR-0049 扩展落地；1.0 产物仍可读，`_ADDED_IN_1_1_BY_ENTITY` 按实体裁剪 |
| **Adapter 六级分级** | ⚠️ ADR only → ✅ 已落地（PR #127） | `ddp_data/capability.py` 六级枚举 + 证据存在性校验脚本 + 单测 |
| **数据清洗** | ❌ 仍 SKIPPED → ✅ 已落地（PR #130） | V51-006 确定性 FLAG 清洗规则与版本化报告 |
| 口径 | `PUBLIC_DATASET_INTEGRATION_TESTED` | **不是**真实硬件 Pilot |

---

## 七、记忆系统（本轮新增基建）

- 上线「记忆星云」系统：**17 张卡片**，全部 ACTIVE，`check_ref` 全部有效
- 接入 `check.sh` **第 10 步**，CI 强制校验
- **3 条反向验证**：故意注入悬空 check_ref / 索引漂移 / 缺字段，校验器均正确 exit 1
- 自动识别 **2 张复发卡片**（INC-005 魔法数字、INC-009 并发争用）

### 本轮新增的 6 条真实教训
| ID | 教训 |
|---|---|
| INC-008 | 纳秒时间戳经 JSON 传输丢精度（2^53 上限） |
| INC-009 | 多 check.sh 并发跑同一 PG 互相污染（**复发 3 次**） |
| INC-010 | 实际读取行数 ≠ 数据集总行数（会造成静默数据伪造） |
| INC-011 | 绕过用例建 Run 导致 Pipeline 版本冲突 |

### 记忆批次 4（2026-11-25 晚间，随本报告同批入库）

- 库存：**22 张卡片**（17 + 批次 3 的 INC-012/013/014 + 本批 2 张），全部 ACTIVE，`check_ref` 全部有效
- 本批新增 2 张：

| ID | 类型 | 教训 | check_ref |
|---|---|---|---|
| INC-015 | INC | 并行 PR 各自 bump 契约版本必然冲突（version 是共享单点）——预分配版本号或 fragments + `$ref` 延迟 bump | `packages/contracts/scripts/check-compatibility.sh + scripts/check_contract_parity.py` |
| SOP-002 | SOP | 并行智能体开发三件套：worktree 隔离 + 迁移号/契约版本/分支名/卡序号预分配 + check.sh 全局串行（引用 MEM-INC-009） | `scripts/check_memory.py + scripts/sync_memory_nebula.py` |

- 本批已运行 `build_memory_index.py` / `check_memory.py`（22 张全过）/ `sync_memory_nebula.py`（星云 8898 新增 2 条 + 幂等更新 20 条）

---

## 八、下一轮待办（2026-11-25 晚间更新：P0/P1 全部完成）

| 优先级 | 事项 | 状态（2026-11-25 晚间） | 闭环 PR |
|---|---|---|---|
| **P0** | 走通 Worker `process_once()` 让 `processing_artifacts` 真正落库 | ✅ **完成**——迁移 0074 补齐四处 CHECK 约束，单次 `process_once()` 跑通 9 个阶段，落库 66 条真实产物 | #126 |
| **P0** | Observation/Action Canonical 字段补齐 | ✅ **完成**——ADR-0049 扩展，`_ADDED_IN_1_1_BY_ENTITY` 按实体裁剪，Schema 重导出 | #129 |
| **P1** | Adapter 六级能力矩阵落地（ADR-0048） | ✅ **完成**——`capability.py` 六级枚举 + 证据存在性校验 + 单测 | #127 |
| **P1** | DATA_CLEANING FLAG 规则（V51-006） | ✅ **完成**——确定性 FLAG 清洗规则 + 版本化报告 | #130 |
| **P1** | 多 Episode 逐帧数据集 + 完整 Pipeline 联动 | ✅ **完成**——5 个真实 demo 280 帧官方 Loader 全集+子集 PASS，经 STREAM_EXTRACTION NDJSON 桥接 | #128 |
| **P2** | 视频链路（需图像样本） | ⬜ 未开始 | 前置：图像样本（仍缺） |
| **P2** | 真实硬件 Pilot | ⬜ 未开始 | 前置：硬件（仍缺） |

## 八·补、V5.2 阶段进展（标注平台蓝图）

> 依据 `docs/roadmap/v5.2/annotation-platform-blueprint-plan.md` §八实施进度与 PR #131。

**阶段 1（已完成）**——judge / 熔断 / PRM（PR #131，`feat(v52-006/007/017)`）：

| 能力 | 内容 | 验证 |
|---|---|---|
| Judge 偏差校正路由 | Swap&Average 位置偏差校正（翻转→TIE 降级）+ 长度惩罚（≥2 倍触发线性扣减）+ 三区路由（≥0.92 自动放行 / 灰区强制人工 / 其余标准流） | 18 单测 |
| 黄金题熔断 | 黄金题滑窗熔断（连续 2 窗 <85% → `PAUSED` + 冻结结算，可手动解除重置） | 9 单测 |
| PRM 步级奖励导出 | `PRM_STEP_REWARD_JSONL` writer：四类标签 reward 映射、未标注步骤不进训练文件、index 连续性强制、roundtrip 校验 | 12 单测 |
| SFT-DPO 库 | `datasets5.2.0` 导出库验证 **PASS**（SFT / 偏好对导出格式落地） | 三方真实验证另存 `docs/testing/evidence/` |

**阶段 2（2026-11-25 收口）**：三线并行（子代理模式，MEM-SOP-003）

| 能力 | 内容 | 验证 |
|---|---|---|
| PRM 步级标注域模型（PR #134，已合并） | 步级标注实体、四类标签与 reward 映射；契约 **1.40.0**（`paths.stage10.yaml`） | 契约 parity + Redocly lint 通过（坑点沉淀 MEM-INC-016/017） |
| Token 成本台账（PR #135，已合并） | judge/PRM Token 计量与成本归集；迁移 **0081**，金额全程 Decimal 量化 4 位小数、对外字符串输出 | `backend/tests/migrations/test_ai_token_usage_migration.py`（坑点沉淀 MEM-INC-019/021） |
| 技能标签路由（PR #136，CI 收口中） | 按技能标签路由标注任务到 Judge/人工队列；迁移 **0080** | SQLite batch 撞跨表触发器已处置（MEM-INC-020，先例 0041/0052） |

- 契约版本唯一 bump 至 **1.40.0**，其余 PR 走 fragments + `$ref` 延迟策略（MEM-INC-015 的处置落地）；
- 迁移链最终态 `0074 → 0078 → 0081 → 0080`（head 唯一；并行期双 head 由后合方重排解决，MEM-INC-018）；
- 本阶段 6 类真实坑点 + 1 张协作 SOP 入库（MEM-INC-016~021、MEM-SOP-003），记忆库 29 张全绿。

**阶段 3（2026-11-25 开工）**：web-ui（标注工作台接入）／badcase-backflow（坏例回流）／honeypot-wiring（黄金题接线）三线并行。

---

## 九、验收判定

**V5.1 核心命题——「让真实机器人数据能进入 DDP 并被正确解析为逐帧数据」——已获实证。**

- 真实样本 → 真实对象存储 → 真实物化 → 真实逐帧 Parquet → **官方 Loader 逐帧读取成功**
- 这是 V4.1 结束时只有 `METADATA_ONLY` 的状态下**不可能达成的**
- ~~但**训练数据生产全闭环尚未完成**：`processing_artifacts` 仍为 0 行、无清洗、无多 Episode、无真实训练消费~~
- **2026-11-25 晚间更新**：上午报告中的未闭环项已由下午 6 个 PR（#126–#131）全部闭环——`processing_artifacts` 落库 66 条真实产物、DATA_CLEANING 落地、多 Episode 280 帧验证、Observation/Action 字段补齐、Adapter 矩阵落地（见 §二·补 与 §8）。§6 诚实边界中的视频帧读取、真实硬件采集、真实训练脚本消费三项**仍未验证**，不得扩大宣称。

**建议**：接受本轮为 **「V5.1 第一阶段验收通过（物化能力打通 + 训练就绪核心验证）」**，
而非「V5.1 全部完成」。后者需要 §8 的 P0/P1 全部闭环。

**2026-11-25 晚间补充判定**：§8 的 P0/P1 已全部闭环（P2 视频链路与硬件 Pilot 前置条件仍缺），
V5.1 主体建设完成度可升级为 **「P0/P1 验收通过」**；「V5.1 全部完成」的判定仍受 §6 保留边界约束。
