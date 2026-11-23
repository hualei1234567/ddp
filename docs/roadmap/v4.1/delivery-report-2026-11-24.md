# DDP V4.1 全面落地交付报告（2026-11-24 凌晨）

- 报告时间：2026-11-24 01:35（Asia/Shanghai）
- 事实基线：`origin/main` @ `b500ba6`（PR #97 合并后）
- 执行模式：多智能体并行 + 主控审查、CI 门禁、真实栈部署验证
- 本轮窗口：2026-11-23 15:38 – 2026-11-24 01:35（约 10 小时）

---

## 一、版本状态

**当前版本：V4.1 S1 全闭环 + S2 全闭环 + S5/S6 部分推进；契约 1.33.0；迁移 head 0071。**

| 维度 | 值 |
|---|---|
| main | `b500ba6` |
| OpenAPI | **1.33.0**（本轮 1.32.0 → 1.33.0） |
| Alembic head | **0071**（新增 `edge_agents` 等 4 表） |
| 迁移总数 | 65 |
| 契约路径 | 245+ |
| 后端测试 | 全量通过（含新增 Edge Agent / 泄漏 / 运维端点测试） |
| 前端测试 | 1118+ passed |
| 质量门禁 | `check.sh` 9 步全过；CI 五项全绿后合并 |
| 仓库卫生 | 单分支 main，无 worktree 堆积 |

---

## 二、已完成项（本轮 7 个 PR，全部 CI 五项全绿后合并）

| PR | 内容 | 里程碑 |
|---|---|---|
| **#91** | **LeRobot v2.1 导出 9 类结构缺口修复 + `lerobot==0.3.3` 官方 Loader 实测通过** | **M5 关键解除** |
| **#92** | V31-012a 端侧 Agent 本地核心（Spool/Manifest/Adapter/Preflight/上传驱动，41 测试） | S2 |
| **#93** | V31-011 Edge Agent 服务端（迁移 0071、配对凭证、心跳、Lease、Agent 专属鉴权、纯拉取 API、契约） | S2 |
| **#94** | V31-012b 真实 DDP HTTP 网关 + 端到端联调（61 测试，修复真实 fd 泄漏） | S2 |
| **#95** | FR-030 Split 防泄漏新增采集会话/受试对象两个分组维度（默认开启） | S5 |
| **#96** | 看板更新 S2 进度 | 文档 |
| **#97** | FR-042 投递队列健康快照端点（只读运维视图，8 测试） | S6 |

### 关键里程碑解除

**M5「训练可交付」核心阻塞已解除**：
- `LE_ROBOT` 导出的 `verification_status` 从 `UNVERIFIED` 提升为 **`VERIFIED_BY_LOADER`**
- 依据：`lerobot==0.3.3` 官方 `LeRobotDataset` 对零手工修补的真实产物**双路构造通过**（`episodes=None` 与 `episodes=[0,1,2]`），`total_episodes=3`、`num_frames=3`、`episode_index=[0,1,2]` 全部校验通过
- 证据：`docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md`（含 9 类缺口逐项修复对照）
- **诚实边界不变**：`loadable_scope=METADATA_ONLY`、`frame_level_read="NOT_CLAIMED_METADATA_ONLY"`、`fps_source="NOMINAL_METADATA_ONLY"` —— 不宣称可训练，只宣称可被官方 Loader 加载元数据

---

## 三、部署与运行状态

### 部署执行（真实栈）

| 步骤 | 结果 |
|---|---|
| `docker compose` 本地栈（postgres 16 + minio） | ✅ 健康运行 |
| `alembic upgrade head`（0070 → 0071） | ✅ **在真实 PostgreSQL 上成功应用** |
| FastAPI 应用装配（真实 settings + 真实路由） | ✅ 新端点全部注册 |

### 部署后核心功能验证（真实 PostgreSQL + 真实 HTTP 路由）

**Edge Agent 全链路（V31-011 + V31-012b）**：

| 步骤 | 结果 |
|---|---|
| 签发一次性配对凭证（必绑 robot，符合设计） | **201** |
| Agent 配对（凭证换专属身份） | **201**，签发 agent_token |
| **凭证重放**（同 token 二次配对） | **401**（单次使用生效） |
| 心跳上报 | **200**，状态 `PAIRED → ACTIVE`，`last_seen_at` 更新 |
| 拉取 assignments（Agent token） | **200** |
| **无 Agent token 访问 assignments** | **401**（身份隔离生效） |
| 管理面详情 | **200**，`status=ACTIVE` |
| 撤销 Agent | **200** |
| **撤销后心跳** | **401**（token 立即失效） |

结论：**S2 Edge Agent 服务端链路在真实栈跑通**。端侧进程仍为 `HARDWARE_CAPTURE_NOT_TESTED`（无真实采集硬件），但协议、鉴权、状态机、隔离、撤销语义都有真实验证。

**FR-042 运维端点**：

| 验证 | 结果 |
|---|---|
| `GET /ops/dispatch-queue` | **200** |
| 实际读到的真实数据 | `pending=5, published=195, failed=5, retrying=0, stale=True, oldest_age=782515s` |
| payload 泄漏检查 | **未泄漏**（响应不含 payload） |
| 四种写方法 | **405**（只读性生效） |

---

## 四、部署验证发现的真实运维问题（FR-042 的首次价值体现）

新端点在真实库上**立即暴露了长期积压**——这类问题此前无任何可见性：

### 卡住的 PENDING 事件（最早可追溯到 2026-11-14，积压约 9 天）

| 事件类型 | 数量 | 最早时间 |
|---|---|---|
| `datasource.import.start` | 6 | 2026-11-14 17:05 |
| `datasource.registered` | 6 | 2026-11-14 17:05 |
| `drill.event` | 5 | 2026-11-14 15:47 |
| `datasource.review.created` | 3 | 2026-11-14 17:40 |
| `datasource.review.decided` | 3 | 2026-11-14 17:40 |

### FAILED 事件（11 条）

| 事件类型 | 数量 | 最后错误 |
|---|---|---|
| `datasource.import.succeed` | 6 | 该导入没有任何媒体资产，无法生成 Episode |
| `ingestion.job.created` | 2 | 需要帧级切片能力 |
| `ingestion.raw_asset.validation_requested` | 1 | **`IntegrityError: duplicate key uq_raw_assets_project_c...`** ← 需关注 |
| `datasource.import_requested` | 1 | 导入任务状态 RUNNING 不允许执行 |
| `processing.run.requested` | 1 | 输入引用必须属于当前项目 |

**处置建议（需业务决策）**：
1. 9 天积压的 PENDING 事件需确认是否有意为之（`drill.event` 是演练数据，可能故意保留）；
2. **`UniqueViolation` 冲突是缺陷**：同一资产重复校验时未做幂等，建议单独修复；
3. 其余 FAILED 多为历史数据问题（无媒体资产、状态冲突），可归档并加告警阈值。

**本轮不做清理**（属不可逆高风险操作）——按用户规则记录等待确认。

---

## 五、未完成项与阻塞

| 项 | 状态 | 阻塞原因 / 解除条件 |
|---|---|---|
| PR #98（看板更新） | CI 中 | 纯文档，后端 CI 跑完即合并 |
| **S3 真实传感器数据物化**（FR-009~015） | 未开工 | 依赖 S2（已闭环）→ 可开工；但需要**真实传感器样本**才能验证 |
| **S4 AI 真实推理**（FR-021/022） | `BLOCKED` | 需 GPU + 模型权重（GroundingDINO/SAM2/VLM） |
| **FR-036/037 训练真实连接器** | `BLOCKED` | 外部训练平台 API 未提供 |
| **FR-038 Staging** | `BLOCKED` | 无 staging 主机/网络/Secrets |
| **FR-039 真实硬件 Pilot** | `BLOCKED` | 无采集硬件 |
| FR-040/041/043/044/045 | 未开工 | 依赖上列条件或属收尾项 |
| FR-001~008 端侧 Agent **真实硬件采集** | `HARDWARE_CAPTURE_NOT_TESTED` | 无机器人/传感器硬件；协议与本地核心已验证 |

---

## 六、风险与回滚方案

| 风险 | 影响 | 回滚 |
|---|---|---|
| 迁移 0071 上线后需回滚 | 新表无历史数据，回滚安全 | `alembic downgrade 0070`（迁移含完整 downgrade，已测） |
| `VERIFIED_BY_LOADER` 被误读为「可训练」 | 下游可能直接开训 | 导出物含 `loadable_scope=METADATA_ONLY` + `frame_level_read` 双重声明；文档明确边界 |
| Agent token 泄漏 | 未授权 Agent 操作 | token 只存哈希、支持轮换/撤销、撤销即失效（已验证）；不进日志 |
| FR-042 端点暴露系统内部失败模式 | 信息面扩大 | 已限定项目负责人口径 + 不含 payload |
| 积压事件长期不处理 | 掩盖真实故障 | FR-042 端点已提供可见性，建议接入告警 |

---

## 七、后续待办（按优先级）

| 优先级 | 事项 | 前置 |
|---|---|---|
| **P0** | 处理部署验证发现的 11 条 FAILED 事件（尤其 `UniqueViolation` 缺陷） | 需业务确认是否清理历史数据 |
| **P0** | PR #98 合并 | CI |
| **P1** | S3 真实传感器物化（FR-009~015）——需真实样本 | 样本提供 |
| **P1** | FR-042 接入告警（积压/失败阈值通知） | 无 |
| **P2** | S4 AI 推理（需 GPU/权重） | 环境 |
| **P2** | FR-036/037 训练连接器（需外部 API） | 平台接口 |
| **P3** | FR-038/039 Staging 与硬件 Pilot | 基础设施 |

---

## 八、本轮交付质量声明

- 全部 **7 个 PR 均通过 CI 五项门禁**（后端 lint/迁移/测试、前端类型/lint/测试/构建、前端 E2E、契约 OpenAPI lint/兼容性、Secret/依赖/Agent 守卫）
- `scripts/check.sh` 9 步口径**未放宽**（新增测试失败 3 次均修根因而非豁免）
- 诚实口径贯穿：`BLOCKED` 逐项标注解除条件，不用总体验收掩盖；`HARDWARE_CAPTURE_NOT_TESTED`、`METADATA_ONLY` 等边界在代码与文档同步声明
- 真实栈验证：迁移在真实 PostgreSQL 应用、API 在真实路由上跑通完整业务链路

**结论：V4.1 S1+S2 已全闭环，M5 核心阻塞已解除，其余项阻塞条件明确且需外部输入（GPU/硬件/staging/平台 API）。**
