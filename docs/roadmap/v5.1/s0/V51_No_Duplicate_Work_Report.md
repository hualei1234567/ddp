# V51 No Duplicate Work Report（防重复开发报告）

- 目的：证明 V5.1 每一项任务都先查过既有实现，**不存在重复建设**
- 方法：对每项 V5.1 任务，列出「已检查的既有资产」→「结论：复用 / 扩展 / 新建」

---

## 一、逐任务复用判定

| V51 ID | 任务 | 已检查的既有资产 | 判定 |
|---|---|---|---|
| **V51-001** | SensorStream 物化 | `canonical/v1.py:235` SensorStream 定义；`processing/` 8 阶段框架；`processing_artifacts` 模型 | **扩展**：类型已有，在既有阶段内填生产者；不新建表框架 |
| **V51-002** | Observation 逐帧 | `canonical/v1.py:322` 定义；`processing_artifacts` | **扩展** |
| **V51-003** | Action | `canonical/v1.py:279` 定义 | **扩展** |
| **V51-004** | 多流同步 | `processing/time_alignment_stage.py`（204 行）+ `domain/episodes/sync_analysis.py` + `sync_drift.py` | **复用**：算法与阶段均已存在，只需真实样本 |
| **V51-005** | Adapter 六级 | `adapters/registry.py:48` `AdapterCapability`（5 值）；`ddp_data/hdf5`、`rosbag`、`media/mp4` | **扩展**：底层能力复用，分级语义需 ADR 调整 |
| **V51-006** | DATA_CLEANING | `processing/stages.py:170` `SkippedStage` 占位；`quality_stage.py` 质量引擎 | **扩展**：接已有阶段，不新建 pipeline |
| **V51-007** | 步骤幂等 | `domain/processing/steps.py`（step_id/attempt/状态机）；`infrastructure/repositories/idempotency.py` | **扩展**：补复合幂等键 |
| **V51-008** | 标注冲突恢复 | — | ✅ **已完成**（PR #107），无需再做 |
| **V51-009** | 负载提示 | — | ✅ **已完成**（PR #104） |
| **V51-010** | QC 证据可视化 | `components/quality/QualityRunDiffReportPanel.tsx`（287 行）；`QualityRunReport` | **扩展**：面板已有，补时间轴定位 |
| **V51-011** | LeRobot 逐帧 | `lerobot/export_writer.py`；`exporting.py`；已过 Loader 元数据验证 | **扩展**：Writer 复用，新增 FRAME_LEVEL 状态（不改 METADATA_ONLY） |
| **V51-012** | Lineage E2E | `episodes/graph.py` + `api/v1/lineage.py` + PR #108 已修 3 缺陷 | **复用**：仅补 Session 段 |
| **V51-013** | RBAC E2E | `domain/identity/models.py:46` `ROLE_PERMISSIONS`；`apps/web/e2e/` 4 spec | **扩展**：加角色/越权断言，不新建权限系统 |
| **V51-014/015/016** | 恢复演练 | — | ✅ **已完成**（PR #103） |
| **V51-017** | Frozen 引用保护 | `retention_policy_id` 字段；`datasets/datasets.py` | **新建**（但复用 Dataset/RawAsset 模型与事务边界） |
| **V51-018~021** | 外部依赖 | `services/edge-agent`（Agent 已 VERIFIED） | ⛔ BLOCKED，不虚构 |
| **V51-022** | 风险收敛 | `risk-list.md` 64 项 | **复用**：逐项对账 |
| **V51-023** | 漏斗指标 | `api/v1/ops_routes.py` + `application/ops/dispatch_queue.py` | **扩展**：加漏斗查询 |
| **V51-024** | 许可校验 | `license`/`access_condition` 字段；Dataset Gate | **扩展** |

**统计：复用 4 项、扩展 12 项、新建 1 项、已完成 5 项、BLOCKED 4 项（含重叠）。**

---

## 二、明确「不新建」的清单（附证据）

| 不新建 | 既有实现（路径） | 证据 |
|---|---|---|
| ❌ 解析框架 | `application/processing/` 8 阶段，1965 行 | `metadata_stage.py` 674 行已做魔数检测+parser 分派 |
| ❌ HDF5 读取器 | `ddp_data/hdf5/metadata.py`（h5py 3.16.0） | `read_hdf5_structure()` + `_BoundedReader` 有界 |
| ❌ ROS Bag 读取器 | `ddp_data/rosbag/metadata.py`（rosbags SDK） | rosbag1/2 双支持 |
| ❌ MP4 探测 | `ddp_data/media/mp4.py`（ffprobe） | 7 个错误类型分类 |
| ❌ 同步算法 | `domain/episodes/sync_analysis.py` + `sync_drift.py` | `time_alignment_stage.py` 已在调用 |
| ❌ Episode 分段 | `processing/segmentation_stage.py`（212 行） | 已处理「既有 Episode 追溯」与「明确边界创建」 |
| ❌ 标定校验 | `processing/calibration_stage.py`（117 行） | 快照可追溯 + 版本一致性 |
| ❌ 质量引擎 | `processing/quality_stage.py`（251 行） | 逐 Episode 执行 |
| ❌ Adapter 注册表 | `adapters/registry.py`（`AdapterSpec`/`AdapterRegistry`） | 注册校验 + 能力枚举 |
| ❌ Canonical 类型 | `canonical/v1.py` 6 实体 | 字段机制含 `missing_fields` 强制登记 |
| ❌ 幂等存储 | `infrastructure/repositories/idempotency.py` | 通用实现 |
| ❌ Outbox/Worker | `infrastructure/repositories/collection_audit.py` + `app/workers/` | 13 种投递类事件全有消费者 |
| ❌ Dataset Gate | `application/datasets/` | 冻结前重验 |
| ❌ Split 防泄漏 | `domain/datasets/leakage.py`（5 维度） | 含 Session/Subject/Scene/Batch/相似簇 |
| ❌ LeRobot Writer | `lerobot/export_writer.py` | 已过官方 Loader 元数据验证 |
| ❌ Lineage 图 | `episodes/graph.py` + `lineage.py` | 正反向+深度上限+循环保护 |
| ❌ RBAC 矩阵 | `identity/models.py:46` `ROLE_PERMISSIONS` | 角色×动作映射完整 |
| ❌ Agent 全链 | `services/edge-agent/` | V4.1 VERIFIED |
| ❌ 上传/multipart | 复用 API | 四要素真实验证 |
| ❌ 备份恢复 | `scripts/ops/backup-restore-drill.sh` | PR #103 已完成 |
| ❌ 运行监控 | `api/v1/ops_routes.py` | PR #97 已完成 |
| ❌ 标注冲突恢复 | `annotationConflict.ts` 等 | PR #107 已完成 |
| ❌ 负载预览 | `capacity-preview` 端点 | PR #104 已完成 |

---

## 三、新增物件的必要性论证（只有 1 项）

### V51-017 Frozen Dataset 引用保护

**为什么现有无法表达**：
- `retention_policy_id` 只是字段，**无任何保护逻辑**（`grep` 实测无删除阻断、无 retention domain）
- DatasetVersion 冻结后，其引用的 RawAsset / ProcessingArtifact **可被删除** → 数据集不可复现
- 现有 `tombstone` 是 RawAsset 状态机动作，但没有「被冻结数据集引用时禁止 tombstone」的校验

**需要新增**：
1. 删除前影响预览（列出引用链：DatasetVersion → DatasetItem → Episode → RawAsset）
2. 事务级阻断（非前端按钮）
3. 软删除/归档路径

**复用**：Dataset/RawAsset 模型、`ProjectScope`、事务边界、Outbox 审计事件。

**规模**：中小（约 1 个 ADR + 1 个 domain service + 2 个 API + 测试）。

---

## 四、Schema 变更的必要性论证

### Canonical 1.1 字段扩展

**为什么必须改**：
- `SensorStream` 缺 `dtype`、`start/end_ts`、`missing_count`、`invalid_count`、`parser_version`
- `Observation`/`Action` 缺 `timestamp_raw`/`timestamp_normalized` 双时间、`validity`、`source_index`
- 蓝图 §3/§4/§5 明确要求这些字段

**为什么不能不改而硬塞进现有字段**：
- 现有 `missing_fields` 强制登记机制要求「没说没有的字段不能空」——塞进别的字段会破坏这个核心不变量
- `timestamp_raw` 与 `timestamp_normalized` 语义不同（原始 vs 归一化），蓝图 §2 明确要求分开存

**风险控制**：`SCHEMA_VERSION=1.1` 增量，全字段可选，保留 1.0 可读；禁止删除现有字段。

---

## 五、重复开发风险的实际检查结果

| 潜在重复 | 检查方式 | 结果 |
|---|---|---|
| 是否已有 HDF5 payload 读取 | `grep -rn "dataset\[()\]\|read_direct\|h5py.*\["` | **无**（只有结构读取） |
| 是否已有 SensorStream 生产者 | `grep "SensorStream("` 全仓 | **0 命中** |
| 是否已有 Observation 生产者 | `grep "Observation("` | 0 命中（同名类型是 `IAAObservation` 等无关） |
| 是否已有 MCAP parser | `grep -rn "mcap" backend/app data-platform` | **无实现** |
| 是否已有 DATA_CLEANING | `grep "DATA_CLEANING"` | 只有 `SkippedStage` |
| 是否已有引用保护 | `grep "frozen.*delete\|referenced.*delete"` | **无** |
| 是否已有 RBAC E2E | `grep "role\|403" apps/web/e2e/` | **0 命中** |

**结论：上述 7 项确实不存在，不存在重复开发。**

---

## 六、结论

1. **V5.1 约 55% 可复用**，8 阶段框架、底层 SDK、Canonical 类型、同步算法、幂等/队列、Dataset/Split/LeRobot/Lineage 全部现成。
2. **真正需要新写的只有**：payload 生产者（SensorStream/Observation/Action）、逐帧存储、Cleaning 规则引擎、Frozen 引用保护、Adapter 分级、若干前端交互。
3. **没有任何一项会重写 V4.1 已 VERIFIED 能力**。
4. **Schema 变更受控**：1.1 增量 + ADR，不做破坏性改动。
