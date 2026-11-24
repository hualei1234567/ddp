# V51 FR Status Matrix（24 项逐项复核）

- 复核基线：`HEAD = f92e83a`（**非**蓝图中的 `c71c72a`）
- 复核方式：只读；每项给出**可核验路径**与**实测证据**
- 状态口径：「代码 + 测试 + 运行证据」三者齐备才可 VERIFIED，缺运行证据最高 PARTIAL

## 状态总览

| 状态 | 数量 | 占比 |
|---|---|---|
| ✅ VERIFIED | **4** | 17% |
| 🟡 PARTIAL | **14** | 58% |
| ⛔ BLOCKED | **4** | 17% |
| 🔵 待定（需用户决策） | **2** | 8% |

**关键结论：没有任何一项 P0 数据物化 FR（V51-001~006, 011）达到 VERIFIED。**

---

## 逐项复核

### A 组：数据物化主链（P0，V5.1 的核心）

| ID | 功能 | 蓝图 | **当前** | 证据 / 差距 |
|---|---|---|---|---|
| **V51-001** | 真实 SensorStream 物化 | PARTIAL | 🟡 **PARTIAL（实质未开始）** | 类型定义在 `data-platform/ddp_data/canonical/v1.py:235`；**全仓零处构造 `SensorStream(...)` 实例**（`grep` 实测）；无持久化表；无 producer |
| **V51-002** | Observation 逐帧 payload | PARTIAL | 🟡 **PARTIAL（实质未开始）** | 定义 `canonical/v1.py:322`；**零实例化**；命中的 `IAAObservation`/`RecordObservation` 为同名无关类型；无特征表、无 payload 存储 |
| **V51-003** | Action 真实源解析 | PARTIAL | 🟡 **PARTIAL（实质未开始）** | 定义 `canonical/v1.py:279`；**零实例化**；无 `ACTION_SOURCE_ABSENT` 语义落地 |
| **V51-004** | 多流同步 | PARTIAL | 🟡 **PARTIAL（有能力无实测）** | `backend/app/application/processing/time_alignment_stage.py`（204 行）真实实现，依赖 `domain/episodes/sync_analysis.py` + `sync_drift.py`；**但真实栈中该阶段 SKIPPED**，无多流样本实测 |
| **V51-005** | Adapter 能力分级 | PARTIAL | 🟡 **PARTIAL（分级维度不符）** | 现有 `AdapterCapability` 仅 5 值：`PARSE/DETECT/FETCH/VERIFY/WRITE`（`adapters/registry.py:48`）；**蓝图要求 6 级**：DETECT/INSPECT/PAYLOAD_PARSE/STREAM_EXTRACT/EPISODE_BUILD/ROUND_TRIP。**已注册 Adapter 仅 2 个**：`lerobot.human1m_preview`、`generic.episode_jsonl` |
| **V51-006** | DATA_CLEANING | SKIPPED | 🔵 **显式 SKIPPED（正确状态）** | `processing/stages.py:170-172` 显式 SKIPPED 并写明原因；无规则版本、无阈值、无证据产物 |
| **V51-007** | 步骤级幂等恢复 | PARTIAL | 🟡 **PARTIAL** | `domain/processing/steps.py` 有 `step_id`/`attempt`/状态机；`STAGE_SEQUENCE` 只前不退；**缺**「input hash + parser version + pipeline version + config hash」复合幂等键（蓝图 §6 要求）；`processing_artifacts=0` 无法验证复用 |

### B 组：Adapter 与格式能力

| ID | 功能 | 蓝图 | **当前** | 证据 / 差距 |
|---|---|---|---|---|
| V51-005 | （同上） | PARTIAL | 🟡 PARTIAL | 同上 |
| — | HDF5 解析能力 | — | ✅ **底层已实现（未接入六级分级）** | `ddp_data/hdf5/`：用官方 `h5py==3.16.0` 只读，有界读取 + 路径脱敏；`read_hdf5_structure()` 可读真实结构 |
| — | ROS Bag 解析能力 | — | ✅ **底层已实现** | `ddp_data/rosbag/`：用 `rosbags` SDK 读索引/metadata（不遍历 messages），支持 rosbag1/rosbag2 |
| — | MP4 解析能力 | — | ✅ **底层已实现** | `ddp_data/media/mp4.py`：受控 ffprobe，有超时/输出上限/错误分类 |
| — | MCAP 解析能力 | — | ❌ **无** | 真实栈有 5 个 MCAP RawAsset，但无 MCAP parser |

### C 组：训练就绪（P0）

| ID | 功能 | 蓝图 | **当前** | 证据 / 差距 |
|---|---|---|---|---|
| **V51-011** | LeRobot 逐帧 Loader 验证 | METADATA_ONLY | 🟡 **METADATA_ONLY（E1 实测确认）** | 本次实测：`lerobot==0.3.3` 可构造、可 `ds[0]`，但返回仅 6 个元数据键 `episode_index/task_index/length/timestamp/split/task`，**`observation` 与 `action` 均不存在**；导出 `features` 仅 5 项。需真实逐帧数据才能升级 |
| **V51-012** | Lineage 正反向 E2E | PARTIAL | 🟡 **PARTIAL（性质已变）** | PR #108 真实栈 E2E：8 段中 **7 段 PASS**（Dataset→Episode、→RawAsset 2 跳、RawAsset→Episode/Dataset、→DATA_SOURCE 两段组合、graph 自洽、跨项目隔离 403、RawAsset→MinIO）；**Dataset→Session 段 UNVERIFIABLE**（真实库中进入 Dataset 的资产全为 IMPORT，无上传/采集会话）。已修 3 个真缺陷 |
| **V51-024** | Dataset 来源许可校验 | PARTIAL | 🟡 **PARTIAL** | 有 `license`/`access_condition` 字段与 Gate 逻辑；缺「许可缺失阻断发布/共享」的强制校验 |

### D 组：治理与运维（M1）

| ID | 功能 | 蓝图 | **当前** | 证据 / 差距 |
|---|---|---|---|---|
| **V51-008** | 标注冲突恢复 | PARTIAL | ✅ **VERIFIED**（PR #107） | 409 携带 `expected_version`/`current_version`；复制草稿；逐项人工合并（默认保留我的）；刷新不丢输入；50 测试（前端 46 + 后端 4） |
| **V51-009** | 分配负载提示 | PARTIAL | ✅ **VERIFIED**（PR #104） | `POST /annotation-assignment-capacity-preview` 只读批量；抽屉实时展示当前/投影/上限/占比/超限；提示不阻断；39 测试；契约 1.34.0 |
| **V51-010** | QC 证据区间可视化 | PARTIAL | 🟡 **PARTIAL** | `QualityRunDiffReportPanel` 有四类差异分组，但**无时间轴定位/区间高亮/点击跳转**（`grep` 实测无 time/jump/seek/highlight） |
| **V51-013** | 多角色浏览器权限 E2E | PARTIAL | 🟡 **PARTIAL（缺关键证明）** | 4 个 E2E spec 均无角色切换或越权断言（`grep role/403/越权` 实测 0 命中）；RBAC 后端有 `ROLE_PERMISSIONS` 矩阵与单点测试 |
| **V51-014** | PG 恢复演练 | PARTIAL | ✅ **VERIFIED**（PR #103） | `backup-restore-drill.sh` 隔离库恢复；表数/版本/RPO 锚点/16 表行数/触发器/约束/索引全一致 |
| **V51-015** | MinIO 恢复演练 | PARTIAL | ✅ **VERIFIED**（PR #103） | 2094 对象全量镜像；**抽样对象真删再恢复**，sha256 前后一致 |
| **V51-016** | Spool 恢复演练 | PARTIAL | ✅ **VERIFIED**（PR #103） | 临时副本 tar 备份 → 删状态库与数据 → 恢复 → 校验状态机两态 + 字节一致；**此前完全未覆盖** |
| **V51-017** | Frozen Dataset 引用保护 | PARTIAL | 🟡 **PARTIAL（无实现）** | `grep` 实测：无 raw_asset 删除用例、无 retention domain 类、无冻结引用阻断；`retention_policy_id` 字段存在但无保护逻辑 |
| **V51-022** | P1 风险逐项收敛 | PARTIAL | 🟡 **PARTIAL** | risk-list 仍 64 项（P1 24 / P2 40），未逐项对账关闭 |
| **V51-023** | 数据可用性漏斗指标 | VERIFIED 基础 | 🟡 **PARTIAL** | 有 `/ops/dispatch-queue`（队列健康）；**无** `RAW_INGESTED→STREAMS_EXTRACTED→…→LOADER_FRAME_VERIFIED` 漏斗指标 |

### E 组：外部阻塞（保持 BLOCKED）

| ID | 功能 | **当前** | 解除条件 |
|---|---|---|---|
| V51-018 | Staging 部署验收 | ⛔ **BLOCKED** | 主机、网络、Secrets、域名/证书 |
| V51-019 | 真实硬件 Pilot | ⛔ **BLOCKED** | 真实机器人/传感器/SDK/采集工作站 |
| V51-020 | AI 真实推理 | ⛔ **BLOCKED** | GPU + 模型权重（`grep gpu/weights/checkpoint config` = 0 处） |
| V51-021 | 训练平台连接器 | ⛔ **BLOCKED** | 外部平台 API/认证/回调契约 |

---

## 需要用户决策的 2 项

| ID | 问题 | 选项 |
|---|---|---|
| **V51-005 / Adapter 分级** | 现有 5 值能力枚举 vs 蓝图 6 级 | A. 扩展为 6 级并迁移（改 `data-platform` 公开枚举）；B. 保留 5 值 + 新增独立「格式能力矩阵」（不改枚举）；C. 只对 Golden Path 格式做 6 级评估 |
| **V51-011 升级路径** | METADATA_ONLY → FRAME_LEVEL 需要真实逐帧数据 | A. 用公开数据集（如 RoboMimic/DROID）做逐帧验证，标 `PUBLIC_DATASET_INTEGRATION_TESTED`；B. 等真实硬件；C. 先做框架+合成测试 |

---

## 与蓝图基线的状态变化（5 项）

| ID | 蓝图 | 当前 | 来源 |
|---|---|---|---|
| V51-008 | PARTIAL | ✅ VERIFIED | PR #107 |
| V51-009 | PARTIAL | ✅ VERIFIED | PR #104 |
| V51-014/015/016 | PARTIAL | ✅ VERIFIED | PR #103 |
| V51-012 | PARTIAL | 🟡 PARTIAL（7/8 段 PASS） | PR #108 |
| V51-010 | PARTIAL | 🟡 PARTIAL（未变） | — |

**M1 治理缺口剩余工作：V51-013、V51-017、V51-010 三项**（原计划 6 项，已完成 3 项）。
