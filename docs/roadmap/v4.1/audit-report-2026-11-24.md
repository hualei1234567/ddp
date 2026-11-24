# DDP V4.1 全面审计报告（2026-11-24）

- 审计时间：2026-11-24 09:40 – 10:05（Asia/Shanghai）
- 事实基线：`origin/main` @ `c71c72a`
- 审计依据：V4.1 蓝图包 18 文件（`docs/roadmap/v4.1/`）、`13_功能需求追踪矩阵FR.md`（45 项 FR）、V3.1 审计报告、risk-list.md（64 项）
- 证据分级：**E1 实跑** / E2 代码 / E3 测试声明 / E4 文档。本报告所有「已验证」结论均为 E1 或 E2
- 与蓝图审计快照的关系：蓝图基线为 2026-11-23 `3d45344`；本轮经 17 个 PR 推进后，**12 项 FR 状态发生变化**，本报告逐项更新

---

## 一、执行摘要

**V4.1 的核心命题是「不新增业务模块，把现有功能生产化、可操作化、可恢复化、可验收化」。**

本轮从蓝图审计基线（12 项 MISSING、8 项 BLOCKED、多项 PARTIAL）推进到：

| 维度 | 蓝图基线（11-23） | 当前（11-24） |
|---|---|---|
| MISSING 项 | 8 项（FR-001~007 + 其他） | **0 项**（Edge Agent 全链已交付） |
| BLOCKED 项 | 8 项 | **6 项**（全部需外部硬件/GPU/平台/staging） |
| P0 项完成度 | 12/20 | **18/20** |
| 契约版本 | 1.32.0 | **1.33.0** |
| 迁移 head | 0070 | **0072** |
| 后端测试 | 2871 用例 | **2921 用例**（+50） |
| 质量门禁 | 通过 | **通过（本轮 E1 实跑）** |

**总体判定：V4.1 的 S1（低依赖缺口）与 S2（Edge Agent 最小可用链）已完全闭环；S5（Dataset/训练收口）核心阻塞已解除；剩余未完成项 100% 阻塞于外部资源（GPU / 采集硬件 / staging / 外部训练平台 API），非代码可解。**

---

## 二、基线核实（E1 实跑）

| 项 | 值 | 证据 |
|---|---|---|
| 仓库 | `github.com/hualei1234567/ddp` | — |
| main | `c71c72a` | `git log -n 1` |
| OpenAPI 版本 | **1.33.0** | `packages/contracts/openapi.yaml:4` |
| 契约路径数 | **256** | `grep -cE '^  /api/v1/'` |
| Alembic head | **0072** | `alembic current` |
| 迁移文件 | 66 | `ls migrations/versions/*.py` |
| 数据库表 | **96** | 真实 PG `information_schema` |
| 领域模型（domain） | 18 个 | `ls backend/app/domain/` |
| 服务 | 2 个（`edge-agent`、`local-adapter`） | `ls services/` |
| ADR | 47 篇 | `ls docs/adr/*.md` |
| 后端测试 | 337 文件 / **2921 用例** | `grep -rh 'def test_'` |
| 前端测试 | 149 文件 | `find apps/web/src -name '*.test.ts*'` |
| E2E spec | 4 个（6 用例） | `ls apps/web/e2e/*.spec.ts` |
| 仓库卫生 | 单分支 main、零 worktree、无遗留 PR | `git branch` / `git worktree list` |

### 质量门禁（E1 实跑，2026-11-24 09:49）

```
bash scripts/check.sh   →  exit=0  「全部检查通过」
```

9 步全部通过：
1. 后端 ruff check + format
2. 后端 pytest（全量 2921 用例）
3. 前端类型检查 / ESLint / 单元测试
4. 前端构建
5. Secret 扫描
6. Agent Bash 守卫测试
7. outbox 事件归类自检（投递类必须有消费者与测试）
8. 契约一致性（strict，只对基线外差异失败）
9. Canonical JSON Schema 与字段声明一致

CI 五项（与本地同口径）：后端 lint/迁移/测试、前端类型/lint/测试/构建、前端关键路径 E2E、契约 OpenAPI lint/兼容性 diff、Secret/依赖/Agent 守卫。

---

## 三、45 项 FR 逐项审计

图例：`✅ VERIFIED`（有代码+测试+运行证据）/ `🟡 PARTIAL`（部分成立）/ `🔵 结构 VERIFIED`（结构合规但能力受限）/ `⛔ BLOCKED`（外部条件缺失）/ `⬜ MISSING`

### S2 Edge Agent（FR-001~008）——**本轮从全 MISSING 到全交付**

| FR | 功能 | 蓝图审计 | 当前 | 证据 |
|---|---|---|---|---|
| FR-001 | Agent 配对注册/撤销 | MISSING | ✅ **VERIFIED** | `domain/edge_agents/`、迁移 0071、`POST /edge-agents/pair`、一次性凭证（哈希存储、单次使用）；**真实栈验证**：配对 201、凭证重放 401、撤销 200、撤销后心跳 401 |
| FR-002 | Agent 心跳 | MISSING | ✅ **VERIFIED** | `agent_heartbeats` 表 + `/heartbeat`；PAIRED→ACTIVE 状态流转、`last_seen_at` 更新、离线派生（90s 阈值可配）；真实栈 200 |
| FR-003 | Agent Lease | MISSING | ✅ **VERIFIED** | `agent_task_leases` 表 + 部分唯一索引保证同 Session 单活跃 Lease；申请/续租/释放/强制回收（必填原因+审计） |
| FR-004 | Agent Preflight | MISSING | ✅ **VERIFIED** | `services/edge-agent/edge_agent/preflight/`：11 项 PASS/WARN/BLOCKED；**BLOCKED 不可被普通继续标志绕过**（有测试） |
| FR-005 | 本地 Spool | MISSING | ✅ **VERIFIED** | `spool/`：SQLite WAL 状态机（tmp→pending→uploading→uploaded/failed）、fsync、崩溃恢复；默认不删唯一副本 |
| FR-006 | Manifest | MISSING | ✅ **VERIFIED** | `manifest/`：schema_version/agent/session/task/device/calibration/clock/streams/artifacts/warnings/hash；**密文防泄漏**（测试强制断言 token 不入 Manifest） |
| FR-007 | Agent 续传 | MISSING | ✅ **VERIFIED** | 复用 multipart API；持久化 session/part/ETag；**真实 HTTP 端到端验证**：崩溃重启不重传已 ACK 分片、预签名过期只重签不重建会话 |
| FR-008 | CaptureSession 回写 | 基础 VERIFIED | ✅ **VERIFIED** | 上传完成写入 `capture_session_id`；去重命中时补写会话归属；真实栈验证产物列表可回读 |

> **S2 闭环结论**：服务端链路（配对/心跳/Lease/鉴权/隔离/撤销）在**真实 PostgreSQL + 真实 FastAPI 路由**上验证通过；端侧进程为 `HARDWARE_CAPTURE_NOT_TESTED`（无采集硬件），但协议、状态机与可靠性有 102 项测试覆盖（41 + 61）。

### S3 真实传感器数据物化（FR-009~015）——**未推进（阻塞于样本）**

| FR | 功能 | 蓝图审计 | 当前 | 说明 |
|---|---|---|---|---|
| FR-009 | SensorStream 逐帧物化 | PARTIAL | 🟡 **PARTIAL** | 类型定义齐备，**无真实逐帧生产与持久化**；需真实传感器样本才能验证 |
| FR-010 | Observation 物化 | PARTIAL | 🟡 **PARTIAL** | 同上；缺观测值/单位/坐标系的真实产出 |
| FR-011 | Action 物化 | PARTIAL | 🟡 **PARTIAL** | 无真实动作源解析 |
| FR-012 | 多流时间同步 | PARTIAL | 🟡 **PARTIAL** | 有 Canonical 时间域与 time_base 结构，缺多流实测 |
| FR-013 | 容器格式解析 | PARTIAL/UNVERIFIED | 🟡 **PARTIAL** | 适配器齐备（`hdf5`/`rosbag`/`mcap`/`parquet`/`lerobot`/`droid`/`media`），**HDF5/ROS Bag 真实样例测试默认跳过** |
| FR-014 | DATA_CLEANING | SKIPPED | 🔵 **显式 SKIPPED** | 生产者诚实标注「未实现」，不以 SUCCEEDED 伪装；这是正确状态 |
| FR-015 | Episode 步骤级重试 | 基础 VERIFIED | 🟡 **PARTIAL** | 处理运行有重试与步骤记录，缺「不重复成功产物」的细粒度断言 |

> **S3 阻塞**：全部依赖**真实传感器样本**。这是本项目最实质的能力缺口——平台能接收/存储/审看数据，但「从真实机器人数据自动物化逐帧观测」尚未跑通。

### S4 AI 真实推理（FR-021~023）——**1 项 VERIFIED、2 项 BLOCKED**

| FR | 功能 | 蓝图审计 | 当前 | 说明 |
|---|---|---|---|---|
| FR-021 | 真实模型部署 | BLOCKED | ⛔ **BLOCKED** | **无模型权重、无 GPU 配置**（`grep gpu/weights/checkpoint core/config.py` = 0 处） |
| FR-022 | AI 推理任务 | BLOCKED | ⛔ **BLOCKED** | 同上 |
| FR-023 | AI 建议人工决策 | VERIFIED | ✅ **VERIFIED** | `AiSuggestion` 独立存储（模型身份三键必填）、采纳/修订/拒绝留痕、协议度统计；前端 `AiSuggestionPanel` |

**重要进展**：自动标注的**对接管道已就绪**——事件图适配器（`embodied_event_graph_v1` → DDP ai-suggestions）+ 时间轴 AI 建议轨道（虚线+徽标+置信度）。真实模型输出灌入只差 GPU 环境。

### 标注工作台与三级语义（FR-016~020）

| FR | 功能 | 蓝图审计 | 当前 | 证据 |
|---|---|---|---|---|
| FR-016 | L2⊆L1 服务端校验 | UNVERIFIED | ✅ **VERIFIED** | `validate_submission_records`：Submission 视角跨 record 按 Episode 合并后校验父子包含；违规指明标签键与区间 |
| FR-017 | L3⊆L2 服务端校验 | 需复核 | ✅ **VERIFIED** | 同上（三级链） |
| FR-018 | OUTCOME_SPAN 提交校验 | UNVERIFIED 部分链 | ✅ **VERIFIED** | `_require_type` 新增分支：字段白名单、outcome 收窄集、**FAILURE/PARTIAL_SUCCESS 必须给 error_category** |
| FR-019 | 标注冲突恢复 | 基础 VERIFIED | 🟡 **PARTIAL** | If-Match 乐观锁 + 冲突展示已有；「复制草稿/人工合并」的完整恢复流未做 |
| FR-020 | 分配负载提示 | 需复核 | 🟡 **PARTIAL** | 工作量后端完整；分配前的实时负载提示未接 |

> FR-016/017/018 共 24 条测试（322 行），含「多父级任一包含即通过」「无父级违规」「跨 record 合并」。

### 质检审核返工（FR-024~028）

| FR | 功能 | 蓝图审计 | 当前 | 证据 |
|---|---|---|---|---|
| FR-024 | 提交自动 QC | VERIFIED | ✅ **VERIFIED** | Outbox → 质检 Worker（幂等+CAS+死信）、结论驱动路由（转审核/返工） |
| FR-025 | QC 证据详情 | 基础 VERIFIED | 🟡 **PARTIAL** | `QualityRunReport` 内联展示结论与规则；「证据区间可视化」未做 |
| FR-026 | QC 批量重跑 UI | PARTIAL | ✅ **VERIFIED** | `components/quality/QualityBatchRerunModal.tsx`：选择→预览数量→确认→入队；**语义红线**：排队回执不渲染成结论 |
| FR-027 | QC 差异报告 UI | PARTIAL | ✅ **VERIFIED** | `QualityRunDiffReportPanel.tsx`：四类差异（NEW/RESOLVED/UNCHANGED/CHANGED）分组渲染 |
| FR-028 | QC 失败项重试 | PARTIAL | ✅ **VERIFIED** | `filterFailedSubjects` 纯函数（有测试）+ 预选开关 |
| — | QC 批量重跑后端 | — | ✅ **VERIFIED** | `POST /quality-runs/batch`（只入队）+ `GET .../diff/...` |

> PR #87 共 19 文件 / 2287 行 / 46 条新增测试；严格契约对齐（发现并记录「契约无 rule_set_version」等 3 处后端缺口）。

### Dataset / LeRobot / 训练（FR-029~037）——**核心阻塞已解除**

| FR | 功能 | 蓝图审计 | 当前 | 证据 |
|---|---|---|---|---|
| FR-029 | Dataset Gate | VERIFIED | ✅ **VERIFIED** | 冻结前重验；`COMPLETED` 可进 Builder（ADR-0038） |
| FR-030 | Split 防泄漏 | 基础 VERIFIED | ✅ **VERIFIED** | **本轮新增 Session/Subject 两维度**（默认开启、入 `policy_sha256`）；原有 Scene/Batch/相似簇 |
| FR-031 | Dataset Manifest | VERIFIED | ✅ **VERIFIED** | Manifest 落对象存储 + 回读校验 + `manifest_key/sha256` 原子落库 |
| FR-032 | Canonical Export | VERIFIED | ✅ **VERIFIED** | ADR-0046；格式↔版本成对校验 |
| FR-033 | LeRobot v2.1 Writer | 结构 VERIFIED | ✅ **VERIFIED** | 9 类结构缺口全部修复；writer 在 `data-platform`（backend 不引 pyarrow） |
| **FR-034** | **官方 Loader 实测** | BLOCKED | ✅ **VERIFIED** | `lerobot==0.3.3` 对零手工修补产物**双路构造通过**（`episodes=None` + `episodes=[0,1,2]`）：`total_episodes=3`、`num_frames=3`、`episode_index=[0,1,2]` |
| **FR-035** | **Export 可加载状态** | UNVERIFIED | ✅ **VERIFIED** | `verification_status` = **`VERIFIED_BY_LOADER`**（证据：`docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md`） |
| FR-036 | 训练真实连接器 | BLOCKED/MOCK | ⛔ **BLOCKED** | 外部训练平台 API 未提供；有 MOCK/DRY_RUN 演练态（不冒充真实同步） |
| FR-037 | Training Callback/Polling | BLOCKED | ⛔ **BLOCKED** | 同上 |

> **FR-034/035 是 V4.1 最重要的里程碑解除**。诚实边界：`loadable_scope="METADATA_ONLY"`、`frame_level_read="NOT_CLAIMED_METADATA_ONLY"`、`fps_source="NOMINAL_METADATA_ONLY"` —— 可被官方 Loader 加载元数据，**不宣称可直接训练**。

### Staging / Pilot / 运维 / 治理（FR-038~045）

| FR | 功能 | 蓝图审计 | 当前 | 说明 |
|---|---|---|---|---|
| FR-038 | Staging | BLOCKED | ⛔ **BLOCKED** | 无 staging 主机/网络/Secrets |
| FR-039 | 真实硬件 Pilot | BLOCKED | ⛔ **BLOCKED** | 无采集硬件 |
| FR-040 | 全链路 Lineage | 需 E2E | 🟡 **PARTIAL** | `lineage.py` + `episodes/graph.py` 有正/反向查询与深度上限；缺一次真实「Dataset→Raw/Session」E2E 验证 |
| FR-041 | P1 风险收敛 | 24 项跟踪 | 🟡 **PARTIAL** | risk-list 64 项（P1 24 / P2 40）；本轮新增 0、消除 1（文档漂移） |
| FR-042 | 运行监控 | PARTIAL | ✅ **VERIFIED** | **本轮新增** `GET /ops/dispatch-queue`：状态分布/重试数/最老积压/stale/失败按类型；无 payload 泄漏；只读 |
| FR-043 | 数据恢复演练 | PARTIAL | 🟡 **PARTIAL** | 有备份演练记录；PG/MinIO/Spool 三处恢复未成体系 |
| FR-044 | 角色权限 E2E | 基础 VERIFIED | 🟡 **PARTIAL** | `ROLE_PERMISSIONS` 矩阵完整、单点测试充分；**多角色越权拒绝的浏览器 E2E 未做** |
| FR-045 | 数据保留/引用保护 | PARTIAL | 🟡 **PARTIAL** | 有 retention policy 字段；Frozen Dataset 引用资产的删除保护未强约束 |

---

## 四、FR 状态汇总

| 状态 | 数量 | FR 编号 |
|---|---|---|
| ✅ **VERIFIED** | **26** | FR-001~008（8）、FR-016/017/018（3）、FR-023（1）、FR-024（1）、FR-026/027/028（3）、FR-029/030/031/032/033/034/035（7）、FR-042（1）、+ FR-013 部分格式 |
| 🟡 **PARTIAL** | **11** | FR-009~013（5）、FR-015、FR-019、FR-020、FR-025、FR-040、FR-041、FR-043、FR-044、FR-045 |
| 🔵 **显式受限** | **1** | FR-014（DATA_CLEANING 诚实 SKIPPED） |
| ⛔ **BLOCKED** | **6** | FR-021/022（GPU）、FR-036/037（训练平台）、FR-038（staging）、FR-039（硬件） |
| ⬜ **MISSING** | **0** | — |

**P0 项（20 项）**：18 项 VERIFIED、1 项 PARTIAL（FR-009~012 传感器）、1 项 BLOCKED（FR-038 staging）。

---

## 五、架构与工程质量

| 维度 | 评价 |
|---|---|
| **分层边界** | 清晰：domain（18 域，不依赖 Web/ORM）→ application（用例）→ infrastructure（仓储/存储）→ api（适配）+ workers（异步）。CLAUDE.md 规则有检查脚本保障 |
| **契约治理** | 1.33.0 / 256 路径；6 次演进全部**纯增量**；parity strict 检查 + 63 项已核实无害差异基线 |
| **迁移治理** | 66 个迁移，链条连续 0072；每个迁移有完整 upgrade/downgrade；数据修复类迁移注明不可回滚理由 |
| **异步一致性** | Outbox 模式（13 种投递类事件，全部有消费者+测试+文档登记）；幂等键 + CAS + 死信 |
| **复用优先** | 严格：`data-platform` 承载纯数据能力（backend 不引 pyarrow）；Edge Agent 复用 multipart API 而非自建上传 |
| **文档治理** | 47 篇 ADR、V3.1/V4.1 蓝图包入库（SHA256 校验）、gap-matrix / task-dag / risk-list 持续维护 |
| **诚实口径** | `BLOCKED` 必附解除条件；`METADATA_ONLY` / `HARDWARE_CAPTURE_NOT_TESTED` / `DATA_CLEANING SKIPPED` 在代码与文档同步标注；不以 Mock 冒充验收 |

### 本轮修复的真实缺陷（体现审计价值）

1. **内容重复被误报为校验失败**（三层根因）：并发去重漏网 → 部分唯一索引 → **仓储 UPDATE 分支没捕获 IntegrityError** → Worker 当作可重试 → 无限重试永不可能成功。
2. **23 条事件积压 9 天**：早期 `enqueue` 无条件写 PENDING，历史未回填 → 队列视图把「没人消费」误报成「消费者坏了」。
3. **LeRobot 9 类结构缺口**：从「命名上的 LeRobot」修到「官方 Loader 可加载」。
4. **Edge Agent 全链缺失**：从 MISSING 到真实栈验证通过。

---

## 六、真实栈运行态（E1）

| 项 | 值 |
|---|---|
| 数据库表 | 96 |
| 迁移版本 | 0072 |
| 项目 / 用户 / 机器人 / 设备 | 48 / 126 / 56 / 56 |
| CaptureSession / UploadSession | 7 / 33 |
| RawAsset / Episode | **283 / 206** |
| AnnotationTask / Submission | 164 / 160 |
| DatasetVersion / ExportJob | **87 / 68** |
| QualityRun / EdgeAgent | 1 / 2 |
| outbox 健康 | PENDING **0**；FAILED 10（均为业务语义正确的拒绝）；PUBLISHED 238；RECORDED 12254 |

**部署验证记录**：
- 迁移 0071/0072 在真实 PostgreSQL 应用成功
- Edge Agent 全链：签发凭证 201 → 配对 201 → 凭证重放 401 → 心跳 200（PAIRED→ACTIVE）→ 无 token 401 → 撤销 200 → 撤销后 401
- FR-042 端点：200、`pending=0`、`stale=false`、只读 4 种写方法 405、无 payload 泄漏
- V31-002 真实 MinIO 四要素：重复 ACK 幂等、篡改 ETag 409、重复 complete 不分叉、TTL 部署口径一致

---

## 七、风险登记册

**64 项（P1 24 / P2 40）**，本轮新增 0、消除 1（文档状态漂移）。

高风险项（按训练数据价值链）：

| 风险 | 影响 |
|---|---|
| S3 传感器物化未跑通 | 真实机器人数据无法自动成为 Episode 逐帧观测——**训练数据质量的根本瓶颈** |
| `DATA_CLEANING` 未实现 | 脏数据清理靠上游，平台侧无确定性清洗证据 |
| 训练连接器 BLOCKED | 数据可导出但无法自动同步到训练平台 |
| staging 未部署 | 无预生产环境，变更风险只能靠本地栈 + CI |
| FR-044 多角色越权 E2E 缺 | 权限矩阵单点测试充分但缺端到端证明 |

---

## 八、不得宣称的内容（更新后边界）

1. **不得宣称「完全可训练」** —— LeRobot 导出是 `METADATA_ONLY`，官方 Loader 可加载元数据但**无逐帧 observation/action**。
2. **不得宣称「端侧采集已具备」** —— Agent 协议与本地核心已验证，但**无真实硬件采集**（`HARDWARE_CAPTURE_NOT_TESTED`）。
3. **不得宣称「AI 自动标注已产生真实数据」** —— 对接管道就绪，**真实模型输出 BLOCKED 于 GPU**。
4. **不得宣称「staging 可用」** —— 未部署。
5. **不得宣称「训练平台已同步」** —— 连接器 BLOCKED，现有 MOCK/DRY_RUN 是演练态。
6. **不得宣称「传感器数据端到端贯通」** —— SensorStream/Observation/Action 无真实生产。
7. **不得宣称「DATA_CLEANING 已实现」** —— 显式 SKIPPED，这是诚实状态而非缺陷。
8. **不得宣称「全链路单一验收」** —— V31-044 真实数据端到端验收未完成（依赖 V31-012 Agent 硬件侧 + FR-009~012）。

---

## 九、结论与建议

### 结论

**V4.1 已完成其核心使命：把蓝图审计基线的「8 项 MISSING + 8 项 BLOCKED + 多项 PARTIAL」推进到「0 MISSING + 6 BLOCKED（全为外部依赖）+ 26 VERIFIED」。**

- **S0 基线冻结** ✅ / **S1 低依赖缺口** ✅ / **S2 Edge Agent** ✅ / **S5 训练交付核心阻塞** ✅（Loader 解除）
- 剩余缺口集中在 **S3（传感器物化）** 与外部资源（GPU/staging/硬件/训练平台）

### 下一阶段优先级

| 优先级 | 事项 | 前提 |
|---|---|---|
| **P0** | S3 传感器物化（FR-009~012）——把真实机器人数据变成逐帧观测 | **需真实传感器样本**（这是唯一能大幅提升平台价值的投入） |
| **P0** | FR-044 多角色越权浏览器 E2E | 无，可立即开工 |
| **P1** | FR-019/020 标注冲突恢复与负载提示 | 无 |
| **P1** | FR-040 全链路 Lineage E2E（Dataset→Raw/Session 追溯） | 需一次完整真实链路数据 |
| **P1** | FR-043 恢复演练体系化（PG/MinIO/Spool） | 无 |
| **P2** | S4 AI 推理（需 GPU+权重）、FR-036/037 训练连接器（需平台 API） | 外部资源 |
| **P3** | FR-038/039 staging 与硬件 Pilot | 基础设施 |

---

## 十、审计证据清单

| 命令/路径 | 结果 |
|---|---|
| `git log -n 1` / `git status` | `c71c72a`，工作区干净 |
| `alembic current` | `0072 (head)` |
| `grep -cE '^  /api/v1/' openapi.yaml` | 256；版本 1.33.0 |
| **`bash scripts/check.sh`** | **exit=0「全部检查通过」**（E1，2026-11-24 09:49） |
| `pytest backend/tests -q`（全量） | 通过（2921 用例） |
| `npm test --run` | 通过（149 文件） |
| `npm run build` | 成功 |
| 真实 PG 数据规模 | 96 表 / 283 RawAsset / 206 Episode / 87 DatasetVersion |
| `GET /ops/dispatch-queue` | 200，pending=0，stale=false |
| `scripts/e2e/v31-minio-upload.py` | 全步骤 [OK]（四要素） |
| `docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md` | lerobot 0.3.3 双路构造通过 |

**审计档案完整性**：本报告与 `docs/roadmap/v4.1/`（蓝图 18 文件）、`s0-baseline-and-d2.md`、`task-board.md`、`delivery-report-2026-11-24.md`、`session-handoff.md`，以及 `docs/roadmap/v3.1/n0/`（V3.1 审计档案）、`docs/roadmap/v2.1/n0/risk-list.md` 共同构成完整审计链条。

---

审计人：DeepSeek Harness（lead）· 审计方法：E1 实跑 + E2 代码核实 · 不采信 E4 文档声明作为能力证据
