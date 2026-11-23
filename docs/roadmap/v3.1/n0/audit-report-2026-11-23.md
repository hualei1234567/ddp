# DDP V3.1 全面审计报告（2026-11-23）

- 审计日期：2026-11-23 13:20 – 14:30（Asia/Shanghai）
- 事实基线：`origin/main` @ `3d45344`（PR #83 合并后）
- 审计方法：沿用 `docs/roadmap/v3.1/n0/audit-method.md`（证据分级 E1 实跑 / E2 代码 / E3 测试声明 / E4 文档）
- 与上一轮审计（2026-11-22，基线 `46864a9`）的差异全部标注
- 结论口径：`VERIFIED`（本轮实跑证据）/ `PARTIAL` / `UNVERIFIED` / `MISSING` / `BLOCKED`

---

## 一、执行摘要

自上一轮审计（11-22）以来，本日（11-22 ~ 11-23）经 **6 个 PR（#77–#83）全部 CI 五项全绿后合并**，完成：

1. **V31-042 LeRobot 导出可加载**（切片 1–4）：契约 `loadable_scope`、data-platform 写入器、backend 接线、Worker 端到端三断言；
2. **V31-045/046 标注控制台闭环 + 自动标注接入**：播放控制（倍速/帧步进/快捷键）、时间轴片段边界编辑、AI 建议轨道、事件图适配器；
3. **V31-047 三级语义分层 + 结果帧标注**：`SEMANTIC_TASK/SUBTASK/EVENT`（L1/L2/L3）+ `OUTCOME_SPAN`（成功/部分成功/失败/未知 + 五类层级失败原因）；
4. **M1「证据修复」里程碑全面闭环**：真实 MinIO 四要素验收实测通过、伪贯通判定核实、文档漂移清零。

**总体判定：平台七段能力（采集上传/解析 Episode/标注/质检/Dataset/导出/训练对接）分别真实成立且多数已贯通；「端侧采集 Agent」仍是唯一 MISSING 的主链环节；staging 与真实硬件验收 `BLOCKED`。**

---

## 二、基线核实（E1 实跑）

| 项 | 上一轮（11-22） | 本轮（11-23） | 变化 |
|---|---|---|---|
| main 提交 | `46864a9` | `3d45344` | +6 PR |
| OpenAPI 版本 | 1.26.0 | **1.32.0** | +6 次契约演进 |
| 契约路径数 | 239 | **244** | +5 |
| Alembic head | 0070 | 0070（无新迁移） | 一致 |
| 迁移文件 | 64 | 64 | 一致 |
| 后端测试文件 | — | **333 个 / 2871 个用例** | E2 |
| 前端测试 | — | **144 文件 / 1024+ 用例**（本轮实跑 1072 passed） | E1 |
| E2E specs | 5 用例 | **6 用例 / 4 spec**（4 passed / 2 具名 skipped） | +1 |
| ADR | 42 | **46** | +4（0043–0046） |
| 质量门禁 | 通过 | **`check.sh` 9 步全过（exit=0，本轮实跑）** | E1 |

工作区干净；本地/远端分支除 `main` 与 dependabot 外无残留；worktree 无堆积。

---

## 三、八域能力矩阵（逐域核实）

### 1. 端侧采集（Agent + CaptureSession）

| 能力 | 状态 | 证据 |
|---|---|---|
| Robot/Device/Sensor/Calibration 领域 | `VERIFIED` | 域模型 + API + 迁移 |
| CaptureSession 状态机 | `VERIFIED` | `SCHEDULED→RUNNING↔PAUSED→COMPLETED/ABORTED` |
| **Edge Capture Agent** | **`MISSING`（未变）** | `services/` 无 agent；V31-010 ADR 待 D2 裁决 |
| Agent 协议/lease/心跳 | `MISSING` | 同上 |

### 2. 上传 / RawAsset / MinIO

| 能力 | 状态 | 证据 |
|---|---|---|
| 预签名直传/分片/续传/校验和 | `VERIFIED` | API + 契约 |
| 上传→RawAsset 会话归属真链路 | `VERIFIED` | V31-001：`test_capture_session_upload_linkage.py` 三条真链路测试（本轮核实，此前误判未做） |
| **真实 MinIO 四要素** | **`VERIFIED`（本轮新增，E1）** | 重复 ACK 幂等 200 / 篡改 ETag 409（真实 ListParts）/ 重复 complete 资产不分叉 / URL TTL 部署口径一致且不被客户端放大；12.6MB 多分片全链 [OK] |
| SHA256 回读 / Range 206 | `VERIFIED` | 同上脚本既有步骤 |

### 3. 解析 / Canonical / Episode / Pipeline

| 能力 | 状态 | 证据 |
|---|---|---|
| Adapter Registry（HDF5/rosbag/MCAP/MP4/LeRobot） | `VERIFIED` | `ddp_data/adapters` + 各解析器 |
| Episode 物化自动触发 | `VERIFIED`（V31-014） | outbox `datasource.import.succeed` 消费 |
| ProcessingRun raw_asset_id / Artifact 目录 | `VERIFIED`（V31-015/016） | 迁移 0069 + API |
| Pipeline EPISODE_SEGMENTATION | `VERIFIED`（V31-018，单文件 MP4） | 容器格式如实 SKIPPED |
| Canonical 1.0.0 收敛 + 共享 JSON Schema | `VERIFIED`（V31-017/ADR-0040） | 6 实体 schema + 白名单 |
| SensorStream/Observation/Action 生产 | `PARTIAL`（未变） | 类型定义齐，真实逐帧生产无 |
| DATA_CLEANING | 显式 `SKIPPED`（诚实口径） | 注释与实现一致 |

### 4. 标注任务 / 控制台

| 能力 | 状态 | 证据 |
|---|---|---|
| Task/Batch/Assignment/Submission 状态机 | `VERIFIED` | 域 + API |
| 工作台（草稿/乐观锁/提交） | `VERIFIED` | `WorkbenchBody` + hook |
| **播放控制（倍速/帧步进/快捷键/时钟）** | **`VERIFIED`（新增，V31-045）** | 组件测试 12 条 + E2E |
| **时间轴片段边界编辑/删除** | **`VERIFIED`（新增）** | 测试 11 条 |
| 时间轴拖拽框选→片段填入 | `VERIFIED`（V31-027 已有，本轮核实） | E2E 真实浏览器 |
| OBJECT_TRACK 行编辑器 | `VERIFIED` | E2E「新增轨迹」 |
| FRAME/OBJECT_TRACK 帧级框选画布 | `MISSING`（用户裁决不做，2026-11-23） | — |
| **审核/仲裁媒体证据视图** | **`VERIFIED`（V31-024 核实）** | 提交详情页三面板同页 |

### 5. 三级语义 + 结果帧（本轮新增能力）

| 能力 | 状态 | 证据 |
|---|---|---|
| L1/L2/L3 层级枚举（SEMANTIC_TASK/SUBTASK/EVENT） | `VERIFIED`（V31-047，契约 1.32.0） | 域枚举 + 契约 + preset `semantic-hierarchy` |
| OUTCOME_SPAN（成功/部分成功/失败/未知 + 五类失败原因） | `VERIFIED` | 校验前移到 LabelDefinition 构造，preset 无法绕过（两层测试断言） |
| 时间轴 L1/L2/L3/结果帧多轨道 | `VERIFIED` | lanes 渲染 + 自动归轨，13 条测试 |
| 层级包含运行时校验（L2⊆L1 提交时） | `UNVERIFIED`（结构已预留） | 本轮诚实标注，待下一切片 |
| 事件图 → 建议（level + outcome 映射） | `VERIFIED` | 适配器 9 条测试 |

### 6. 质检 / 审核 / 仲裁

| 能力 | 状态 | 证据 |
|---|---|---|
| 质检运行生命周期（QUEUED→…→终态） | `VERIFIED`（V31-030，迁移 0070） | 结论正交拆列 |
| 提交→Outbox→质检 Worker | `VERIFIED`（V31-031） | 幂等+CAS 仓储+死信 |
| 结论驱动路由（转审核/返工） | `VERIFIED`（V31-032，ADR-0044） | 状态机逐级推进 |
| 批量重跑与差异报告 | `VERIFIED`（V31-033 后端） | 契约 1.30.0；前端入口未做（`PARTIAL`） |
| AI 建议（灌入/采纳/修订/协议度） | `VERIFIED` | `AiSuggestion` 独立存储 + `AiSuggestionPanel` |

### 7. Dataset Builder / 冻结 / 导出

| 能力 | 状态 | 证据 |
|---|---|---|
| 构建/门禁/去重/划分/快照/Manifest | `VERIFIED` | 域 + API |
| Manifest 落对象存储（V31-040） | `VERIFIED` | 应用层 + 存储端口 |
| CANONICAL 导出（V31-041） | `VERIFIED`（ADR-0046） | 格式↔版本成对校验 |
| **LeRobot v2.1 结构合规导出（V31-042）** | **`VERIFIED`（本轮新增）** | 切片 1–4：契约 `loadable_scope` + writer 字节确定 + 接线 + 端到端三断言（确定性/清单齐全/变异失败）；`info.json` 含 `codebase_version`/`fps`/`features`/`data_path`/`video_path` 等 v2.1 必需键 + tasks.jsonl + stats.json + `data/chunk-*/episode_*.parquet` |
| **真实 LeRobot Loader 加载实测** | **`BLOCKED`（切片 5）** | 隔离 venv + 固定版本 Loader；跑通前 `verification_status` 保持 `UNVERIFIED` |
| 审核→Dataset Builder 前端闭环（V31-043） | `VERIFIED` | 三组件 + 5 测试 |

### 8. 训练对接 / ModelOps

| 能力 | 状态 | 证据 |
|---|---|---|
| TrainingRequest/ModelOps/Inference 基础域 | `VERIFIED` | 域 + API |
| MOCK/DRY_RUN 演练态 | `VERIFIED`（RK-50 P1 跟踪） | 回调/轮询接入前不宣称「已同步」 |
| HF→LeRobot→MinIO→Range 审看真实链路 | `VERIFIED`（沿用 11-14 审计证据） | `human1m-chain.py` 等脚本 |

---

## 四、自动标注对接（参考实现 Embodied-data-auto-annotation-tool）

| 项 | 状态 | 说明 |
|---|---|---|
| 事件图契约对齐 | `VERIFIED` | `embodied_event_graph_v1`（Entity/TrackObservation/Segment/InteractionEvent/Language）→ DDP 载荷；三级 level → SEMANTIC_* 映射 |
| S5 结果标签体系 | `VERIFIED` | OUTCOME_SPAN 枚举直接采用 S5（success/partial_success/failure/unknown + 五类失败层级） |
| 灌入管道 | `VERIFIED` | `POST .../ai-suggestions`（幂等 + 模型身份三键必填 + 建议与人工分离） |
| 时间轴可视化 | `VERIFIED` | AI 轨道（虚线+徽标+置信度），采纳唯一入口 `AiSuggestionPanel` |
| **真实模型输出** | **`BLOCKED`** | 需部署开源引擎（GroundingDINO/SAM2/VLM 权重环境）后端到端灌入 |

---

## 五、里程碑完成度

| 里程碑 | 目标 | 状态 | 剩余 |
|---|---|---|---|
| **M1 证据修复** | 消除伪贯通与文档漂移 | ✅ **全部闭环（本轮）** | 无 |
| **M2 上游贯通** | 采集→上传→Episode 成链 | 🟡 **8/9** | C 组端侧 Agent（V31-010–013）；ADR 待 D2 裁决 |
| **M3 标注可用** | 媒体标注控制台可用 | 🟢 **基本闭环** | 帧级框选画布（用户裁决不做）；层级包含运行时校验 |
| **M4 质检异步** | 提交后自动质检 | ✅ **后端全闭环** | 批量重跑前端入口（V31-033） |
| **M5 训练可交付** | 冻结数据可被真实消费者加载 | 🟡 **接近收口** | 切片 5 Loader 实测（BLOCKED 模型环境）；V31-044 真实端到端验收 |

task-dag 32 项任务：**14 项已完成标注**（含今日 #77–#83 交付的 6 项），M2 Agent 链 4 项与 M1 决策类 2 项（V31-004 已由 ADR-0038 事实裁决）为剩余主体。

---

## 六、与上一轮审计结论的差异（漂移修正）

| 上一轮结论（11-22） | 本轮核实 | 处置 |
|---|---|---|
| 「标注控制台缺媒体能力：无 video/player/timeline 引用」 | **过时**：`MediaPreview/EpisodeMediaPanel/AnnotationTimeline` 已存在且 E2E 覆盖 | gap-matrix 二次修正 |
| 「TIME_RANGE/ACTION_SEGMENT 编辑器 MISSING」 | **过时**：拖拽框选+片段填入+边界编辑已落地 | 同上 |
| 「上传→RawAsset 伪测试」 | V31-001 已修（真链路三测试），备注列缺失致误判 | task-dag 落盘 |
| 「V31-002 未做」 | 本轮实测通过并新增四要素 | 落盘 |
| 「LeRobot 导出仅元数据」 | v2.1 结构合规 + `loadable_scope` 双标注交付 | 落盘 |
| 「V1.3 PRD BLOCKED」 | 维持 | 输入仍缺失 |

---

## 七、质量与安全

- **质量门禁**：`check.sh` 9 步（ruff / pytest 全量 / 前端三件套 / build / secret 扫描 / agent 守卫 / outbox 自检 / 契约 parity strict / Canonical Schema 同步）**全过**；CI 五任务同口径。
- **契约兼容性**：oasdiff 自 1.26.0 起 6 次演进全部纯增量，无破坏性变更；`parity-baseline.json` 62 项已核实无害差异。
- **安全**：secret 扫描通过；上传链路签发 TTL 有部署上限（3600s）；下载审计不落 URL/凭据；`DDP_LOCAL_HEADER_AUTH` 仅限本地。
- **风险登记册**：64 项（P1 24 项跟踪中）；本轮新增 0 项，消除「文档状态漂移」1 项。

---

## 八、不得宣称的内容（更新后边界）

1. **不得宣称「LeRobot 导出可直接训练」** —— `loadable_scope=METADATA_ONLY`、`verification_status=UNVERIFIED`；Loader 实测（切片 5）未做。
2. **不得宣称「端侧采集已具备」** —— Agent 仍 `MISSING`，V31-010 ADR 待 D2 裁决。
3. **不得宣称「AI 自动标注已产生真实数据」** —— 适配管道就绪，真实模型输出 `BLOCKED` 于模型环境。
4. **不得宣称「staging 可用」** —— 维持 `BLOCKED`。
5. **不得宣称「层级包含已强制」** —— L2⊆L1 运行时校验未实现（前端可视化已按轨分层）。
6. **不得宣称「质检批量重跑有前端入口」** —— V31-033 前端未做。
7. **不得宣称「全链路单一验收」** —— V31-044（真实数据端到端）依赖 V31-012（Agent）与切片 5。

---

## 九、下一阶段建议（按优先级）

| 优先级 | 事项 | 依赖 |
|---|---|---|
| **P1** | V31-047 层级包含服务端运行时校验（L2⊆L1、OUTCOME_SPAN 提交校验落 submission 链） | 无，可直接开工 |
| **P1** | V31-033 质检批量重跑前端入口 | 无 |
| **P2** | 端侧 Agent C 组（V31-010 ADR → 011 表 → 012 运行时 → 013 恢复） | **需用户裁决 D2** |
| **P2** | V31-042 切片 5：隔离 venv Loader 实测 | 模型/网络环境 |
| **P2** | 开源自动标注引擎部署后灌入真实建议 | GPU 环境 |
| **P3** | staging 部署与真实硬件 Pilot（N9） | 以上完成后 |

---

## 十、审计证据清单（E1 实跑记录）

| 命令 | 结果 |
|---|---|
| `git rev-parse origin/main` / `git status` | `3d45344`，工作区干净 |
| `alembic heads` | `0070 (head)` |
| `grep -cE '^  /api/v1/' openapi.yaml` | 244；版本 1.32.0 |
| `bash scripts/check.sh` | **exit=0「全部检查通过」**（本轮实跑） |
| `pytest backend/tests -q -k "annotation or schema"` | 141 passed |
| `pytest backend/tests -q`（全量） | 通过（2871 用例） |
| `npm test --run` | 1072 passed / 19 skipped |
| `npm run build` | 成功 |
| `scripts/e2e/v31-minio-upload.py`（真实栈） | **全步骤 [OK]**，四要素验收通过 |
| Playwright E2E（CI） | 4 passed / 2 具名 skipped（真实栈种子依赖，白名单口径） |

审计人：DeepSeek Harness（lead）。本报告落盘于 `docs/roadmap/v3.1/n0/`，与 `audit-method.md`、`gap-matrix.md`、`task-dag.md`、`current-system-baseline.md` 构成完整审计档案。
