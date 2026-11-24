# V51 Existing Code Reuse Map（禁止重复开发）

- 复核方式：逐项在仓库中定位真实实现路径（E2 代码核实 + 行号）
- 原则：**能复用的一律复用**；只有证明现有对象无法表达时才新增（需 ADR）

---

## 一、可直接复用的资产（按价值排序）

### 1. 处理阶段框架（**最高价值复用**）

`backend/app/application/processing/` 已有 8 阶段的完整框架，**共 1965 行**：

| 文件 | 行数 | 能力 | V5.1 用途 |
|---|---|---|---|
| `metadata_stage.py` | **674** | METADATA_EXTRACTION / FORMAT_PARSING；按内容魔数选 parser；产物为 JSON 对象；**不伪造 Episode** | 直接扩展为真实 payload 解析入口 |
| `quality_stage.py` | 251 | QUALITY_PRE_CHECK；Episode 质量引擎 | 复用为 Cleaning 后质量汇总 |
| `segmentation_stage.py` | 212 | EPISODE_SEGMENTATION；既有 Episode 追溯 + 明确边界时创建 | 复用，补多流边界规则 |
| `time_alignment_stage.py` | 204 | **TIME_ALIGNMENT 严格 manifest 驱动**；依赖 `sync_analysis.py` + `sync_drift.py` | 复用为 FR-012 多流同步主实现 |
| `calibration_stage.py` | 117 | CALIBRATION_VALIDATION；校验快照可追溯未漂移 | 直接复用 |
| `hdf5_metadata.py` | 74 | HDF5 结构 → Stage 有界摘要（192KiB 预算） | 复用 |
| `rosbag_metadata.py` | 59 | ROS Bag 索引 → Stage 有界摘要 | 复用 |
| `stages.py` | — | Stage 协议 + `SkippedStage` + 跨项目隔离校验 | 复用 |
| `pipeline.py` / `use_cases.py` | — | 阶段编排、失败闭合、状态机推进 | 复用 |

**结论：V5.1 的 FR-009~015 不需要新建处理框架，只需在既有 8 阶段内填充真实 payload 生产者。**

### 2. 底层格式读取能力（**已真实实现，非 stub**）

| 能力 | 路径 | 实现方式 | 可用性 |
|---|---|---|---|
| HDF5 | `data-platform/ddp_data/hdf5/` | 官方 `h5py==3.16.0` 只读；`_BoundedReader` 有界读取；错误路径脱敏 | ✅ 可读真实结构（`read_hdf5_structure`） |
| ROS Bag | `data-platform/ddp_data/rosbag/` | `rosbags` SDK 读索引/metadata（**不遍历 messages**）；支持 rosbag1/2 | ✅ 可读真实 topic/时间范围 |
| MP4 | `data-platform/ddp_data/media/mp4.py` | 受控 ffprobe，含超时/输出上限/错误分类 | ✅ 可读真实流信息 |
| Parquet | `data-platform/ddp_data/parquet/` | pyarrow | ✅ |
| LeRobot | `data-platform/ddp_data/lerobot/` | `parser.py` / `export_writer.py` / `event_graph_adapter.py` | ✅ 元数据级 |

### 3. Canonical 类型定义（**已完备，只缺生产者**）

`data-platform/ddp_data/canonical/v1.py` 已有 6 个实体：
- `Frame`(162) / `VideoStream`(199) / **`SensorStream`(235)** / **`Action`(279)** / **`Observation`(322)** / `Episode`(370)

字段已覆盖蓝图 §3~§5 的绝大部分要求（`stream_id`/`modality`/`unit`/`frame_id`/`frame_convention`/`calibration_id`/`schema_version`/`missing_fields` 等）。

**差距：全仓零处构造实例**（`grep 'SensorStream('` 等 = 0）。任务不是重定义类型，而是**写生产者**。

配套：`canonical/_schema.py`（字段规格）、`json_schema.py`（导出 JSON Schema，check.sh 第 9 步守护）、`projection.py`（投影）。

### 4. 同步分析（FR-012 的核心算法已有）

| 能力 | 路径 |
|---|---|
| 同步分析 | `backend/app/domain/episodes/sync_analysis.py` — `analyze_stream_sync()` |
| 漂移估计 | `backend/app/domain/episodes/sync_drift.py` — `SyncDriftError` |
| 标定模型 | `backend/app/domain/calibration/` + `calibration_stage.py` |

**V5.1 FR-012 不需要新写算法**，只需真实多流样本喂给现有实现。

### 5. 幂等 / 租约 / 队列（全部复用）

| 能力 | 路径 | 说明 |
|---|---|---|
| Outbox | `infrastructure/repositories/collection_audit.py` | `dispatch=True` 才进 PENDING；13 种投递类事件全部有消费者+测试 |
| Worker | `app/workers/`（outbox/exports/datasources/ingestion_jobs/processing/quality） | 幂等 + CAS + 死信 |
| 步骤状态机 | `domain/processing/steps.py` + `state_machines.py` | 只前不退 |
| 制品模型 | `domain/processing/artifacts.py` | `ProcessingArtifactKind` 已定义 |
| 幂等仓储 | `infrastructure/repositories/idempotency.py` | 通用幂等键存储 |

### 6. Dataset / LeRobot / Lineage（复用，不重建）

| 能力 | 路径 | 复用方式 |
|---|---|---|
| Dataset Gate | `application/datasets/` | 复用，补 Stream/Observation/Loader 条件 |
| Split 防泄漏 | `domain/datasets/leakage.py` + `policies.py` | 已含 Session/Subject/Scene/Batch/相似簇 5 维 |
| Canonical Export | ADR-0046 | 复用 |
| LeRobot Writer | `data-platform/ddp_data/lerobot/export_writer.py` | 扩展逐帧 feature，**不改 METADATA_ONLY 语义**（新增 FRAME_LEVEL 状态） |
| Lineage 图 | `application/episodes/graph.py` + `api/v1/lineage.py` | 复用；PR #108 已补 `UPLOADED_BY` 投影边 |
| Manifest | `application/datasets/` + ADR | 复用 |

### 7. Agent / 上传 / 恢复（V4.1 已 VERIFIED，只回归）

| 能力 | 路径 | 状态 |
|---|---|---|
| Edge Agent 全链 | `services/edge-agent/` | VERIFIED（`HARDWARE_CAPTURE_NOT_TESTED`） |
| multipart 上传 | 复用 API | VERIFIED（真实 MinIO 四要素） |
| 备份恢复 | `scripts/ops/backup-restore-drill.sh` | VERIFIED（PR #103） |
| 运行监控 | `api/v1/ops_routes.py` + `application/ops/dispatch_queue.py` | VERIFIED，扩展漏斗指标 |
| 标注冲突恢复 | `apps/web/src/services/annotationConflict.ts` 等 | VERIFIED（PR #107） |
| 负载提示 | `POST /annotation-assignment-capacity-preview` | VERIFIED（PR #104） |

---

## 二、明确的差距（**必须新建**，附理由）

| 差距 | 为什么不能复用 | 规模评估 |
|---|---|---|
| **SensorStream/Observation/Action 生产者** | 类型有定义但零实例化；无可复用生产者 | **核心工作量**，需在 8 阶段内填充 |
| **逐帧 payload 存储** | 现有 `processing_artifacts` 只存 JSON 元数据；蓝图要求「高频大数组优先列式/二进制对象存储，DB 只存索引范围引用」 | 中（复用 MinIO + 新 artifact kind） |
| **Adapter 六级能力分级** | 现有 5 值枚举 `PARSE/DETECT/FETCH/VERIFY/WRITE` 与蓝图 6 级语义不同 | 小（枚举/矩阵扩展，需 ADR） |
| **DATA_CLEANING 规则引擎** | 显式 SKIPPED，无规则版本/阈值/证据 | 中 |
| **复合幂等键** | 现有 step 只有 `attempt`，无 `input_hash+parser_ver+pipeline_ver+config_hash` 组合键 | 小 |
| **MCAP parser** | 无实现，但真实栈有 5 个 MCAP 资产 | 中（`mcap` SDK） |
| **Frozen Dataset 引用保护** | 无删除阻断、无 retention domain、无影响预览 | 中 |
| **RBAC 浏览器 E2E** | 4 个 spec 无角色/越权断言 | 小（测试为主） |
| **数据可用性漏斗指标** | 只有队列健康，无成熟度漏斗 | 小 |
| **QC 证据区间可视化** | 有差异面板，无时间轴定位 | 小~中（前端为主） |

---

## 三、重复开发风险清单（**禁止做的事**）

| 禁止 | 理由 |
|---|---|
| ❌ 新建「解析中心」「物化中心」 | 已有 8 阶段 framework + `processing_artifacts` |
| ❌ 重写 HDF5/ROS Bag/MP4 读取 | `ddp_data` 已用官方 SDK 真实实现且有界脱敏 |
| ❌ 新写同步算法 | `sync_analysis.py` + `sync_drift.py` 已存在 |
| ❌ 新写 Adapter Registry | `adapters/registry.py` 已有能力枚举 + 注册校验 |
| ❌ 新写幂等/队列 | Outbox + Worker + `idempotency.py` 齐备 |
| ❌ 重定义 Canonical 类型 | 6 实体字段已覆盖蓝图要求；改 Schema 需 ADR 且会破全链兼容 |
| ❌ 新建 RBAC / 权限系统 | `ROLE_PERMISSIONS` 矩阵完整，只需补浏览器证明 |
| ❌ 新建第二套 Lineage | `episodes/graph.py` + `lineage.py` 已有正反向查询 |
| ❌ 重写 LeRobot Writer | `export_writer.py` 已通过官方 Loader 元数据验证 |
| ❌ 把 METADATA_ONLY 直接改名 | 必须新增独立 FRAME_LEVEL 状态，不覆盖旧证据 |

---

## 四、复用率评估

| V5.1 任务组 | 可复用比例 | 说明 |
|---|---|---|
| 治理缺口（V51-010/013/017） | ~60% | E2E 框架、权限矩阵、QC 面板均已有；主要补交互与断言 |
| 数据物化（V51-001~005/007） | ~55% | 阶段框架、底层 SDK、Canonical 类型、同步算法均可复用；**核心新增是 payload 生产者与存储** |
| Cleaning（V51-006） | ~30% | 有阶段骨架与质量引擎，规则体系需新建 |
| 训练就绪（V51-011/012/024） | ~70% | Writer/Gate/Lineage 齐备；补逐帧验证与许可校验 |
| 运维指标（V51-023） | ~50% | Ops 端点可扩展 |

**综合：约 55% 可复用，无需重建基础框架。V5.1 的实质工作是「往已有阶段里填真实 payload 生产者」，而非搭新架子。**
