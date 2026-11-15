# DDP 数据架构与 Canonical Schema 演进规范

## 1. 目标

建立与数据源和机器人型号解耦的 DDP 内部数据模型。LeRobot、MCAP、ROS bag、HDF5、MP4 等均是输入或交换格式，不是 DDP 内部唯一数据模型。

## 2. 数据分层

```text
Raw
  原始文件、原始字段、原始时间戳、来源 Repo / 设备 / 任务
    ↓
Canonical
  统一实体、时间语义、传感器、坐标系、Episode Schema
    ↓
Processed
  解析、同步、切片、清洗、质量预检的衍生产物
    ↓
Labeled
  标签 Schema、标注结果、审核和质量结论
    ↓
Dataset
  样本清单、版本、划分、Manifest、Hash、冻结
    ↓
Training / Evaluation
  训练引用、模型版本、评测结果和反馈
```

Raw 不可被处理结果覆盖。任何衍生数据必须能追溯输入资产、处理版本和参数。

## 3. 核心实体建议

| 实体 | 职责 | 关键关联 |
|---|---|---|
| DataSource | 外部数据源和 Repo | ImportJob |
| DatasetSource | 数据集 Repo、Revision、License、Split | Episode |
| ImportJob | 导入过程和状态 | DataSource / Asset |
| RawAsset | 原始文件和 Hash | Source / Project / Device |
| MediaAsset | 视频、图片、音频、深度等媒体资源 | Episode / Stream |
| Robot | 机器人类型和实例 | Device / CollectionTask |
| Device | 设备实例 | Sensor / Calibration |
| Sensor | 传感器元数据和采样信息 | Stream |
| Session | 一次采集或数据会话 | Episode |
| Episode | 任务级样本单元 | Frame / Stream / Annotation |
| Frame | 帧索引和时间戳 | Episode / Stream |
| Observation | 观测数据 | Sensor / Frame |
| Action | 动作指令或动作数据 | Episode / Timestamp |
| CalibrationRecord | 标定参数和版本 | Device / Sensor |
| Annotation | 标签和时间片段 | Episode / Schema |
| LabelSchema | 标签定义和版本 | Annotation |
| QualityRule | 质量规则和版本 | QualityRun |
| QualityRun | 规则执行记录 | Episode / Dataset |
| QualityIssue | 问题、严重度和处理状态 | QualityRun / Review |
| Dataset | 逻辑数据集 | DatasetVersion |
| DatasetVersion | 冻结版本 | Manifest / DatasetItem |
| DatasetItem | 数据集样本清单 | Episode / Asset |
| DataLineage | 来源和衍生关系 | Raw / Processed / Dataset |
| TrainingRequest | 训练数据需求 | DatasetVersion |
| ExportJob | 导出任务 | DatasetVersion |
| ModelVersion | 模型版本引用 | EvaluationResult |
| EvaluationResult | 模型评测结果 | DatasetVersion / ModelVersion |

字段最终以现有 ORM、数据库规范和迁移历史为准，不得为了匹配此建议而破坏已有表结构。

## 4. Canonical 核心字段原则

### 4.1 来源字段
- source_provider；
- source_repo_id / source_uri；
- source_revision / commit；
- source_license；
- source_config / split；
- source_record_id；
- imported_at；
- original_metadata（保留原始字段）。

### 4.2 时间字段
- original_timestamp；
- normalized_timestamp；
- timestamp_unit；
- clock_source；
- timezone（适用时）；
- synchronization_group；
- conversion_metadata。

不得只保留一个无法解释来源的 timestamp。

### 4.3 媒体字段
- asset_type；
- media_format；
- camera_name / stream_name；
- duration；
- fps；
- frame_count；
- width / height；
- size_bytes；
- checksum；
- storage_reference。

### 4.4 Episode 字段
- episode_id；
- source_id；
- episode_index；
- task_index；
- instruction；
- start_time / end_time；
- duration；
- frame_count；
- robot/device/sensor references；
- schema_version；
- processing_version。

### 4.5 标注字段
- annotation_type；
- schema_id / schema_version；
- start_time / end_time；
- payload；
- source_type（dataset / parser / model / human）；
- model_reference（如适用）；
- confidence（仅模型结果适用）；
- review_status；
- reviewer / reviewed_at。

## 5. Adapter 规范

### 5.1 统一接口职责
每个 Adapter 负责：
- 探测格式和版本；
- 读取数据集元信息；
- 枚举样本 / Episode；
- 读取原始字段；
- 映射 Canonical Schema；
- 声明支持字段和缺失字段；
- 产出解析警告和错误；
- 保留来源、版本和原始字段。

### 5.2 Adapter 优先顺序
1. 现有 DDP Adapter；
2. Hugging Face 官方 SDK / datasets；
3. LeRobot 官方 SDK；
4. MCAP / ROS 官方工具与成熟 SDK；
5. h5py / PyArrow 等成熟生态；
6. 仅针对 DDP 业务差异开发轻量 Adapter。

### 5.3 首批 Adapter
- Generic MP4 + CSV / JSON / JSONL；
- Hugging Face Datasets Video；
- LeRobot v2.1；
- LeRobot v3.0。

后续按真实数据验证增加：
- MCAP；
- ROS bag / rosbag2；
- HDF5；
- Parquet；
- 厂商自定义数据格式。

### 5.4 禁止事项
- 不得在业务页面写死某个 Dataset Repo 的字段；
- 不得假设每个数据集都有左右手、双视角、Depth、Pose 或 Action；
- 缺失字段不得伪装成 0 或空动作；
- 不得把转换后的值覆盖原始数据；
- 不得在没有真实样本测试时宣称 Adapter 已支持某格式。

## 6. Dataset Version 与血缘

冻结版本至少记录：
- 样本清单和样本版本；
- Canonical Schema 版本；
- Label Schema 版本；
- Quality Rule 版本；
- Processing Pipeline 版本和参数；
- Train / Validation / Test 划分策略；
- Manifest；
- Hash；
- 创建人和时间；
- 来源授权与 License；
- 导出格式和版本。

数据血缘应支持：
Model Evaluation
← Training Run Reference
← Dataset Version
← Episode
← Annotation Version
← Processing Pipeline Version
← Raw Asset
← Collection Task / Dataset Source
← Project / Robot / Device / Worker。

## 7. 数据质量和泄漏检查
- 文件完整性、Hash、格式和解析；
- 时间戳范围和时间同步；
- FPS、帧数、丢帧和持续时间；
- 传感器通道、单位和坐标系；
- Episode 边界和重复；
- 标注边界和 Schema 一致性；
- Dataset 去重；
- 同一连续 Episode、相似场景或高度相似样本造成的 Train/Test 泄漏；
- 质量规则和报告版本化。

## 8. Schema 演进
- Schema 必须带版本；
- 兼容变更优先；
- 破坏性变更需 ADR、迁移方案和回滚方案；
- 新旧 Schema 可在过渡期并存；
- Migration 必须可审查、可测试；
- 原始数据和历史 Dataset Version 不得因 Schema 升级而静默改变。
