# 06｜SensorStream / Observation / Action 真实物化设计

## 1. 目标
把“类型定义存在”变成“真实 payload 可以被逐帧解析、持久化、查询、导出和 Loader 读取”。

## 2. 三类数据定义
**SensorStream：** 某个传感器或数据源产生的连续数据流元信息与存储引用。  
**Observation：** 某个时间点或采样点的环境/机器人观测，保留 modality、payload、timestamp、unit、coordinate frame 和 source reference。  
**Action：** 来自真实动作/控制源的动作记录；若数据集没有动作源，不得用视频描述伪造 Action。

## 3. SensorStream 必需字段
stream_id、episode_id、source_asset_id、sensor_id/device_id、modality、stream_name、timestamp_source、timestamp_unit、time_base_id、sampling_rate（可空但说明原因）、dtype、shape、unit、coordinate_frame、calibration_ref/version、payload_location、sample_count、start/end timestamp、missing_count、invalid_count、parser_version、schema_version。

## 4. Observation 必需字段
observation_id、episode_id、stream_id、timestamp_raw、timestamp_normalized、time_base_id、modality、feature_name、value reference 或 payload、dtype、shape、unit、coordinate_frame、validity、source_index/source_offset、calibration_ref、transform provenance。
高频大数组优先列式或二进制对象存储；数据库保存索引、分区范围、引用和校验信息，避免把大数组逐条塞入 OLTP 表。

## 5. Action 必需字段
action_id、episode_id、timestamp_raw、timestamp_normalized、time_base_id、action_type、value/payload、dtype、shape、unit、coordinate_frame、source_channel、source_asset_id、source_index、validity、semantics_version。
动作来源要能回答：控制指令、关节目标、末端位姿、遥操作输入，还是数据集定义的 action label。不同来源不可混为同一语义。

## 6. 解析流程
读取 Manifest/RawAsset → 校验 Hash/格式/版本 → Adapter inspect → 解析 stream inventory → 读取真实 payload → 规范化 dtype/shape/unit → 映射时间域 → 关联设备/传感器/标定 → 输出 SensorStream → 物化 Observation/Action → 写 Artifact 和 ProcessingRun lineage → 抽样读取并与源文件对比 → 产出 Parse Report。

## 7. 缺失数据规则
- Action 不存在：`ACTION_SOURCE_ABSENT`，不能造零值冒充真实动作。
- Calibration 不存在：保留原始坐标系，标记 `CALIBRATION_MISSING`，不得宣称转换后坐标。
- Unit 未知：`UNIT_UNKNOWN`，不自动猜测单位。
- Timestamp 无法映射：`TIMEBASE_UNRESOLVED`，不混入已同步流。
- Shape 不一致：标记异常或隔离，不截断后静默通过。

## 8. 存储策略
RawAsset 永久不可变。解析结果作为版本化 Artifact。大数组/高频数据优先列式或二进制对象存储；PostgreSQL 保存查询索引、范围、引用和状态。每个 Artifact 有 Hash、大小、格式、Schema Version、Parser Version。

## 9. 查询与预览
Episode 详情显示每条 Stream 的采样率、时间范围、样本数、缺失数、单位、坐标系、标定版本；支持按时间范围抽样读取 Observation/Action；显示源文件偏移和解析版本。

## 10. 验收
至少一种真实机器人格式输出非空 SensorStream；真实 Observation payload 可逐帧/逐采样读取；有动作源时 Action 与源数据一致，无动作源时状态诚实；时间戳、单位、shape、dtype 与 fixture 一致；RawAsset 未修改；重跑不重复创建相同产物。
