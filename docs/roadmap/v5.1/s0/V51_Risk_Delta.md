# V51 Risk Delta（风险增量与对账）

- 依据：V4.1 审计报告 + 最新 `docs/roadmap/v2.1/n0/risk-list.md`（64 项：P1 24 / P2 40）+ S0 实测发现
- **不假设任何历史风险已关闭**，逐类对账

---

## 一、继承的风险（未变化）

| 类 | 数量 | 状态 | 说明 |
|---|---|---|---|
| risk-list 总计 | 64 | 未对账关闭 | P1 24 / P2 40；S0 未逐项复核（属 V51-022 工作） |
| ADR | 47 | 稳定 | 本轮无新增 ADR |

---

## 二、S0 新发现的风险（本轮新增）

### 🔴 R-V51-001：无任何真实机器人数据样本（**最高风险**）

| 项 | 内容 |
|---|---|
| 事实 | 仓库内 11 个二进制样本中，`.h5`(166B)/`.mcap`(102B)/`.parquet`(105B) 内容直接写着 `SYNTHETIC_*_CONTAINER_ONLY`；4 份真实公开样本只登记了 facts（SHA256/结构）但无文件本体；`env \| grep DDP_TEST` 无输出 |
| 影响 | **V5.1 P0 主体（V51-001~006、011）无法验收**。没有真实 payload，任何 Parser 都只能对合成数据有效 |
| 概率 | 确定（已实测） |
| 缓解 | 下载 RoboMimic HDF5（MIT，36.7MB，facts 已登记）；或用户提供真实设备导出 |
| 阻塞级别 | **M2 全部阻塞在此** |
| 需用户决策 | ✅ 是 |

### 🔴 R-V51-002：数据物化零落地，且失败原因被误判为缺陷

| 项 | 内容 |
|---|---|
| 事实 | `processing_artifacts = 0`；4 次 ProcessingRun 中 2 FAILED（`input_ref_not_project_scoped`）、1 CANCELLED、1 SUCCEEDED；所有物化阶段 SKIPPED |
| 影响 | 无法用历史数据验证任何物化能力；需先修通处理链路 |
| 缓解 | 用 Golden Sample 新建 ProcessingRun 走通全链路；旧失败数据归档不重试 |
| 备注 | `input_ref_not_project_scoped` 是**正确的跨项目隔离**（`stages.py:137-146`），**不要"修复"它** |

### 🟠 R-V51-003：Canonical Schema 字段不足，改动有兼容风险

| 项 | 内容 |
|---|---|
| 事实 | SensorStream 字段覆盖约 60%；缺 `dtype`/`parser_version`/`timestamp_raw` 双时间/`missing_count`/`validity`/`source_index` 等 10 类 |
| 影响 | 不改无法表达真实 payload；改了可能破坏 87 个历史 dataset_versions |
| 缓解 | `SCHEMA_VERSION=1.1` 增量（全可选字段，保留 1.0 可读）+ ADR；禁止破坏性改动 |
| 需用户决策 | ✅ 需确认方案 A/B/C |

### 🟠 R-V51-004：Adapter 能力语义与蓝图六级不对齐

| 项 | 内容 |
|---|---|
| 事实 | 现有 `AdapterCapability` 5 值（PARSE/DETECT/FETCH/VERIFY/WRITE）≠ 蓝图 6 级（DETECT/INSPECT/PAYLOAD_PARSE/STREAM_EXTRACT/EPISODE_BUILD/ROUND_TRIP） |
| 影响 | 若直接套用蓝图口径会产生「注册成功 = 完整支持」的误报（正是痛点二根因） |
| 缓解 | 扩展枚举或建独立能力矩阵（需 ADR）；UI 按格式分别展示 |
| 需用户决策 | ✅ 需选方案 |

### 🟡 R-V51-005：HDF5/ROS Bag 能力被高估

| 项 | 内容 |
|---|---|
| 事实 | HDF5 只读**结构**不读数值；ROS Bag **显式不遍历 messages**；两者都只到 INSPECT |
| 影响 | 「支持 HDF5/ROS Bag」是错误表述；无法直接产出 stream |
| 缓解 | 在能力矩阵中如实标 INSPECT；升级计划见 Adapter 矩阵 §3 |

### 🟡 R-V51-006：无 MCAP parser 但存量有 MCAP 资产

| 项 | 内容 |
|---|---|
| 事实 | 真实栈 283 RawAsset 中 5 个是 MCAP；仓库无 MCAP parser |
| 影响 | 这 5 个资产无法进入任何解析链路 |
| 缓解 | 排在 Golden Path 之后引入 `mcap` SDK |

### 🟡 R-V51-007：FR-040 Lineage 的 Dataset→Session 链永远无数据

| 项 | 内容 |
|---|---|
| 事实 | PR #108 实测：进入 DatasetVersion 的资产 100% 来自 IMPORT（HF 导入），不经上传/采集会话；`Episode.capture_session_id` = 0/4 |
| 影响 | 该段链路无法靠现有数据验证，只能靠一次真实采集 |
| 缓解 | 要么补一次真实采集链路，要么产品裁定「纯导入数据不要求 Session 归属」 |
| 需用户决策 | ✅ 是 |

### 🟡 R-V51-008：Frozen Dataset 引用保护完全缺失

| 项 | 内容 |
|---|---|
| 事实 | `grep` 实测：无 raw_asset 删除用例、无 retention domain 类、无冻结引用阻断；`retention_policy_id` 字段存在但无保护逻辑 |
| 影响 | 冻结数据集引用的资产可能被误删 → 数据集不可复现 |
| 缓解 | V51-017 实现：事务级阻断 + 删除影响预览 + 软删除路径 |

### 🟡 R-V51-009：RBAC 只有后端矩阵，无浏览器越权证明

| 项 | 内容 |
|---|---|
| 事实 | 4 个 E2E spec 均无角色切换或 403 断言（`grep` = 0 命中）；后端有 `ROLE_PERMISSIONS` 与单点测试 |
| 影响 | 无法证明「UI 隐藏 + API 拒绝」双层边界 |
| 缓解 | V51-013 补浏览器 E2E（至少 2 项目 × 多角色） |

---

## 三、外部依赖风险（保持 BLOCKED，不得虚构）

| ID | 依赖缺失 | 解除条件 | 是否阻塞核心 |
|---|---|---|---|
| V51-018 | Staging 主机/网络/Secrets/域名 | 资源到位 | ❌ 不阻塞（核心链可在本地栈验收） |
| V51-019 | 真实机器人/传感器/SDK | 硬件到位 | 🟡 阻塞「真实硬件 Pilot」但可用公开数据集替代验证 |
| V51-020 | GPU + 模型权重 | 资源到位 | ❌ 不阻塞（蓝图明确不依赖 AI） |
| V51-021 | 外部训练平台 API/认证/回调 | 契约到位 | ❌ 不阻塞（导出即可交付） |

---

## 四、风险优先级建议

| 优先级 | 风险 | 行动 |
|---|---|---|
| **P0（立即）** | R-V51-001 无真实样本 | 用户确认 RoboMimic 下载方案 → 立即获取 |
| **P0（立即）** | R-V51-002 物化零落地 | Golden Sample 到位后新建 Run 走通全链 |
| **P0（M2 前）** | R-V51-003 Canonical 字段 + R-V51-004 Adapter 分级 | 各自出 ADR |
| P1（M1） | R-V51-008 引用保护、R-V51-009 RBAC E2E | 无外部依赖，可并行 |
| P1（M3） | R-V51-007 Lineage Session 段 | 需产品口径裁定 |
| P2 | R-V51-005/006 格式能力 | Golden Path 之后 |

---

## 五、诚实边界声明（V5.1 全程有效）

**绝不可宣称**（本轮 S0 已实证）：

1. ❌「平台已有真实传感器数据物化能力」——`processing_artifacts = 0`
2. ❌「支持 HDF5/ROS Bag/MCAP 数据」——仅 HDF5/ROS Bag 到 INSPECT，MCAP 无
3. ❌「LeRobot 已训练就绪」——实测 `ds[0]` 无 observation/action
4. ❌「已有真实样本测试」——真实样本测试 100% skip，无 `DDP_TEST_*`
5. ❌「Episode 来自真实解析」——206 Episode 未经真实 payload 解析
6. ❌「合成容器头测试等于格式兼容」——文件内嵌 `SYNTHETIC_*_ONLY`
7. ❌「真实硬件 Pilot 已验收」——`HARDWARE_CAPTURE_NOT_TESTED`
8. ❌「跨项目隔离是缺陷」——`input_ref_not_project_scoped` 是正确安全行为

**可以宣称**：

1. ✅ 工程质量健康（check.sh 9 步全过，2953 用例，迁移 0072 单头）
2. ✅ 治理层 P1 四项已完成三项（冲突恢复/负载提示/恢复演练）
3. ✅ 底层格式 SDK 真实可用（h5py/rosbags/ffprobe，非自研非 stub）
4. ✅ 8 阶段处理框架齐备（1965 行，含同步/分段/标定/质量）
5. ✅ Canonical 6 实体定义完备且机制优秀（`missing_fields` 强制登记）
6. ✅ LeRobot 元数据级导出通过官方 Loader 验证（`METADATA_ONLY` 口径准确）
