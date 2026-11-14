# DDP 数据治理与 Hugging Face MVP 规范

## 1. 当前验证目标

当前阶段优先用 Hugging Face 上许可和访问条件明确的公开第一人称数据集，验证真实数据链路：

`数据集登记 → 小样本导入 / Streaming → 文件校验 → 格式解析 → Episode / 视频审看 → 人工审核 → 质量报告 → Dataset Version / 导出`

不默认全量下载大型数据集，不默认公开数据允许商业使用。

## 2. 数据源登记

每个 Hugging Face 数据源至少记录：
- Repo ID；
- Revision / Commit；
- Config；
- Split；
- Dataset Card 地址或来源；
- License；
- 是否 Gated；
- 访问条件；
- 导入时间；
- 导入范围；
- 缓存位置；
- 导入工具与版本；
- 导入结果和错误信息。

每次导入前重新核对 Dataset Card、License 和访问条件。不得把访问成功等同于拥有商业使用权。

## 3. Adapter 原则

按现有系统结构实现或复用 Adapter，避免将单一数据集字段写死在核心业务逻辑中。

候选技术：
- `huggingface_hub`：Hub 元信息、文件和仓库访问；
- `datasets`：Dataset 加载与 Streaming；
- LeRobot 官方工具：LeRobot 数据格式；
- FFmpeg / OpenCV：媒体处理；
- PyArrow：Parquet / Arrow；
- h5py：HDF5；
- ROS 官方工具 / MCAP SDK：对应机器人记录格式。

是否引入依赖必须先检查现有项目和兼容性。

## 4. 统一数据语义

建议逻辑层级：

```text
DataSource
  └── Dataset / CollectionProject
        └── Task / CollectionTask
              └── Session
                    └── Episode
                          ├── MediaAsset
                          ├── SensorStream
                          ├── Observation
                          ├── ControlCommand
                          ├── ExecutionState
                          ├── Annotation
                          ├── QualityResult
                          └── TimelineEvent
```

不同来源字段不一致时保留原始元数据，并通过 Adapter 映射；不能凭空补齐不存在的设备、操作员或基地信息。

## 5. 原始与派生数据

- 原始数据只读保存，不原地覆盖。
- 派生文件记录源文件、处理步骤、参数和工具版本。
- 文件记录 URI、大小、MIME / 格式、校验和。
- 处理失败保留错误和任务状态。
- 同一数据重复导入应有去重或幂等策略。

## 6. 时序与动作语义

时序数据必须明确：
- 时间戳单位；
- 时钟来源；
- 是否已同步；
- 采样频率；
- 缺失值和异常值处理。

动作数据必须明确：
- 数据类型；
- 维度；
- 单位；
- 坐标系；
- 关节顺序；
- 夹爪语义；
- 采样频率；
- 机器人本体 / 版本。

严格区分 Observation、Control Command、Execution State、Model Action 和 Safety-adjusted Action。

## 7. 标注和质量

模型预测、人工修订和最终审核结论必须分开保存。自动标注的置信度不能直接替代人工验收标准。质量规则应版本化，报告需说明样本范围、规则版本、通过率和失败原因。

## 8. Dataset Version

Dataset Version 应记录：
- 纳入样本清单；
- 来源与 Revision；
- 筛选条件；
- Split；
- Schema 版本；
- 标注和质量状态；
- 生成时间；
- 导出格式；
- Manifest 校验信息。

训练、验证、测试集应避免 Episode / Session / 重复片段泄漏。
