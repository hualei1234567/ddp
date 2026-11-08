# ADR-0003 数据分层与 Canonical Schema

- 状态：已提议（待用户批准）
- 日期：2026-11-08
- 关联：规范 §6

## 背景

DDP 要接入 MCAP、ROS bag、HDF5、MP4、Parquet、LeRobot 等多种格式，并保证原始数据不可被覆盖、
训练数据可复现。直接在原始格式上做标注/训练会导致不可追溯。

## 决策

固定六层数据：

```text
Raw → Canonical → Processed → Labeled → Dataset → Training/Evaluation
```

- **Raw**：原始文件、原始时间戳、原始元数据；只读，不覆盖。
- **Canonical**：统一实体、时间、坐标系、单位、传感器、Episode Schema；版本化。
- **Processed**：清洗、同步、切片、校验后的产物。
- **Labeled**：标注结果、标签版本、审核结论。
- **Dataset**：数据集与版本、划分、Manifest、Hash。
- **Training/Evaluation**：训练引用、模型版本、评测结果。

LeRobot 是训练交换格式之一，不是 DDP 内部唯一数据模型。

## 后果

- 任何处理都必须记录输入版本、Pipeline 版本、参数与产物引用。
- 数据集冻结后不可静默修改；变更创建新版本。
- 血缘可以从模型评测反向追溯到原始资产与采集任务。

## 替代方案

- 直接以 LeRobot 为内部模型：会丢失原始时间/标定/多厂商差异，否决。
